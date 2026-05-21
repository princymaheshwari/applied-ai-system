"""Reflection Agent for Pattern Compression.

This module implements the reflection agent that periodically analyzes
recent cases and compresses them into semantic pattern fingerprints.

The reflection process:
1. Fetch recent cases (e.g., last 7 days)
2. Group cases by root_cause_category
3. For categories with 3+ cases, synthesize a pattern
4. Upsert the pattern into the pattern_fingerprints table

Patterns capture higher-level knowledge like:
- "Locale bugs typically present as UnicodeDecodeError + LANG delta"
- "OpenSSL issues often involve version mismatch between cryptography and libssl"

Usage:
    from config_detective.memory.reflection import run_reflection
    
    # Run reflection on recent cases
    await run_reflection(days=7, min_cases=3)
    
    # Or use the scheduled runner
    await start_reflection_scheduler(interval_hours=24)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from .embeddings import embed_text
from .memory_rag import get_cases_since, upsert_pattern
from .models import CaseRecord, PatternFingerprint

logger = logging.getLogger(__name__)

# Minimum cases needed to form a pattern
DEFAULT_MIN_CASES = 3

# Template for synthesizing pattern descriptions
PATTERN_TEMPLATE = """Pattern: {category}

Based on {case_count} similar cases:
- Typical error signatures: {symptoms}
- Common configuration deltas: {deltas}
- Effective fixes: {fixes}
- Average confidence: {avg_confidence:.0%}
- Verified fix rate: {verified_rate:.0%}
"""


async def run_reflection(
    days: int = 7,
    min_cases: int = DEFAULT_MIN_CASES,
) -> dict[str, int]:
    """Run reflection to compress recent cases into patterns.

    This is the main entry point for the reflection agent.
    Call this periodically (e.g., daily) to keep patterns up to date.

    Args:
        days: Number of days to look back for cases
        min_cases: Minimum cases in a category to create a pattern

    Returns:
        Dict mapping category to number of cases processed
    """
    logger.info(f"Starting reflection over last {days} days (min_cases={min_cases})")

    # Get recent cases
    cases = await get_cases_since(days=days)
    if not cases:
        logger.info("No recent cases found for reflection")
        return {}

    # Group by category
    by_category = _group_by_category(cases)
    results: dict[str, int] = {}

    # Process each category with enough cases
    for category, category_cases in by_category.items():
        if len(category_cases) >= min_cases:
            pattern = await synthesize_pattern(category, category_cases)
            if pattern:
                success = await upsert_pattern(pattern)
                if success:
                    logger.info(
                        f"Updated pattern for '{category}' from {len(category_cases)} cases"
                    )
                    results[category] = len(category_cases)
                else:
                    logger.error(f"Failed to upsert pattern for '{category}'")
        else:
            logger.debug(
                f"Skipping '{category}' - only {len(category_cases)} cases "
                f"(need {min_cases})"
            )

    return results


def _group_by_category(cases: list[CaseRecord]) -> dict[str, list[CaseRecord]]:
    """Group cases by their root_cause_category."""
    by_category: dict[str, list[CaseRecord]] = defaultdict(list)
    for case in cases:
        category = case.root_cause_category or "unknown"
        by_category[category].append(case)
    return dict(by_category)


async def synthesize_pattern(
    category: str,
    cases: list[CaseRecord],
) -> PatternFingerprint | None:
    """Synthesize a pattern fingerprint from multiple cases.

    Analyzes the cases to extract:
    - Typical symptoms (error message patterns)
    - Common deltas (configuration items that differ)
    - Effective fixes

    Args:
        category: The bug category (locale, ssl, etc.)
        cases: List of cases to analyze

    Returns:
        PatternFingerprint or None if synthesis fails
    """
    if not cases:
        return None

    # Extract symptoms (unique failure signatures, simplified)
    symptoms = _extract_symptoms(cases)

    # Extract typical deltas
    deltas = _extract_typical_deltas(cases)

    # Extract typical fixes
    fixes = _extract_typical_fixes(cases)

    # Calculate statistics
    avg_confidence = sum(c.confidence for c in cases) / len(cases)
    verified_count = sum(1 for c in cases if c.fix_verified)
    verified_rate = verified_count / len(cases)

    # Generate pattern description
    description = PATTERN_TEMPLATE.format(
        category=category,
        case_count=len(cases),
        symptoms=", ".join(symptoms[:3]) or "various errors",
        deltas=", ".join(deltas[:5]) or "multiple config items",
        fixes=", ".join(fixes[:3]) or "various fixes",
        avg_confidence=avg_confidence,
        verified_rate=verified_rate,
    )

    # Get embedding for the description
    embedding = await embed_text(description)

    return PatternFingerprint(
        category=category,
        pattern_description=description,
        pattern_embedding=embedding,
        typical_symptoms=symptoms,
        typical_deltas=deltas,
        typical_fixes=fixes,
        case_count=len(cases),
        last_updated=datetime.utcnow(),
    )


def _extract_symptoms(cases: list[CaseRecord]) -> list[str]:
    """Extract unique symptom patterns from failure signatures.

    Simplifies failure signatures to core error patterns.
    """
    symptoms: list[str] = []
    seen: set[str] = set()

    for case in cases:
        # Extract the first line or key error type
        signature = case.failure_signature.strip()
        first_line = signature.split("\n")[0][:100]

        # Simplify common patterns
        simplified = _simplify_error(first_line)

        if simplified not in seen:
            seen.add(simplified)
            symptoms.append(simplified)

    return symptoms[:10]  # Limit to 10 unique symptoms


def _simplify_error(error: str) -> str:
    """Simplify an error message to its core pattern.

    Removes file paths, line numbers, and specific values
    to get the error type pattern.
    """
    import re

    # Remove file paths
    error = re.sub(r'[\'"][^"\']*[/\\][^\'"]*[\'"]', '"<path>"', error)
    error = re.sub(r"File ['\"][^'\"]+['\"]", "File <path>", error)

    # Remove line numbers
    error = re.sub(r"line \d+", "line N", error)

    # Remove specific hex/memory addresses
    error = re.sub(r"0x[0-9a-fA-F]+", "0x...", error)

    # Remove specific version numbers in some contexts
    error = re.sub(r"\d+\.\d+\.\d+", "X.Y.Z", error)

    return error.strip()


def _extract_typical_deltas(cases: list[CaseRecord]) -> list[str]:
    """Extract the most common delta types from cases."""
    delta_counts: dict[str, int] = defaultdict(int)

    for case in cases:
        # Count root cause node
        delta_counts[case.root_cause_node_id] += 2  # Weight root cause higher

        # Count deltas from summary
        for delta in case.delta_summary:
            delta_counts[delta.node_id] += 1

    # Sort by count and return top deltas
    sorted_deltas = sorted(delta_counts.items(), key=lambda x: x[1], reverse=True)
    return [d[0] for d in sorted_deltas[:10]]


def _extract_typical_fixes(cases: list[CaseRecord]) -> list[str]:
    """Extract unique fix patterns from cases."""
    fixes: list[str] = []
    seen: set[str] = set()

    # Prioritize verified fixes
    for case in sorted(cases, key=lambda c: c.fix_verified, reverse=True):
        fix = case.fix_text.strip()
        simplified = _simplify_fix(fix)

        if simplified not in seen:
            seen.add(simplified)
            fixes.append(fix[:200])  # Truncate long fixes

    return fixes[:10]


def _simplify_fix(fix: str) -> str:
    """Simplify a fix for deduplication."""
    import re

    # Normalize whitespace
    fix = " ".join(fix.split())

    # Remove specific version numbers
    fix = re.sub(r"==\d+\.\d+\.\d+", "==X.Y.Z", fix)
    fix = re.sub(r">=\d+\.\d+\.\d+", ">=X.Y.Z", fix)

    return fix.lower()[:100]


async def get_reflection_status() -> dict[str, int | str]:
    """Get the status of the reflection system.

    Returns:
        Dict with counts and last update times
    """
    from .memory_rag import get_all_patterns, get_stats

    stats = await get_stats()
    patterns = await get_all_patterns()

    # Find most recent pattern update
    last_updated = None
    if patterns:
        last_updated = max(p.last_updated for p in patterns)

    return {
        "total_cases": stats.total_cases,
        "total_patterns": stats.total_patterns,
        "categories_with_patterns": len(patterns),
        "last_reflection": last_updated.isoformat() if last_updated else "never",
    }


# ============================================================================
# Scheduled Reflection (for production use)
# ============================================================================


class ReflectionScheduler:
    """Simple scheduler for periodic reflection runs.

    In production, you might use a proper scheduler like APScheduler
    or run this as a cron job. This provides a basic async loop.
    """

    def __init__(self, interval_hours: int = 24):
        self.interval_hours = interval_hours
        self._running = False

    async def start(self) -> None:
        """Start the reflection scheduler loop."""
        import asyncio

        self._running = True
        logger.info(f"Starting reflection scheduler (interval={self.interval_hours}h)")

        while self._running:
            try:
                await run_reflection()
            except Exception as e:
                logger.error(f"Reflection run failed: {e}")

            # Wait for next run
            await asyncio.sleep(self.interval_hours * 3600)

    def stop(self) -> None:
        """Stop the reflection scheduler."""
        self._running = False
        logger.info("Reflection scheduler stopped")


# Global scheduler instance (for convenience)
_scheduler: ReflectionScheduler | None = None


async def start_reflection_scheduler(interval_hours: int = 24) -> ReflectionScheduler:
    """Start a background reflection scheduler.

    Args:
        interval_hours: Hours between reflection runs

    Returns:
        The scheduler instance
    """
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()

    _scheduler = ReflectionScheduler(interval_hours)
    # Note: caller should await this in a background task
    return _scheduler


def stop_reflection_scheduler() -> None:
    """Stop the background reflection scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        _scheduler = None
