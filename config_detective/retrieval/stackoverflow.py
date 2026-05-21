"""Stack Overflow search for external evidence.

This module searches Stack Overflow for questions and answers
related to error signatures and configuration issues.

API: StackExchange API v2.3
Rate limits:
- Without key: 300 requests/day
- With key: 10,000 requests/day

Usage:
    from config_detective.retrieval.stackoverflow import search_stackoverflow

    results = await search_stackoverflow(
        query="UnicodeDecodeError",
        tags=["python", "encoding"],
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
from .models import (
    ExternalEvidence,
    EvidenceSource,
    EvidenceType,
    StackOverflowQuestion,
)
from .utils import (
    build_stackoverflow_query,
    clean_search_query,
    compute_community_score,
    compute_package_match_score,
    compute_recency_score,
    compute_text_similarity,
    extract_error_type,
    strip_html,
)

logger = logging.getLogger(__name__)

# StackExchange API configuration
SE_API_BASE = "https://api.stackexchange.com/2.3"
SE_SEARCH_ADVANCED = f"{SE_API_BASE}/search/advanced"

# Default timeout
REQUEST_TIMEOUT = 10.0

# Tag mappings for common technologies
TECHNOLOGY_TAGS: dict[str, list[str]] = {
    "python": ["python", "python-3.x"],
    "node": ["node.js", "javascript"],
    "locale": ["python", "unicode", "encoding", "utf-8", "locale"],
    "ssl": ["python", "ssl", "openssl", "cryptography"],
    "timezone": ["python", "datetime", "timezone", "pytz"],
    "database": ["python", "postgresql", "mysql", "sqlite"],
}


def _get_stackexchange_params() -> dict[str, str]:
    """Get base parameters for StackExchange API requests."""
    params = {
        "site": "stackoverflow",
        "filter": "withbody",  # Include body in response
    }

    # Add API key if available (higher rate limits)
    key = os.getenv("STACKEXCHANGE_KEY")
    if key:
        params["key"] = key

    return params


async def search_stackoverflow(
    failure_signature: str,
    tags: list[str] | None = None,
    package_names: list[str] | None = None,
    max_results: int = 10,
    use_cache: bool = True,
) -> list[ExternalEvidence]:
    """Search Stack Overflow for relevant questions.

    Args:
        failure_signature: The error message to search for
        tags: Optional tags to filter by (e.g., ["python", "unicode"])
        package_names: Optional package names for relevance scoring
        max_results: Maximum results to return
        use_cache: Whether to use cached results

    Returns:
        List of ExternalEvidence from Stack Overflow
    """
    cache = get_cache()

    # Extract error type and clean query
    error_type = extract_error_type(failure_signature)
    cleaned_sig = clean_search_query(failure_signature, max_length=80)

    # Build search query
    query = build_stackoverflow_query(
        error_type=error_type,
        tags=tags,
        additional_terms=[cleaned_sig] if cleaned_sig else None,
    )

    if not query.strip():
        logger.warning("Empty Stack Overflow search query, skipping")
        return []

    # Infer tags from error type if not provided
    inferred_tags = tags or _infer_tags(failure_signature, package_names)

    # Check cache
    cache_key = make_cache_key(
        "stackoverflow", query, ",".join(inferred_tags), str(max_results)
    )
    if use_cache:
        cached = cache.get("stackoverflow", cache_key)
        if cached is not None:
            logger.debug(f"Stack Overflow cache hit for query: {query[:50]}")
            return [ExternalEvidence.from_dict(e) for e in cached]

    # Make API request
    try:
        results = await _fetch_stackoverflow_questions(
            query, inferred_tags, max_results
        )
    except Exception as e:
        logger.error(f"Stack Overflow search failed: {e}")
        return []

    # Convert to ExternalEvidence with relevance scoring
    evidence_list = []
    for question in results:
        relevance = _compute_so_relevance(
            question, failure_signature, package_names or []
        )
        evidence = question.to_evidence(relevance_score=relevance)
        evidence.cache_key = cache_key
        evidence_list.append(evidence)

    # Sort by relevance
    evidence_list.sort()

    # Cache results
    if use_cache:
        cache.set("stackoverflow", cache_key, [e.to_dict() for e in evidence_list])

    return evidence_list


def _infer_tags(
    failure_signature: str,
    package_names: list[str] | None,
) -> list[str]:
    """Infer Stack Overflow tags from the failure signature and packages."""
    tags = set()

    # Always include Python (our primary use case)
    tags.add("python")

    sig_lower = failure_signature.lower()

    # Check for known patterns
    if "unicode" in sig_lower or "encoding" in sig_lower or "ascii" in sig_lower:
        tags.update(TECHNOLOGY_TAGS.get("locale", []))
    if "ssl" in sig_lower or "certificate" in sig_lower:
        tags.update(TECHNOLOGY_TAGS.get("ssl", []))
    if "timezone" in sig_lower or "tz" in sig_lower or "datetime" in sig_lower:
        tags.update(TECHNOLOGY_TAGS.get("timezone", []))

    # Add package names as potential tags
    if package_names:
        for pkg in package_names[:3]:  # Limit to first 3
            tags.add(pkg.lower())

    return list(tags)[:5]  # SO allows max 5 tags


async def _fetch_stackoverflow_questions(
    query: str,
    tags: list[str],
    max_results: int,
) -> list[StackOverflowQuestion]:
    """Fetch questions from StackExchange API.

    Args:
        query: Search query
        tags: Tags to filter by
        max_results: Maximum results

    Returns:
        List of StackOverflowQuestion objects
    """
    params = _get_stackexchange_params()
    params.update({
        "q": query,
        "pagesize": min(max_results, 30),
        "order": "desc",
        "sort": "relevance",
    })

    # Add tags if provided
    if tags:
        params["tagged"] = ";".join(tags[:5])

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.get(SE_SEARCH_ADVANCED, params=params)

        if response.status_code == 400:
            # Often means bad query, try without tags
            logger.debug("SO search failed with tags, retrying without")
            params.pop("tagged", None)
            response = await client.get(SE_SEARCH_ADVANCED, params=params)

        response.raise_for_status()
        data = response.json()

    # Check for API throttling
    if data.get("backoff"):
        logger.warning(f"Stack Overflow API backoff: {data['backoff']}s")

    questions = []
    for item in data.get("items", [])[:max_results]:
        try:
            question = _parse_so_question(item)
            questions.append(question)
        except Exception as e:
            logger.debug(f"Failed to parse SO question: {e}")
            continue

    logger.info(f"Stack Overflow search returned {len(questions)} questions for: {query[:50]}")
    return questions


def _parse_so_question(item: dict[str, Any]) -> StackOverflowQuestion:
    """Parse a StackExchange API response item into a StackOverflowQuestion."""
    # Parse creation date (Unix timestamp)
    creation_date = datetime.utcfromtimestamp(item["creation_date"])

    # Strip HTML from body
    body = strip_html(item.get("body", ""))

    return StackOverflowQuestion(
        question_id=item["question_id"],
        title=item.get("title", ""),
        body=body,
        link=item.get("link", ""),
        score=item.get("score", 0),
        answer_count=item.get("answer_count", 0),
        is_answered=item.get("is_answered", False),
        creation_date=creation_date,
        tags=item.get("tags", []),
        accepted_answer_id=item.get("accepted_answer_id"),
    )


def _compute_so_relevance(
    question: StackOverflowQuestion,
    failure_signature: str,
    package_names: list[str],
) -> float:
    """Compute relevance score for a Stack Overflow question.

    Args:
        question: The SO question
        failure_signature: Original failure signature
        package_names: Package names to match

    Returns:
        Relevance score 0.0-1.0
    """
    score = 0.0

    # Text similarity (40% weight)
    combined_text = f"{question.title} {question.body[:500]}"
    text_sim = compute_text_similarity(combined_text, failure_signature)
    score += text_sim * 0.4

    # Package/tag match (25% weight)
    # Check both package names and tags
    all_terms = package_names + question.tags
    pkg_score = compute_package_match_score(combined_text, package_names)
    tag_match = len(set(t.lower() for t in question.tags) & set(p.lower() for p in package_names))
    tag_score = min(1.0, tag_match * 0.3) if package_names else 0
    score += max(pkg_score, tag_score) * 0.25

    # Recency (15% weight)
    recency = compute_recency_score(question.creation_date)
    score += recency * 0.15

    # Community signal (20% weight)
    community = compute_community_score({
        "score": question.score,
        "answer_count": question.answer_count,
        "is_answered": question.is_answered,
        "has_accepted_answer": question.accepted_answer_id is not None,
    })
    score += community * 0.2

    return min(1.0, score)


async def search_by_tags(
    tags: list[str],
    query: str | None = None,
    max_results: int = 5,
) -> list[ExternalEvidence]:
    """Search Stack Overflow by tags only.

    Useful for browsing common issues in a technology area.

    Args:
        tags: Tags to search (required)
        query: Optional additional query text
        max_results: Maximum results

    Returns:
        List of ExternalEvidence
    """
    if not tags:
        return []

    cache = get_cache()
    cache_key = make_cache_key("so_tags", ",".join(tags), query or "")

    cached = cache.get("stackoverflow", cache_key)
    if cached is not None:
        return [ExternalEvidence.from_dict(e) for e in cached]

    try:
        results = await _fetch_stackoverflow_questions(
            query or "", tags, max_results
        )
    except Exception as e:
        logger.error(f"Stack Overflow tag search failed: {e}")
        return []

    evidence_list = [q.to_evidence(relevance_score=0.5) for q in results]

    cache.set("stackoverflow", cache_key, [e.to_dict() for e in evidence_list])

    return evidence_list
