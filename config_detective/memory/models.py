"""Pydantic models for Memory RAG.

This module defines the data structures for:
- CaseRecord: Individual investigation cases (episodic memory)
- PatternFingerprint: Compressed patterns learned from cases (semantic memory)

These models handle serialization to/from Supabase and provide
validation for all memory operations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class DeltaSummary(BaseModel):
    """Compact representation of a delta for storage.

    This is a simplified version of the full Delta dataclass,
    containing only the fields needed for memory retrieval.
    """

    node_id: str
    node_type: str
    delta_type: str
    name: str
    value_a: str | None = None
    value_b: str | None = None
    suspect_score: float = 0.5
    category: str | None = None


class CaseRecord(BaseModel):
    """A single investigation case stored in episodic memory.

    Each case represents one completed investigation, storing enough
    context to be useful for future similar cases.

    Attributes:
        id: Unique case identifier
        snapshot_a_hash: Hash of the "working" environment snapshot
        snapshot_b_hash: Hash of the "failing" environment snapshot
        failure_signature: The error message/trace that triggered investigation
        failure_embedding: 384-dim vector from BGE embedding (stored as list)
        root_cause_node_id: Graph node ID of the root cause (e.g., "env:LANG")
        root_cause_category: Bug category (locale, ssl, timezone, etc.)
        fix_text: The actual fix applied
        fix_verified: Whether sandbox verification passed
        confidence: Confidence score (0.0-1.0)
        delta_summary: List of relevant deltas for context
        created_at: When this case was recorded
    """

    id: UUID = Field(default_factory=uuid4)
    snapshot_a_hash: str
    snapshot_b_hash: str
    failure_signature: str
    failure_embedding: list[float] | None = Field(
        default=None,
        description="384-dimensional BGE embedding vector",
    )
    root_cause_node_id: str
    root_cause_category: str | None = None
    fix_text: str
    fix_verified: bool = False
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    delta_summary: list[DeltaSummary] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def to_supabase_row(self) -> dict[str, Any]:
        """Convert to a dict suitable for Supabase insert.

        Supabase expects specific formats for certain types:
        - UUID as string
        - datetime as ISO string
        - embedding as list (Supabase pgvector handles conversion)
        """
        return {
            "id": str(self.id),
            "snapshot_a_hash": self.snapshot_a_hash,
            "snapshot_b_hash": self.snapshot_b_hash,
            "failure_signature": self.failure_signature,
            "failure_embedding": self.failure_embedding,
            "root_cause_node_id": self.root_cause_node_id,
            "root_cause_category": self.root_cause_category,
            "fix_text": self.fix_text,
            "fix_verified": self.fix_verified,
            "confidence": self.confidence,
            "delta_summary": [d.model_dump() for d in self.delta_summary],
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_supabase_row(cls, row: dict[str, Any]) -> "CaseRecord":
        """Create a CaseRecord from a Supabase row."""
        delta_summary = [
            DeltaSummary.model_validate(d) for d in (row.get("delta_summary") or [])
        ]

        return cls(
            id=UUID(row["id"]) if isinstance(row["id"], str) else row["id"],
            snapshot_a_hash=row["snapshot_a_hash"],
            snapshot_b_hash=row["snapshot_b_hash"],
            failure_signature=row["failure_signature"],
            failure_embedding=row.get("failure_embedding"),
            root_cause_node_id=row["root_cause_node_id"],
            root_cause_category=row.get("root_cause_category"),
            fix_text=row["fix_text"],
            fix_verified=row.get("fix_verified", False),
            confidence=row.get("confidence", 0.5),
            delta_summary=delta_summary,
            created_at=(
                datetime.fromisoformat(row["created_at"])
                if isinstance(row["created_at"], str)
                else row["created_at"]
            ),
        )

    def similarity_score(self, other_embedding: list[float]) -> float:
        """Compute cosine similarity with another embedding.

        Used when we already have embeddings loaded and want to
        avoid another API call.
        """
        if not self.failure_embedding or not other_embedding:
            return 0.0

        # Cosine similarity
        dot_product = sum(a * b for a, b in zip(self.failure_embedding, other_embedding))
        norm_a = sum(a * a for a in self.failure_embedding) ** 0.5
        norm_b = sum(b * b for b in other_embedding) ** 0.5

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)


class PatternFingerprint(BaseModel):
    """A compressed pattern learned from multiple cases (semantic memory).

    Pattern fingerprints represent higher-level knowledge extracted from
    individual cases. For example, after seeing 10 locale-related bugs,
    the system creates a fingerprint describing the common symptoms,
    typical deltas, and effective fixes.

    Attributes:
        id: Unique pattern identifier
        category: Bug category (locale, ssl, timezone, etc.)
        pattern_description: Natural language description of the pattern
        pattern_embedding: Embedding for similarity matching
        typical_symptoms: Common error message patterns
        typical_deltas: Common delta types seen (e.g., ["env:LANG", "locale:LC_ALL"])
        typical_fixes: Common fix patterns
        case_count: Number of cases that contributed to this pattern
        last_updated: When this pattern was last refined
    """

    id: UUID = Field(default_factory=uuid4)
    category: str
    pattern_description: str
    pattern_embedding: list[float] | None = None
    typical_symptoms: list[str] = Field(default_factory=list)
    typical_deltas: list[str] = Field(default_factory=list)
    typical_fixes: list[str] = Field(default_factory=list)
    case_count: int = Field(default=1, ge=1)
    last_updated: datetime = Field(default_factory=datetime.utcnow)

    def to_supabase_row(self) -> dict[str, Any]:
        """Convert to a dict suitable for Supabase insert/upsert."""
        return {
            "id": str(self.id),
            "category": self.category,
            "pattern_description": self.pattern_description,
            "pattern_embedding": self.pattern_embedding,
            "typical_symptoms": self.typical_symptoms,
            "typical_deltas": self.typical_deltas,
            "typical_fixes": self.typical_fixes,
            "case_count": self.case_count,
            "last_updated": self.last_updated.isoformat(),
        }

    @classmethod
    def from_supabase_row(cls, row: dict[str, Any]) -> "PatternFingerprint":
        """Create a PatternFingerprint from a Supabase row."""
        return cls(
            id=UUID(row["id"]) if isinstance(row["id"], str) else row["id"],
            category=row["category"],
            pattern_description=row["pattern_description"],
            pattern_embedding=row.get("pattern_embedding"),
            typical_symptoms=row.get("typical_symptoms") or [],
            typical_deltas=row.get("typical_deltas") or [],
            typical_fixes=row.get("typical_fixes") or [],
            case_count=row.get("case_count", 1),
            last_updated=(
                datetime.fromisoformat(row["last_updated"])
                if isinstance(row["last_updated"], str)
                else row["last_updated"]
            ),
        )

    def matches_failure(self, failure_text: str) -> bool:
        """Check if any typical symptom matches the failure text."""
        failure_lower = failure_text.lower()
        return any(symptom.lower() in failure_lower for symptom in self.typical_symptoms)


class SimilarCaseResult(BaseModel):
    """Result from a similarity search, including the similarity score."""

    case: CaseRecord
    similarity: float = Field(ge=0.0, le=1.0)

    def __lt__(self, other: "SimilarCaseResult") -> bool:
        """Sort by similarity (descending)."""
        return self.similarity > other.similarity


class MemoryStats(BaseModel):
    """Statistics about the memory store."""

    total_cases: int = 0
    total_patterns: int = 0
    cases_by_category: dict[str, int] = Field(default_factory=dict)
    oldest_case: datetime | None = None
    newest_case: datetime | None = None
    avg_confidence: float = 0.0
    verified_fix_rate: float = 0.0
