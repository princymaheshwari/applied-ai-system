"""Utility functions for multi-source retrieval.

This module provides helpers for:
- Query building and cleaning
- Text similarity scoring
- Relevance computation
- HTML/Markdown stripping
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta
from typing import Any


# Common error type patterns to extract
ERROR_PATTERNS = [
    r"(\w+Error):",  # Python errors: UnicodeDecodeError:, ValueError:
    r"(\w+Exception):",  # Java-style: NullPointerException:
    r"Error: (\w+)",  # Generic: Error: ENOENT
    r"error\[E\d+\]",  # Rust errors: error[E0308]
    r"fatal error:",  # C/C++ fatal errors
    r"panic:",  # Go/Rust panics
]


def extract_error_type(failure_signature: str) -> str | None:
    """Extract the error type from a failure signature.

    Args:
        failure_signature: Full error message/trace

    Returns:
        Extracted error type or None

    Examples:
        >>> extract_error_type("UnicodeDecodeError: 'ascii' codec")
        'UnicodeDecodeError'
        >>> extract_error_type("Error: ENOENT: no such file")
        'ENOENT'
    """
    for pattern in ERROR_PATTERNS:
        match = re.search(pattern, failure_signature, re.IGNORECASE)
        if match:
            return match.group(1) if match.lastindex else match.group(0)
    return None


def clean_search_query(text: str, max_length: int = 100) -> str:
    """Clean and truncate text for use in search queries.

    Args:
        text: Raw text to clean
        max_length: Maximum length of cleaned query

    Returns:
        Cleaned query string
    """
    # Remove file paths
    text = re.sub(r'[\'"][^"\']*[/\\][^\'"]*[\'"]', "", text)
    text = re.sub(r"File ['\"][^'\"]+['\"]", "", text)

    # Remove line numbers
    text = re.sub(r", line \d+", "", text)
    text = re.sub(r":\d+:\d+", "", text)

    # Remove hex addresses
    text = re.sub(r"0x[0-9a-fA-F]+", "", text)

    # Remove excessive whitespace
    text = " ".join(text.split())

    # Truncate
    if len(text) > max_length:
        text = text[:max_length].rsplit(" ", 1)[0] + "..."

    return text.strip()


def strip_html(text: str) -> str:
    """Remove HTML tags from text.

    Args:
        text: HTML text

    Returns:
        Plain text
    """
    # Decode HTML entities
    text = html.unescape(text)

    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)

    # Remove code blocks (common in SO answers)
    text = re.sub(r"```[\s\S]*?```", " [code] ", text)
    text = re.sub(r"`[^`]+`", " [code] ", text)

    # Normalize whitespace
    text = " ".join(text.split())

    return text.strip()


def extract_package_from_delta(delta_id: str) -> str | None:
    """Extract package name from a delta node ID.

    Args:
        delta_id: Delta node ID like "py_pkg:requests" or "os_pkg:libssl3"

    Returns:
        Package name or None

    Examples:
        >>> extract_package_from_delta("py_pkg:requests")
        'requests'
        >>> extract_package_from_delta("os_pkg:libssl3")
        'libssl3'
    """
    if ":" not in delta_id:
        return None

    prefix, name = delta_id.split(":", 1)
    if prefix in ("py_pkg", "node_pkg", "os_pkg"):
        return name
    return None


def compute_text_similarity(text1: str, text2: str) -> float:
    """Compute Jaccard similarity between two texts.

    Uses word-level tokenization for comparison.

    Args:
        text1: First text
        text2: Second text

    Returns:
        Similarity score 0.0-1.0
    """
    # Tokenize
    words1 = set(re.findall(r"\w+", text1.lower()))
    words2 = set(re.findall(r"\w+", text2.lower()))

    if not words1 or not words2:
        return 0.0

    # Jaccard similarity
    intersection = len(words1 & words2)
    union = len(words1 | words2)

    return intersection / union if union > 0 else 0.0


def compute_recency_score(created_at: datetime | None, max_age_days: int = 365) -> float:
    """Compute a recency score based on creation date.

    More recent items get higher scores.

    Args:
        created_at: When the item was created
        max_age_days: Items older than this get score 0

    Returns:
        Recency score 0.0-1.0
    """
    if created_at is None:
        return 0.5  # Unknown recency

    now = datetime.utcnow()
    # Handle timezone-aware datetimes
    if created_at.tzinfo is not None:
        created_at = created_at.replace(tzinfo=None)

    age = now - created_at
    age_days = age.total_seconds() / 86400

    if age_days < 0:
        return 1.0  # Future date (shouldn't happen)
    if age_days > max_age_days:
        return 0.0

    # Linear decay
    return 1.0 - (age_days / max_age_days)


def compute_community_score(metadata: dict[str, Any]) -> float:
    """Compute a community signal score from metadata.

    Higher scores for items with more engagement.

    Args:
        metadata: Source-specific metadata

    Returns:
        Community score 0.0-1.0
    """
    score = 0.0

    # GitHub: reactions, comments
    if "reactions" in metadata:
        reactions = metadata["reactions"]
        score += min(0.3, reactions * 0.02)  # Cap at 0.3 for 15+ reactions

    if "comments" in metadata:
        comments = metadata["comments"]
        score += min(0.2, comments * 0.02)  # Cap at 0.2 for 10+ comments

    # Stack Overflow: score, answer count
    if "score" in metadata:
        so_score = metadata["score"]
        score += min(0.3, so_score * 0.03)  # Cap at 0.3 for 10+ score

    if "answer_count" in metadata:
        answers = metadata["answer_count"]
        score += min(0.2, answers * 0.05)  # Cap at 0.2 for 4+ answers

    if metadata.get("is_answered"):
        score += 0.1

    if metadata.get("has_accepted_answer"):
        score += 0.1

    # OSV: severity
    severity = metadata.get("severity", "").upper()
    severity_scores = {
        "CRITICAL": 0.5,
        "HIGH": 0.4,
        "MEDIUM": 0.3,
        "LOW": 0.2,
    }
    score += severity_scores.get(severity, 0.1)

    return min(1.0, score)


def compute_package_match_score(
    result_text: str,
    package_names: list[str],
) -> float:
    """Compute how well a result matches package names.

    Args:
        result_text: Text from the result (title + snippet)
        package_names: List of package names to look for

    Returns:
        Match score 0.0-1.0
    """
    if not package_names:
        return 0.0

    result_lower = result_text.lower()
    matches = sum(1 for pkg in package_names if pkg.lower() in result_lower)

    return min(1.0, matches / len(package_names))


def build_github_query(
    error_type: str | None = None,
    package_names: list[str] | None = None,
    additional_terms: list[str] | None = None,
) -> str:
    """Build a GitHub search query string.

    Args:
        error_type: Error type to search for
        package_names: Package names (used as repo filters)
        additional_terms: Additional search terms

    Returns:
        GitHub search query string
    """
    parts = []

    if error_type:
        parts.append(f'"{error_type}"')

    if additional_terms:
        parts.extend(additional_terms[:3])  # Limit terms

    query = " ".join(parts)

    # Add is:issue filter
    if query:
        query += " is:issue"

    return query


def build_stackoverflow_query(
    error_type: str | None = None,
    tags: list[str] | None = None,
    additional_terms: list[str] | None = None,
) -> str:
    """Build a Stack Overflow search query string.

    Args:
        error_type: Error type to search for
        tags: Tags to filter by (e.g., ["python", "unicode"])
        additional_terms: Additional search terms

    Returns:
        StackOverflow search query string
    """
    parts = []

    if error_type:
        parts.append(error_type)

    if additional_terms:
        parts.extend(additional_terms[:3])

    return " ".join(parts)


def deduplicate_results(
    results: list[Any],
    key_fn: callable,
    similarity_threshold: float = 0.8,
) -> list[Any]:
    """Deduplicate results based on similarity.

    Args:
        results: List of results to deduplicate
        key_fn: Function to extract text for comparison from each result
        similarity_threshold: Similarity above which items are considered duplicates

    Returns:
        Deduplicated list
    """
    if not results:
        return []

    unique = [results[0]]

    for item in results[1:]:
        item_text = key_fn(item)
        is_duplicate = False

        for unique_item in unique:
            unique_text = key_fn(unique_item)
            similarity = compute_text_similarity(item_text, unique_text)
            if similarity >= similarity_threshold:
                is_duplicate = True
                break

        if not is_duplicate:
            unique.append(item)

    return unique
