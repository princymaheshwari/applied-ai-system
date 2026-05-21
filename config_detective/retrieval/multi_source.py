"""Multi-source retrieval orchestrator.

This module provides the main entry point for searching all external
sources simultaneously and combining the results.

Features:
- Fan-out: Query all sources in parallel
- Deduplication: Remove near-duplicate results
- Relevance reranking: Combine scores and resort
- Statistics: Track what was searched and found

Usage:
    from config_detective.retrieval import search_all_sources

    results = await search_all_sources(
        failure_signature="UnicodeDecodeError: 'ascii' codec",
        delta_items=["env:LANG", "locale:LC_ALL"],
        package_names=["requests"],
        top_k=15,
    )
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import TYPE_CHECKING

from .github_search import search_github_issues
from .models import (
    EvidenceSource,
    ExternalEvidence,
    RetrievalStats,
    SearchQuery,
)
from .osv import lookup_vulnerabilities
from .stackoverflow import search_stackoverflow
from .utils import (
    compute_text_similarity,
    deduplicate_results,
    extract_error_type,
    extract_package_from_delta,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Deduplication threshold
DEDUP_SIMILARITY_THRESHOLD = 0.75

# Default max results per source
DEFAULT_MAX_PER_SOURCE = 5


async def search_all_sources(
    failure_signature: str,
    delta_items: list[str] | None = None,
    package_names: list[str] | None = None,
    package_versions: dict[str, str] | None = None,
    top_k: int = 15,
    max_per_source: int = DEFAULT_MAX_PER_SOURCE,
    include_sources: list[EvidenceSource] | None = None,
    use_cache: bool = True,
) -> list[ExternalEvidence]:
    """Search all external sources for relevant evidence.

    This is the main entry point for multi-source retrieval.
    Queries are dispatched in parallel to all sources.

    Args:
        failure_signature: The error message/trace to search for
        delta_items: Delta node IDs (e.g., ["env:LANG", "py_pkg:requests"])
        package_names: Package names for targeted searches
        package_versions: Package name -> version mapping for CVE lookup
        top_k: Total results to return across all sources
        max_per_source: Maximum results from each source
        include_sources: Which sources to query (defaults to all)
        use_cache: Whether to use cached results

    Returns:
        List of ExternalEvidence, sorted by relevance
    """
    start_time = time.time()

    # Build search query
    query = SearchQuery(
        failure_signature=failure_signature,
        delta_items=delta_items or [],
        package_names=package_names or [],
        package_versions=package_versions or {},
        max_results_per_source=max_per_source,
        include_sources=include_sources or list(EvidenceSource),
    )

    # Extract package names from deltas if not provided
    if not query.package_names and query.delta_items:
        query.package_names = _extract_packages_from_deltas(query.delta_items)

    # Fan-out: query all sources in parallel
    results = await _fanout_search(query, use_cache)

    # Deduplicate results
    deduped = _deduplicate_results(results)

    # Rerank combined results
    reranked = _rerank_results(deduped, query)

    # Take top K
    final_results = reranked[:top_k]

    # Log statistics
    duration_ms = int((time.time() - start_time) * 1000)
    _log_stats(query, results, final_results, duration_ms)

    return final_results


async def _fanout_search(
    query: SearchQuery,
    use_cache: bool,
) -> list[ExternalEvidence]:
    """Dispatch searches to all sources in parallel.

    Args:
        query: The search query
        use_cache: Whether to use cache

    Returns:
        Combined results from all sources
    """
    tasks = []

    # GitHub Issues
    if EvidenceSource.GITHUB in query.include_sources:
        tasks.append(
            _search_github(query, use_cache)
        )

    # Stack Overflow
    if EvidenceSource.STACKOVERFLOW in query.include_sources:
        tasks.append(
            _search_stackoverflow(query, use_cache)
        )

    # OSV.dev
    if EvidenceSource.OSV in query.include_sources and query.package_versions:
        tasks.append(
            _search_osv(query, use_cache)
        )

    # Run all searches in parallel
    results_lists = await asyncio.gather(*tasks, return_exceptions=True)

    # Combine results, filtering out exceptions
    all_results: list[ExternalEvidence] = []
    for result in results_lists:
        if isinstance(result, Exception):
            logger.error(f"Search task failed: {result}")
        elif isinstance(result, list):
            all_results.extend(result)

    return all_results


async def _search_github(query: SearchQuery, use_cache: bool) -> list[ExternalEvidence]:
    """Search GitHub Issues."""
    try:
        return await search_github_issues(
            failure_signature=query.failure_signature,
            package_names=query.package_names,
            delta_items=query.delta_items,
            max_results=query.max_results_per_source,
            use_cache=use_cache,
        )
    except Exception as e:
        logger.error(f"GitHub search failed: {e}")
        return []


async def _search_stackoverflow(query: SearchQuery, use_cache: bool) -> list[ExternalEvidence]:
    """Search Stack Overflow."""
    try:
        return await search_stackoverflow(
            failure_signature=query.failure_signature,
            package_names=query.package_names,
            max_results=query.max_results_per_source,
            use_cache=use_cache,
        )
    except Exception as e:
        logger.error(f"Stack Overflow search failed: {e}")
        return []


async def _search_osv(query: SearchQuery, use_cache: bool) -> list[ExternalEvidence]:
    """Search OSV.dev for vulnerabilities."""
    try:
        # Convert package_versions dict to list of tuples
        packages = list(query.package_versions.items())
        return await lookup_vulnerabilities(
            packages=packages,
            use_cache=use_cache,
        )
    except Exception as e:
        logger.error(f"OSV search failed: {e}")
        return []


def _extract_packages_from_deltas(delta_items: list[str]) -> list[str]:
    """Extract package names from delta node IDs."""
    packages = []
    for delta_id in delta_items:
        pkg = extract_package_from_delta(delta_id)
        if pkg and pkg not in packages:
            packages.append(pkg)
    return packages


def _deduplicate_results(results: list[ExternalEvidence]) -> list[ExternalEvidence]:
    """Remove near-duplicate results.

    Uses text similarity on title + snippet to detect duplicates.
    """
    if not results:
        return []

    def get_text(e: ExternalEvidence) -> str:
        return f"{e.title} {e.snippet}"

    return deduplicate_results(
        results,
        key_fn=get_text,
        similarity_threshold=DEDUP_SIMILARITY_THRESHOLD,
    )


def _rerank_results(
    results: list[ExternalEvidence],
    query: SearchQuery,
) -> list[ExternalEvidence]:
    """Rerank results based on combined signals.

    Adjusts relevance scores based on:
    - Original source score
    - Cross-source consistency (same issue mentioned multiple places)
    - Package match bonus
    """
    if not results:
        return []

    # Count how many sources mention similar content
    content_counts: dict[str, int] = {}
    for result in results:
        # Create a simplified content hash
        key = _content_hash(result)
        content_counts[key] = content_counts.get(key, 0) + 1

    # Adjust scores
    for result in results:
        key = _content_hash(result)
        count = content_counts.get(key, 1)

        # Boost for cross-source validation
        if count > 1:
            result.relevance_score = min(1.0, result.relevance_score + 0.1 * (count - 1))

        # Boost for CVEs (important for security)
        if result.source == EvidenceSource.OSV:
            result.relevance_score = min(1.0, result.relevance_score + 0.05)

    # Sort by adjusted relevance
    results.sort()

    return results


def _content_hash(evidence: ExternalEvidence) -> str:
    """Create a simplified hash for content similarity grouping."""
    # Use first 100 chars of title as a rough grouping key
    key = evidence.title[:100].lower()
    return hashlib.md5(key.encode()).hexdigest()[:8]


def _log_stats(
    query: SearchQuery,
    raw_results: list[ExternalEvidence],
    final_results: list[ExternalEvidence],
    duration_ms: int,
) -> None:
    """Log retrieval statistics."""
    # Count results per source
    by_source: dict[str, int] = {}
    for result in raw_results:
        source = result.source.value
        by_source[source] = by_source.get(source, 0) + 1

    logger.info(
        f"Multi-source retrieval completed in {duration_ms}ms: "
        f"{len(raw_results)} raw -> {len(final_results)} final "
        f"(by source: {by_source})"
    )


def get_retrieval_stats(
    results: list[ExternalEvidence],
    query: SearchQuery,
    duration_ms: int,
) -> RetrievalStats:
    """Generate statistics for a retrieval operation.

    Args:
        results: Final results
        query: Original query
        duration_ms: Time taken

    Returns:
        RetrievalStats object
    """
    # Count by source
    by_source: dict[str, int] = {}
    for result in results:
        source = result.source.value
        by_source[source] = by_source.get(source, 0) + 1

    # Generate query hash
    query_hash = hashlib.sha256(
        f"{query.failure_signature}|{','.join(query.package_names)}".encode()
    ).hexdigest()[:12]

    return RetrievalStats(
        query_hash=query_hash,
        sources_queried=[s.value for s in query.include_sources],
        results_per_source=by_source,
        total_results=len(results),
        duration_ms=duration_ms,
    )


async def search_for_delta(
    delta_id: str,
    delta_value_a: str | None,
    delta_value_b: str | None,
    failure_signature: str | None = None,
    max_results: int = 5,
) -> list[ExternalEvidence]:
    """Search for a specific delta.

    Convenience function to search all sources for information
    about a specific configuration delta.

    Args:
        delta_id: Delta node ID (e.g., "env:LANG")
        delta_value_a: Value in environment A
        delta_value_b: Value in environment B
        failure_signature: Optional failure message
        max_results: Maximum results

    Returns:
        List of ExternalEvidence
    """
    # Extract search terms
    package = extract_package_from_delta(delta_id)
    packages = [package] if package else []

    # Build a simple failure signature if not provided
    if not failure_signature:
        parts = delta_id.split(":")
        failure_signature = f"{parts[-1]} configuration issue"

    # Search
    return await search_all_sources(
        failure_signature=failure_signature,
        delta_items=[delta_id],
        package_names=packages,
        top_k=max_results,
    )
