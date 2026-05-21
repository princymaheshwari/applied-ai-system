"""Data models for multi-source retrieval.

This module defines the data structures for:
- SearchQuery: What we're searching for
- ExternalEvidence: Normalized results from any source
- Source-specific result types

All external results are normalized to ExternalEvidence for
consistent handling by the agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class EvidenceSource(str, Enum):
    """Sources of external evidence."""

    GITHUB = "github"
    STACKOVERFLOW = "stackoverflow"
    OSV = "osv"
    LIBRARIES_IO = "libraries_io"


class EvidenceType(str, Enum):
    """Types of evidence found."""

    ISSUE = "issue"  # GitHub issue
    QUESTION = "question"  # Stack Overflow question
    ANSWER = "answer"  # Stack Overflow answer
    VULNERABILITY = "vulnerability"  # OSV CVE
    PACKAGE_INFO = "package_info"  # libraries.io metadata


@dataclass
class SearchQuery:
    """A search query to be sent to external sources.

    Attributes:
        failure_signature: The error message/trace to search for
        delta_items: List of delta node IDs (e.g., ["env:LANG", "os_pkg:libssl3"])
        package_names: Package names to search in (extracted from deltas)
        package_versions: Optional version constraints for CVE lookup
        max_results_per_source: Maximum results to fetch from each source
        include_sources: Which sources to query (defaults to all)
    """

    failure_signature: str
    delta_items: list[str] = field(default_factory=list)
    package_names: list[str] = field(default_factory=list)
    package_versions: dict[str, str] = field(default_factory=dict)
    max_results_per_source: int = 5
    include_sources: list[EvidenceSource] = field(
        default_factory=lambda: list(EvidenceSource)
    )

    def get_search_terms(self) -> list[str]:
        """Extract key search terms from the query.

        Returns terms suitable for text search APIs.
        """
        terms = []

        # Extract error type from signature
        sig = self.failure_signature
        if ":" in sig:
            # e.g., "UnicodeDecodeError: 'ascii'" -> "UnicodeDecodeError"
            error_type = sig.split(":")[0].strip()
            if error_type and not error_type.startswith("/"):
                terms.append(error_type)

        # Add package names
        terms.extend(self.package_names)

        # Extract key terms from delta items
        for delta in self.delta_items:
            if ":" in delta:
                # e.g., "env:LANG" -> "LANG"
                item_name = delta.split(":", 1)[1]
                if len(item_name) > 2:  # Skip very short names
                    terms.append(item_name)

        return list(set(terms))  # Deduplicate


@dataclass
class ExternalEvidence:
    """A normalized result from any external source.

    All source-specific results are converted to this format
    for consistent handling by the agent.

    Attributes:
        source: Which source this came from
        evidence_type: Type of evidence (issue, question, CVE, etc.)
        title: Title of the issue/question/CVE
        url: Link to the source
        snippet: Relevant text excerpt (max ~500 chars)
        relevance_score: Computed relevance (0.0-1.0)
        created_at: When this was created (if available)
        metadata: Source-specific additional data
        retrieved_at: When we fetched this result
        cache_key: Key used for caching (if cached)
    """

    source: EvidenceSource
    evidence_type: EvidenceType
    title: str
    url: str
    snippet: str
    relevance_score: float = 0.5
    created_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    retrieved_at: datetime = field(default_factory=datetime.utcnow)
    cache_key: str | None = None

    def __lt__(self, other: "ExternalEvidence") -> bool:
        """Sort by relevance score (descending)."""
        return self.relevance_score > other.relevance_score

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "source": self.source.value,
            "evidence_type": self.evidence_type.value,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "relevance_score": self.relevance_score,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "metadata": self.metadata,
            "retrieved_at": self.retrieved_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExternalEvidence":
        """Create from dictionary."""
        return cls(
            source=EvidenceSource(data["source"]),
            evidence_type=EvidenceType(data["evidence_type"]),
            title=data["title"],
            url=data["url"],
            snippet=data["snippet"],
            relevance_score=data.get("relevance_score", 0.5),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if data.get("created_at")
                else None
            ),
            metadata=data.get("metadata", {}),
            retrieved_at=(
                datetime.fromisoformat(data["retrieved_at"])
                if data.get("retrieved_at")
                else datetime.utcnow()
            ),
        )


@dataclass
class GitHubIssue:
    """Raw GitHub issue data before normalization."""

    number: int
    title: str
    body: str
    html_url: str
    state: str  # "open" or "closed"
    created_at: datetime
    updated_at: datetime
    comments: int
    reactions_total: int
    repository: str
    labels: list[str] = field(default_factory=list)

    def to_evidence(self, relevance_score: float = 0.5) -> ExternalEvidence:
        """Convert to normalized ExternalEvidence."""
        snippet = self.body[:500] if self.body else ""
        return ExternalEvidence(
            source=EvidenceSource.GITHUB,
            evidence_type=EvidenceType.ISSUE,
            title=f"[{self.repository}] {self.title}",
            url=self.html_url,
            snippet=snippet,
            relevance_score=relevance_score,
            created_at=self.created_at,
            metadata={
                "number": self.number,
                "state": self.state,
                "comments": self.comments,
                "reactions": self.reactions_total,
                "labels": self.labels,
                "repository": self.repository,
            },
        )


@dataclass
class StackOverflowQuestion:
    """Raw Stack Overflow question data before normalization."""

    question_id: int
    title: str
    body: str
    link: str
    score: int
    answer_count: int
    is_answered: bool
    creation_date: datetime
    tags: list[str] = field(default_factory=list)
    accepted_answer_id: int | None = None

    def to_evidence(self, relevance_score: float = 0.5) -> ExternalEvidence:
        """Convert to normalized ExternalEvidence."""
        snippet = self.body[:500] if self.body else ""
        return ExternalEvidence(
            source=EvidenceSource.STACKOVERFLOW,
            evidence_type=EvidenceType.QUESTION,
            title=self.title,
            url=self.link,
            snippet=snippet,
            relevance_score=relevance_score,
            created_at=self.creation_date,
            metadata={
                "question_id": self.question_id,
                "score": self.score,
                "answer_count": self.answer_count,
                "is_answered": self.is_answered,
                "tags": self.tags,
                "has_accepted_answer": self.accepted_answer_id is not None,
            },
        )


@dataclass
class OSVVulnerability:
    """Raw OSV.dev vulnerability data before normalization."""

    id: str  # e.g., "GHSA-xxxx-xxxx-xxxx" or "CVE-2024-1234"
    summary: str
    details: str
    severity: str | None  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    published: datetime
    modified: datetime
    affected_packages: list[str]
    affected_versions: list[str]
    references: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)

    def to_evidence(self, relevance_score: float = 0.5) -> ExternalEvidence:
        """Convert to normalized ExternalEvidence."""
        # Use first reference URL or OSV.dev URL
        url = self.references[0] if self.references else f"https://osv.dev/vulnerability/{self.id}"

        snippet = self.details[:500] if self.details else self.summary

        return ExternalEvidence(
            source=EvidenceSource.OSV,
            evidence_type=EvidenceType.VULNERABILITY,
            title=f"[{self.severity or 'UNKNOWN'}] {self.id}: {self.summary}",
            url=url,
            snippet=snippet,
            relevance_score=relevance_score,
            created_at=self.published,
            metadata={
                "vuln_id": self.id,
                "severity": self.severity,
                "affected_packages": self.affected_packages,
                "affected_versions": self.affected_versions,
                "aliases": self.aliases,
            },
        )


@dataclass
class RetrievalStats:
    """Statistics about a retrieval operation."""

    query_hash: str
    sources_queried: list[str]
    results_per_source: dict[str, int]
    cache_hits: int = 0
    cache_misses: int = 0
    total_results: int = 0
    duration_ms: int = 0
    errors: list[str] = field(default_factory=list)
