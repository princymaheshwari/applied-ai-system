"""Multi-agent orchestrator - LangGraph state machine with observable intermediate states.

This module provides the core investigation workflow for CONFIG DETECTIVE:
    Triage -> Build_Graph -> Diff -> Memory_Recall -> Retrieval ->
    Hypothesize -> Critique -> [loop or finalize] -> Report

Key components:
- State: InvestigationState TypedDict for workflow state
- Trace: Observable events for real-time progress tracking
- Nodes: Individual processing steps (triage, differ, etc.)
- Orchestrator: LangGraph workflow assembly
- Runner: High-level API for running investigations

Usage:
    >>> from config_detective.agents import run_investigation
    >>> report = await run_investigation(
    ...     snapshot_a=working_env.model_dump(),
    ...     snapshot_b=failing_env.model_dump(),
    ...     failure_trace="UnicodeDecodeError: ..."
    ... )
    >>> print(f"Root cause: {report.root_cause_delta_id}")
"""

# State and models
from .state import (
    ErrorCategory,
    Hypothesis,
    InvestigationReport,
    InvestigationState,
    InvestigationStatus,
    create_initial_state,
)

# Tracing
from .trace import (
    EventType,
    NodeTracer,
    TraceEvent,
    TraceStore,
    emit_event,
    get_trace_store,
    reset_trace_store,
)

# Orchestrator
from .orchestrator import (
    create_investigation_graph,
    get_compiled_graph,
    get_graph_visualization,
    reset_compiled_graph,
    run_investigation_async,
    run_investigation_sync,
    stream_investigation,
)

# Runner (main API)
from .runner import (
    format_trace_log,
    get_investigation_trace,
    quick_diagnose,
    run_investigation,
    run_investigation_blocking,
    run_investigation_from_files,
    stream_investigation_events,
)

__all__ = [
    # State
    "ErrorCategory",
    "Hypothesis",
    "InvestigationReport",
    "InvestigationState",
    "InvestigationStatus",
    "create_initial_state",
    # Tracing
    "EventType",
    "NodeTracer",
    "TraceEvent",
    "TraceStore",
    "emit_event",
    "get_trace_store",
    "reset_trace_store",
    # Orchestrator
    "create_investigation_graph",
    "get_compiled_graph",
    "get_graph_visualization",
    "reset_compiled_graph",
    "run_investigation_async",
    "run_investigation_sync",
    "stream_investigation",
    # Runner
    "format_trace_log",
    "get_investigation_trace",
    "quick_diagnose",
    "run_investigation",
    "run_investigation_blocking",
    "run_investigation_from_files",
    "stream_investigation_events",
]
