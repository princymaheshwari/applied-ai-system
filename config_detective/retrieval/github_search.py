"""GitHub Issues search for external evidence.

This module searches GitHub Issues for error signatures and
related discussions that might help diagnose configuration bugs.

API: GitHub REST API v3
Rate limits:
- Unauthenticated: 10 requests/minute
- Authenticated: 30 requests/minute

Usage:
    from config_detective.retrieval.github_search import search_github_issues

    results = await search_github_issues(
        query="UnicodeDecodeError",
        package_names=["requests", "cryptography"],
        max_results=10,
    )
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

import httpx

from .cache import get_cache, make_cache_key
from .models import ExternalEvidence, EvidenceSource, EvidenceType, GitHubIssue
from .utils import (
    build_github_query,
    clean_search_query,
    compute_community_score,
    compute_package_match_score,
    compute_recency_score,
    compute_text_similarity,
    extract_error_type,
    strip_html,
)

logger = logging.getLogger(__name__)

# GitHub API configuration
GITHUB_API_BASE = "https://api.github.com"
GITHUB_SEARCH_ISSUES = f"{GITHUB_API_BASE}/search/issues"

# Default timeout for API requests
REQUEST_TIMEOUT = 10.0


def _get_github_headers() -> dict[str, str]:
    """Get headers for GitHub API requests."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "config-detective/1.0",
    }

    # Add auth token if available (higher rate limits)
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"

    return headers


async def search_github_issues(
    failure_signature: str,
    package_names: list[str] | None = None,
    delta_items: list[str] | None = None,
    max_results: int = 10,
    use_cache: bool = True,
) -> list[ExternalEvidence]:
    """Search GitHub Issues for relevant discussions.

    Args:
        failure_signature: The error message to search for
        package_names: Optional package names to include in search
        delta_items: Optional delta items to extract search terms from
        max_results: Maximum results to return
        use_cache: Whether to use cached results

    Returns:
        List of ExternalEvidence from GitHub Issues
    """
    cache = get_cache()

    # Build search query
    error_type = extract_error_type(failure_signature)
    cleaned_sig = clean_search_query(failure_signature, max_length=50)

    # Build query terms
    query_terms = []
    if error_type:
        query_terms.append(error_type)
    if cleaned_sig and cleaned_sig != error_type:
        query_terms.append(cleaned_sig)

    query = build_github_query(
        error_type=error_type,
        package_names=package_names,
        additional_terms=query_terms[:2],
    )

    if not query.strip():
        logger.warning("Empty GitHub search query, skipping")
        return []

    # Check cache
    cache_key = make_cache_key("github", query, str(max_results))
    if use_cache:
        cached = cache.get("github", cache_key)
        if cached is not None:
            logger.debug(f"GitHub cache hit for query: {query[:50]}")
            return [ExternalEvidence.from_dict(e) for e in cached]

    # Make API request
    try:
        results = await _fetch_github_issues(query, max_results)
    except Exception as e:
        logger.error(f"GitHub search failed: {e}")
        return []

    # Convert to ExternalEvidence with relevance scoring
    evidence_list = []
    for issue in results:
        relevance = _compute_github_relevance(
            issue, failure_signature, package_names or []
        )
        evidence = issue.to_evidence(relevance_score=relevance)
        evidence.cache_key = cache_key
        evidence_list.append(evidence)

    # Sort by relevance
    evidence_list.sort()

    # Cache results
    if use_cache:
        cache.set("github", cache_key, [e.to_dict() for e in evidence_list])

    return evidence_list


