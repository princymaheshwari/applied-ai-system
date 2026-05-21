"""Multi-source retrieval module.

Searches GitHub Issues, StackExchange, OSV.dev, and libraries.io
for external evidence to help diagnose configuration bugs.

Features:
- Fan-out: Query all sources in parallel
- Deduplication: Remove near-duplicate results
- Relevance reranking: Combine scores and resort
- Aggressive caching: SQLite cache to avoid rate limits

Main entry point:
    from config_detective.retrieval import search_all_sources

    results = await search_all_sources(
        failure_signature="UnicodeDecodeError: 'ascii'",
        delta_items=["env:LANG"],
        package_names=["requests"],
    )

Individual sources:
    from config_detective.retrieval import (
        search_github_issues,
        search_stackoverflow,
        lookup_vulnerabilities,
    )
"""

# Models
from .models import (
    EvidenceSource,
    EvidenceType,
    ExternalEvidence,
    GitHubIssue,
    OSVVulnerability,
    RetrievalStats,
    SearchQuery,
    StackOverflowQuestion,
)

# Cache
from .cache import (
    RetrievalCache,
    get_cache,
    make_cache_key,
)

# Utils
from .utils import (
    build_github_query,
    build_stackoverflow_query,
    clean_search_query,
    compute_community_score,
    compute_package_match_score,
    compute_recency_score,
    compute_text_similarity,
    deduplicate_results,
    extract_error_type,
    extract_package_from_delta,
    strip_html,
)

# Source-specific searches
from .github_search import (
    search_github_by_package,
    search_github_issues,
)

from .stackoverflow import (
    search_by_tags,
    search_stackoverflow,
)

from .osv import (
    lookup_by_cve,
    lookup_from_deltas,
    lookup_vulnerabilities,
)

# Multi-source orchestrator
from .multi_source import (
    get_retrieval_stats,
    search_all_sources,
    search_for_delta,
)

__all__ = [
    # Models
    "EvidenceSource",
    "EvidenceType",
    "ExternalEvidence",
    "GitHubIssue",
    "StackOverflowQuestion",
    "OSVVulnerability",
    "SearchQuery",
    "RetrievalStats",
    # Cache
    "RetrievalCache",
    "get_cache",
    "make_cache_key",
    # Utils
    "extract_error_type",
    "clean_search_query",
    "strip_html",
    "extract_package_from_delta",
    "compute_text_similarity",
    "compute_recency_score",
    "compute_community_score",
    "compute_package_match_score",
    "build_github_query",
    "build_stackoverflow_query",
    "deduplicate_results",
    # GitHub
    "search_github_issues",
    "search_github_by_package",
    # Stack Overflow
    "search_stackoverflow",
    "search_by_tags",
    # OSV
    "lookup_vulnerabilities",
    "lookup_by_cve",
    "lookup_from_deltas",
    # Multi-source
    "search_all_sources",
    "search_for_delta",
    "get_retrieval_stats",
]
