"""Investigation state schema for the LangGraph orchestrator.

This module defines the typed state that flows through the investigation
workflow. LangGraph uses TypedDict for state management, ensuring type
safety across all nodes.

The state is divided into:
- Inputs: What the user provides
- Intermediate results: What each node produces
- Control flow: Iteration counts, confidence thresholds
- Output: Final report and fix suggestions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, TypedDict
from uuid import uuid4

if TYPE_CHECKING:
    import networkx as nx

    from config_detective.graph.schema import Delta
    from config_detective.memory.models import SimilarCaseResult
    from config_detective.retrieval.models import ExternalEvidence
    from config_detective.snapshot.models import EnvironmentSnapshot


class ErrorCategory(str, Enum):
    """Categories of configuration errors."""

    LOCALE = "locale"
    SSL = "ssl"
    TIMEZONE = "timezone"
    PYTHON_VERSION = "python_version"
    MISSING_PACKAGE = "missing_package"
    VERSION_MISMATCH = "version_mismatch"
    ENV_VAR = "env_var"
    UNKNOWN = "unknown"


class InvestigationStatus(str, Enum):
    """Status of the investigation."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


@dataclass
class Hypothesis:
    """A hypothesis about the root cause of the configuration bug.

    The Hypothesizer node generates k hypotheses, each with an explanation
    and proposed fix. The Critic node scores them and selects the best one.

    Attributes:
        id: Unique identifier for this hypothesis
        rank: Ranking among hypotheses (1 = most likely)
        delta_id: The suspected root cause delta node ID
        delta_type: Type of delta (e.g., "version_changed")
        explanation: Why this might be the cause
        fix_suggestion: Proposed fix in plain English
        fix_code: Concrete fix (e.g., "ENV LANG=C.UTF-8")
        confidence: Confidence score (0.0-1.0)
        supporting_evidence: Evidence supporting this hypothesis
        created_at: When this hypothesis was generated
    """

    id: str = field(default_factory=lambda: str(uuid4())[:8])
    rank: int = 1
    delta_id: str = ""
    delta_type: str = ""
    explanation: str = ""
    fix_suggestion: str = ""
    fix_code: str | None = None
    confidence: float = 0.5
    supporting_evidence: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "rank": self.rank,
            "delta_id": self.delta_id,
            "delta_type": self.delta_type,
            "explanation": self.explanation,
            "fix_suggestion": self.fix_suggestion,
            "fix_code": self.fix_code,
            "confidence": self.confidence,
            "supporting_evidence": self.supporting_evidence,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Hypothesis":
        """Create from dictionary."""
        return cls(
            id=data.get("id", str(uuid4())[:8]),
            rank=data.get("rank", 1),
            delta_id=data.get("delta_id", ""),
            delta_type=data.get("delta_type", ""),
            explanation=data.get("explanation", ""),
            fix_suggestion=data.get("fix_suggestion", ""),
            fix_code=data.get("fix_code"),
            confidence=data.get("confidence", 0.5),
            supporting_evidence=data.get("supporting_evidence", []),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if isinstance(data.get("created_at"), str)
                else datetime.utcnow()
            ),
        )


@dataclass
class InvestigationReport:
    """The final report from an investigation.

    Contains the root cause, fix, confidence, and full reasoning chain.
    """

    trace_id: str
    status: InvestigationStatus
    root_cause_delta_id: str | None = None
    root_cause_category: ErrorCategory = ErrorCategory.UNKNOWN
    root_cause_explanation: str = ""
    fix_suggestion: str = ""
    fix_code: str | None = None
    confidence: float = 0.0
    reasoning_chain: list[str] = field(default_factory=list)
    similar_cases_found: int = 0
    external_evidence_found: int = 0
    iterations: int = 1
    duration_ms: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "trace_id": self.trace_id,
            "status": self.status.value,
            "root_cause_delta_id": self.root_cause_delta_id,
            "root_cause_category": self.root_cause_category.value,
            "root_cause_explanation": self.root_cause_explanation,
            "fix_suggestion": self.fix_suggestion,
            "fix_code": self.fix_code,
            "confidence": self.confidence,
            "reasoning_chain": self.reasoning_chain,
            "similar_cases_found": self.similar_cases_found,
            "external_evidence_found": self.external_evidence_found,
            "iterations": self.iterations,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at.isoformat(),
        }