async def _fetch_github_issues(query: str, max_results: int) -> list[GitHubIssue]:
    """Fetch issues from GitHub API.

    Args:
        query: GitHub search query
        max_results: Maximum results to fetch

    Returns:
        List of GitHubIssue objects
    """
    params = {
        "q": query,
        "per_page": min(max_results, 30),  # GitHub max is 30 per page
        "sort": "reactions",
        "order": "desc",
    }

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.get(
            GITHUB_SEARCH_ISSUES,
            params=params,
            headers=_get_github_headers(),
        )

        if response.status_code == 403:
            logger.warning("GitHub rate limit exceeded")
            return []

        response.raise_for_status()
        data = response.json()

    issues = []
    for item in data.get("items", [])[:max_results]:
        try:
            issue = _parse_github_issue(item)
            issues.append(issue)
        except Exception as e:
            logger.debug(f"Failed to parse GitHub issue: {e}")
            continue

    logger.info(f"GitHub search returned {len(issues)} issues for: {query[:50]}")
    return issues


def _parse_github_issue(item: dict[str, Any]) -> GitHubIssue:
    """Parse a GitHub API response item into a GitHubIssue."""
    # Extract repository from URL
    repo_url = item.get("repository_url", "")
    repo_name = "/".join(repo_url.split("/")[-2:]) if repo_url else "unknown"

    # Parse dates
    created_at = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
    updated_at = datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))

    # Get reactions total
    reactions = item.get("reactions", {})
    reactions_total = reactions.get("total_count", 0) if isinstance(reactions, dict) else 0

    # Get labels
    labels = [label.get("name", "") for label in item.get("labels", [])]

    return GitHubIssue(
        number=item["number"],
        title=item.get("title", ""),
        body=item.get("body") or "",
        html_url=item["html_url"],
        state=item.get("state", "unknown"),
        created_at=created_at,
        updated_at=updated_at,
        comments=item.get("comments", 0),
        reactions_total=reactions_total,
        repository=repo_name,
        labels=labels,
    )


def _compute_github_relevance(
    issue: GitHubIssue,
    failure_signature: str,
    package_names: list[str],
) -> float:
    """Compute relevance score for a GitHub issue.

    Args:
        issue: The GitHub issue
        failure_signature: Original failure signature
        package_names: Package names to match

    Returns:
        Relevance score 0.0-1.0
    """
    score = 0.0

    # Text similarity (40% weight)
    combined_text = f"{issue.title} {issue.body[:500]}"
    text_sim = compute_text_similarity(combined_text, failure_signature)
    score += text_sim * 0.4

    # Package match (30% weight)
    pkg_score = compute_package_match_score(combined_text, package_names)
    score += pkg_score * 0.3

    # Recency (15% weight)
    recency = compute_recency_score(issue.created_at)
    score += recency * 0.15

    # Community signal (15% weight)
    community = compute_community_score({
        "reactions": issue.reactions_total,
        "comments": issue.comments,
    })
    score += community * 0.15

    # Bonus for closed issues (likely resolved)
    if issue.state == "closed":
        score = min(1.0, score + 0.05)

    return min(1.0, score)


async def search_github_by_package(
    package_name: str,
    error_type: str | None = None,
    max_results: int = 5,
) -> list[ExternalEvidence]:
    """Search GitHub Issues in a specific package's repository.

    Args:
        package_name: Package name to search in
        error_type: Optional error type to filter by
        max_results: Maximum results

    Returns:
        List of ExternalEvidence
    """
    # Build query targeting the package repo
    query_parts = [f"repo:*/{package_name}"]
    if error_type:
        query_parts.append(f'"{error_type}"')
    query_parts.append("is:issue")

    query = " ".join(query_parts)

    cache = get_cache()
    cache_key = make_cache_key("github_pkg", package_name, error_type or "")

    # Check cache
    cached = cache.get("github", cache_key)
    if cached is not None:
        return [ExternalEvidence.from_dict(e) for e in cached]

    try:
        results = await _fetch_github_issues(query, max_results)
    except Exception as e:
        logger.error(f"GitHub package search failed: {e}")
        return []

    evidence_list = []
    for issue in results:
        relevance = 0.7 if error_type else 0.5  # Higher if error matches
        evidence = issue.to_evidence(relevance_score=relevance)
        evidence_list.append(evidence)

    # Cache
    cache.set("github", cache_key, [e.to_dict() for e in evidence_list])

    return evidence_list
