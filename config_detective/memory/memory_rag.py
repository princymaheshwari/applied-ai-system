"""Memory RAG - Main API for episodic and semantic memory.

This module provides the primary interface for the Memory RAG system:
- store_case: Save a completed investigation to episodic memory
- retrieve_similar_cases: Find past cases with similar failure signatures
- get_patterns: Get relevant pattern fingerprints for a failure
- get_stats: Get memory statistics

The memory system has two layers:
1. Episodic Memory (cases table): Individual investigation records
2. Semantic Memory (pattern_fingerprints table): Compressed patterns

Usage:
    from config_detective.memory import memory_rag
    
    # Store a completed investigation
    case_id = await memory_rag.store_case(
        snapshot_a_hash="abc123",
        snapshot_b_hash="def456",
        failure_signature="UnicodeDecodeError: 'ascii'",
        root_cause_node_id="env:LANG",
        fix_text="ENV LANG=C.UTF-8",
        deltas=[...],
        confidence=0.92,
    )
    
    # Retrieve similar past cases
    similar = await memory_rag.retrieve_similar_cases(
        "UnicodeDecodeError: 'utf-8' codec",
        top_k=5,
    )
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from .embeddings import embed_text
from .models import (
    CaseRecord,
    DeltaSummary,
    MemoryStats,
    PatternFingerprint,
    SimilarCaseResult,
)
from .supabase_client import get_client, is_available

if TYPE_CHECKING:
    from config_detective.graph.schema import Delta

logger = logging.getLogger(__name__)


# ============================================================================
# Episodic Memory Operations
# ============================================================================


async def store_case(
    snapshot_a_hash: str,
    snapshot_b_hash: str,
    failure_signature: str,
    root_cause_node_id: str,
    fix_text: str,
    deltas: list["Delta"] | None = None,
    root_cause_category: str | None = None,
    fix_verified: bool = False,
    confidence: float = 0.5,
) -> str | None:
    """Store a completed investigation in episodic memory.

    This is called after the agent completes an investigation and
    has identified a root cause with a fix.

    Args:
        snapshot_a_hash: Hash of the "working" environment snapshot
        snapshot_b_hash: Hash of the "failing" environment snapshot
        failure_signature: The error message/trace that triggered investigation
        root_cause_node_id: Graph node ID of the root cause (e.g., "env:LANG")
        fix_text: The actual fix (e.g., "ENV LANG=C.UTF-8")
        deltas: Optional list of Delta objects for context
        root_cause_category: Bug category (locale, ssl, etc.)
        fix_verified: Whether sandbox verification passed
        confidence: Confidence score (0.0-1.0)

    Returns:
        Case ID (UUID as string) if stored successfully, None otherwise
    """
    client = get_client()
    if not client:
        logger.warning("Supabase not available, case not stored")
        return None

    try:
        # Get embedding for failure signature
        embedding = await embed_text(failure_signature)

        # Convert deltas to summary format
        delta_summary = []
        if deltas:
            for d in deltas:
                delta_summary.append(
                    DeltaSummary(
                        node_id=d.node_id,
                        node_type=d.node_type.value if hasattr(d.node_type, "value") else str(d.node_type),
                        delta_type=d.delta_type.value if hasattr(d.delta_type, "value") else str(d.delta_type),
                        name=d.name,
                        value_a=d.value_a,
                        value_b=d.value_b,
                        suspect_score=d.suspect_score,
                        category=d.category,
                    )
                )

        # Create case record
        case = CaseRecord(
            snapshot_a_hash=snapshot_a_hash,
            snapshot_b_hash=snapshot_b_hash,
            failure_signature=failure_signature,
            failure_embedding=embedding,
            root_cause_node_id=root_cause_node_id,
            root_cause_category=root_cause_category,
            fix_text=fix_text,
            fix_verified=fix_verified,
            confidence=confidence,
            delta_summary=delta_summary,
        )

        # Insert into Supabase
        result = client.table("cases").insert(case.to_supabase_row()).execute()

        if result.data:
            case_id = result.data[0]["id"]
            logger.info(f"Stored case {case_id} in episodic memory")
            return case_id
        else:
            logger.error("Failed to store case: no data returned")
            return None

    except Exception as e:
        logger.error(f"Failed to store case: {e}")
        return None


async def retrieve_similar_cases(
    failure_signature: str,
    top_k: int = 5,
    similarity_threshold: float = 0.5,
) -> list[SimilarCaseResult]:
    """Find past cases with similar failure signatures.

    Uses vector similarity search on the failure_embedding column
    to find semantically similar past cases.

    Args:
        failure_signature: The current failure message/trace
        top_k: Maximum number of results to return
        similarity_threshold: Minimum similarity score (0.0-1.0)

    Returns:
        List of SimilarCaseResult, sorted by similarity (highest first)
    """
    client = get_client()
    if not client:
        logger.warning("Supabase not available, returning empty results")
        return []

    try:
        # Get embedding for query
        query_embedding = await embed_text(failure_signature)

        # Call the match_cases function in Supabase
        result = client.rpc(
            "match_cases",
            {
                "query_embedding": query_embedding,
                "match_count": top_k,
                "similarity_threshold": similarity_threshold,
            },
        ).execute()

        if not result.data:
            return []

        # Convert to SimilarCaseResult objects
        results = []
        for row in result.data:
            case = CaseRecord.from_supabase_row(row)
            similarity = row.get("similarity", 0.0)
            results.append(SimilarCaseResult(case=case, similarity=similarity))

        # Sort by similarity (should already be sorted, but ensure it)
        results.sort()
        return results

    except Exception as e:
        logger.error(f"Failed to retrieve similar cases: {e}")
        return []


async def get_case_by_id(case_id: str | UUID) -> CaseRecord | None:
    """Retrieve a specific case by ID.

    Args:
        case_id: UUID of the case (string or UUID object)

    Returns:
        CaseRecord if found, None otherwise
    """
    client = get_client()
    if not client:
        return None

    try:
        case_id_str = str(case_id)
        result = client.table("cases").select("*").eq("id", case_id_str).execute()

        if result.data:
            return CaseRecord.from_supabase_row(result.data[0])
        return None

    except Exception as e:
        logger.error(f"Failed to get case {case_id}: {e}")
        return None


async def get_cases_since(
    days: int = 7,
    category: str | None = None,
) -> list[CaseRecord]:
    """Get recent cases for reflection processing.

    Args:
        days: Number of days to look back
        category: Optional category filter

    Returns:
        List of CaseRecord objects
    """
    client = get_client()
    if not client:
        return []

    try:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        query = client.table("cases").select("*").gte("created_at", cutoff)

        if category:
            query = query.eq("root_cause_category", category)

        result = query.order("created_at", desc=True).execute()

        return [CaseRecord.from_supabase_row(row) for row in (result.data or [])]

    except Exception as e:
        logger.error(f"Failed to get recent cases: {e}")
        return []


# ============================================================================
# Semantic Memory Operations
# ============================================================================


async def get_patterns(
    failure_signature: str | None = None,
    category: str | None = None,
    top_k: int = 3,
) -> list[PatternFingerprint]:
    """Get relevant pattern fingerprints.

    Patterns can be retrieved by:
    - Semantic similarity to a failure signature
    - Exact category match
    - Or both (combined filtering)

    Args:
        failure_signature: Optional failure text for similarity search
        category: Optional category filter
        top_k: Maximum patterns to return

    Returns:
        List of PatternFingerprint objects
    """
    client = get_client()
    if not client:
        return []

    try:
        # If we have a failure signature, use vector similarity
        if failure_signature:
            query_embedding = await embed_text(failure_signature)
            result = client.rpc(
                "match_patterns",
                {
                    "query_embedding": query_embedding,
                    "match_count": top_k,
                    "similarity_threshold": 0.4,
                },
            ).execute()

            patterns = [
                PatternFingerprint.from_supabase_row(row) for row in (result.data or [])
            ]

            # Filter by category if specified
            if category:
                patterns = [p for p in patterns if p.category == category]

            return patterns[:top_k]

        # Otherwise, just filter by category
        elif category:
            result = (
                client.table("pattern_fingerprints")
                .select("*")
                .eq("category", category)
                .limit(top_k)
                .execute()
            )
            return [
                PatternFingerprint.from_supabase_row(row) for row in (result.data or [])
            ]

        # No filters - return top patterns by case count
        else:
            result = (
                client.table("pattern_fingerprints")
                .select("*")
                .order("case_count", desc=True)
                .limit(top_k)
                .execute()
            )
            return [
                PatternFingerprint.from_supabase_row(row) for row in (result.data or [])
            ]

    except Exception as e:
        logger.error(f"Failed to get patterns: {e}")
        return []


async def upsert_pattern(pattern: PatternFingerprint) -> bool:
    """Insert or update a pattern fingerprint.

    Used by the reflection agent to create/update patterns.

    Args:
        pattern: The pattern to upsert

    Returns:
        True if successful, False otherwise
    """
    client = get_client()
    if not client:
        return False

    try:
        # Get embedding for pattern description
        if not pattern.pattern_embedding:
            pattern.pattern_embedding = await embed_text(pattern.pattern_description)

        # Upsert based on category (unique constraint)
        result = (
            client.table("pattern_fingerprints")
            .upsert(pattern.to_supabase_row(), on_conflict="category")
            .execute()
        )

        return bool(result.data)

    except Exception as e:
        logger.error(f"Failed to upsert pattern: {e}")
        return False


async def get_all_patterns() -> list[PatternFingerprint]:
    """Get all pattern fingerprints.

    Returns:
        List of all PatternFingerprint objects
    """
    client = get_client()
    if not client:
        return []

    try:
        result = (
            client.table("pattern_fingerprints")
            .select("*")
            .order("case_count", desc=True)
            .execute()
        )
        return [
            PatternFingerprint.from_supabase_row(row) for row in (result.data or [])
        ]

    except Exception as e:
        logger.error(f"Failed to get all patterns: {e}")
        return []


# ============================================================================
# Statistics and Health
# ============================================================================


async def get_stats() -> MemoryStats:
    """Get statistics about the memory store.

    Returns:
        MemoryStats object with counts, averages, etc.
    """
    client = get_client()
    if not client:
        return MemoryStats()

    try:
        # Get case counts
        cases_result = client.table("cases").select("*", count="exact").execute()
        total_cases = cases_result.count or 0

        # Get pattern count
        patterns_result = (
            client.table("pattern_fingerprints").select("*", count="exact").execute()
        )
        total_patterns = patterns_result.count or 0

        # Get cases by category
        category_result = (
            client.table("cases")
            .select("root_cause_category")
            .execute()
        )
        cases_by_category: dict[str, int] = {}
        for row in category_result.data or []:
            cat = row.get("root_cause_category") or "unknown"
            cases_by_category[cat] = cases_by_category.get(cat, 0) + 1

        # Get date range
        oldest_result = (
            client.table("cases")
            .select("created_at")
            .order("created_at", desc=False)
            .limit(1)
            .execute()
        )
        newest_result = (
            client.table("cases")
            .select("created_at")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        oldest_case = None
        newest_case = None
        if oldest_result.data:
            oldest_case = datetime.fromisoformat(oldest_result.data[0]["created_at"])
        if newest_result.data:
            newest_case = datetime.fromisoformat(newest_result.data[0]["created_at"])

        # Get average confidence and verified rate
        confidence_result = (
            client.table("cases").select("confidence, fix_verified").execute()
        )
        if confidence_result.data:
            confidences = [r.get("confidence", 0.5) for r in confidence_result.data]
            verified = [r.get("fix_verified", False) for r in confidence_result.data]
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            verified_rate = sum(1 for v in verified if v) / len(verified) if verified else 0.0
        else:
            avg_confidence = 0.0
            verified_rate = 0.0

        return MemoryStats(
            total_cases=total_cases,
            total_patterns=total_patterns,
            cases_by_category=cases_by_category,
            oldest_case=oldest_case,
            newest_case=newest_case,
            avg_confidence=avg_confidence,
            verified_fix_rate=verified_rate,
        )

    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        return MemoryStats()


def memory_available() -> bool:
    """Check if the memory system is available.

    Returns True if Supabase is configured and connected.
    """
    return is_available()