class InvestigationState(TypedDict, total=False):
    """The state that flows through the LangGraph investigation workflow.

    This TypedDict defines all fields that can be read/written by nodes.
    Using total=False makes all fields optional, allowing incremental updates.

    Fields are grouped by purpose:
    - Inputs: Provided by the user
    - Graphs: Built from snapshots
    - Analysis: Deltas, memory hits, external evidence
    - Hypotheses: Generated root cause hypotheses
    - Control: Iteration count, confidence threshold
    - Output: Final report
    - Tracing: Event log for observability
    """

    # === Inputs ===
    snapshot_a_dict: dict[str, Any]  # Serialized EnvironmentSnapshot (working)
    snapshot_b_dict: dict[str, Any]  # Serialized EnvironmentSnapshot (failing)
    failure_trace: str  # The error message/stack trace

    # === Triage Results ===
    error_category: str  # ErrorCategory value
    error_type: str  # Extracted error type (e.g., "UnicodeDecodeError")
    triage_summary: str  # Brief classification summary

    # === Graphs ===
    # Note: NetworkX graphs are not directly serializable, so we store node/edge counts
    graph_a_nodes: int
    graph_b_nodes: int
    graphs_built: bool

    # === Analysis ===
    deltas: list[dict[str, Any]]  # Serialized Delta objects
    top_deltas: list[dict[str, Any]]  # Top N most suspicious deltas
    similar_cases: list[dict[str, Any]]  # Serialized SimilarCaseResult
    external_evidence: list[dict[str, Any]]  # Serialized ExternalEvidence
    packages_with_deltas: list[str]  # Package names for targeted search

    # === Hypotheses ===
    hypotheses: list[dict[str, Any]]  # Serialized Hypothesis objects
    selected_hypothesis: dict[str, Any] | None  # The winning hypothesis

    # === Control Flow ===
    confidence: float  # Current confidence score (0.0-1.0)
    confidence_threshold: float  # Minimum required confidence
    iteration: int  # Current iteration number
    max_iterations: int  # Maximum allowed iterations
    should_continue: bool  # Whether to loop back

    # === Output ===
    report: dict[str, Any] | None  # Serialized InvestigationReport
    status: str  # InvestigationStatus value

    # === Tracing ===
    trace_id: str  # Unique ID for this investigation
    events: list[dict[str, Any]]  # Trace events
    start_time: str  # ISO timestamp
    reasoning_chain: list[str]  # Human-readable reasoning steps


def create_initial_state(
    snapshot_a_dict: dict[str, Any],
    snapshot_b_dict: dict[str, Any],
    failure_trace: str,
    confidence_threshold: float = 0.7,
    max_iterations: int = 3,
) -> InvestigationState:
    """Create the initial state for an investigation.

    Args:
        snapshot_a_dict: Serialized working environment snapshot
        snapshot_b_dict: Serialized failing environment snapshot
        failure_trace: The error message/stack trace
        confidence_threshold: Minimum confidence to accept a hypothesis
        max_iterations: Maximum hypothesis iterations

    Returns:
        Initial InvestigationState
    """
    trace_id = str(uuid4())[:12]

    return InvestigationState(
        # Inputs
        snapshot_a_dict=snapshot_a_dict,
        snapshot_b_dict=snapshot_b_dict,
        failure_trace=failure_trace,
        # Triage (to be filled)
        error_category=ErrorCategory.UNKNOWN.value,
        error_type="",
        triage_summary="",
        # Graphs
        graph_a_nodes=0,
        graph_b_nodes=0,
        graphs_built=False,
        # Analysis
        deltas=[],
        top_deltas=[],
        similar_cases=[],
        external_evidence=[],
        packages_with_deltas=[],
        # Hypotheses
        hypotheses=[],
        selected_hypothesis=None,
        # Control
        confidence=0.0,
        confidence_threshold=confidence_threshold,
        iteration=0,
        max_iterations=max_iterations,
        should_continue=True,
        # Output
        report=None,
        status=InvestigationStatus.PENDING.value,
        # Tracing
        trace_id=trace_id,
        events=[],
        start_time=datetime.utcnow().isoformat(),
        reasoning_chain=[],
    )
