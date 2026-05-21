"""Runner module - high-level API for running investigations.

This module provides convenient entry points for running CONFIG DETECTIVE
investigations from the CLI, Streamlit UI, or programmatically.

Functions:
- run_investigation: Main entry point for full investigations
- run_investigation_from_files: Load snapshots from files and investigate
- quick_diagnose: Simplified single-snapshot analysis
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator

from .orchestrator import (
    run_investigation_async,
    run_investigation_sync,
    stream_investigation,
)
from .state import (
    create_initial_state,
    ErrorCategory,
    Hypothesis,
    InvestigationReport,
    InvestigationState,
    InvestigationStatus,
)
from .trace import get_trace_store, TraceEvent

logger = logging.getLogger(__name__)


async def run_investigation(
    snapshot_a: dict[str, Any],
    snapshot_b: dict[str, Any],
    failure_trace: str,
    confidence_threshold: float = 0.7,
    max_iterations: int = 3,
) -> InvestigationReport:
    """Run a full investigation comparing two environments.

    This is the main API for CONFIG DETECTIVE. It takes two environment
    snapshots (working and failing) and a failure trace, then orchestrates
    the full investigation workflow.

    Args:
        snapshot_a: Serialized working environment snapshot
        snapshot_b: Serialized failing environment snapshot
        failure_trace: The error message/stack trace
        confidence_threshold: Minimum confidence to accept (default 0.7)
        max_iterations: Maximum hypothesis iterations (default 3)

    Returns:
        InvestigationReport with findings

    Example:
        >>> report = await run_investigation(
        ...     snapshot_a=working_env.model_dump(),
        ...     snapshot_b=failing_env.model_dump(),
        ...     failure_trace="UnicodeDecodeError: 'ascii' codec..."
        ... )
        >>> print(f"Root cause: {report.root_cause_delta_id}")
        >>> print(f"Fix: {report.fix_suggestion}")
    """
    # Create initial state
    initial_state = create_initial_state(
        snapshot_a_dict=snapshot_a,
        snapshot_b_dict=snapshot_b,
        failure_trace=failure_trace,
        confidence_threshold=confidence_threshold,
        max_iterations=max_iterations,
    )

    logger.info(f"Starting investigation {initial_state['trace_id']}")

    # Run the investigation
    final_state = await run_investigation_async(initial_state)

    # Extract report
    report_dict = final_state.get("report", {})
    if report_dict:
        return InvestigationReport(
            trace_id=report_dict.get("trace_id", initial_state["trace_id"]),
            status=InvestigationStatus(report_dict.get("status", "failed")),
            root_cause_delta_id=report_dict.get("root_cause_delta_id"),
            root_cause_category=ErrorCategory(report_dict.get("root_cause_category", "unknown")),
            root_cause_explanation=report_dict.get("root_cause_explanation", ""),
            fix_suggestion=report_dict.get("fix_suggestion", ""),
            fix_code=report_dict.get("fix_code"),
            confidence=report_dict.get("confidence", 0.0),
            reasoning_chain=report_dict.get("reasoning_chain", []),
            similar_cases_found=report_dict.get("similar_cases_found", 0),
            external_evidence_found=report_dict.get("external_evidence_found", 0),
            iterations=report_dict.get("iterations", 1),
            duration_ms=report_dict.get("duration_ms", 0),
        )
    else:
        # Create minimal report from state
        return InvestigationReport(
            trace_id=initial_state["trace_id"],
            status=InvestigationStatus(final_state.get("status", "failed")),
            confidence=final_state.get("confidence", 0.0),
            reasoning_chain=final_state.get("reasoning_chain", []),
        )


def run_investigation_blocking(
    snapshot_a: dict[str, Any],
    snapshot_b: dict[str, Any],
    failure_trace: str,
    confidence_threshold: float = 0.7,
    max_iterations: int = 3,
) -> InvestigationReport:
    """Synchronous wrapper for run_investigation.

    Use this when you need to run an investigation from synchronous code.

    Args:
        snapshot_a: Serialized working environment snapshot
        snapshot_b: Serialized failing environment snapshot
        failure_trace: The error message/stack trace
        confidence_threshold: Minimum confidence to accept
        max_iterations: Maximum hypothesis iterations

    Returns:
        InvestigationReport with findings
    """
    return asyncio.run(run_investigation(
        snapshot_a=snapshot_a,
        snapshot_b=snapshot_b,
        failure_trace=failure_trace,
        confidence_threshold=confidence_threshold,
        max_iterations=max_iterations,
    ))


async def run_investigation_from_files(
    snapshot_a_path: str | Path,
    snapshot_b_path: str | Path,
    failure_trace: str,
    confidence_threshold: float = 0.7,
    max_iterations: int = 3,
) -> InvestigationReport:
    """Run investigation loading snapshots from JSON files.

    Args:
        snapshot_a_path: Path to working environment snapshot JSON
        snapshot_b_path: Path to failing environment snapshot JSON
        failure_trace: The error message/stack trace
        confidence_threshold: Minimum confidence to accept
        max_iterations: Maximum hypothesis iterations

    Returns:
        InvestigationReport with findings

    Raises:
        FileNotFoundError: If snapshot files don't exist
        json.JSONDecodeError: If files are not valid JSON
    """
    snapshot_a_path = Path(snapshot_a_path)
    snapshot_b_path = Path(snapshot_b_path)

    logger.info(f"Loading snapshot A from {snapshot_a_path}")
    with open(snapshot_a_path, "r", encoding="utf-8") as f:
        snapshot_a = json.load(f)

    logger.info(f"Loading snapshot B from {snapshot_b_path}")
    with open(snapshot_b_path, "r", encoding="utf-8") as f:
        snapshot_b = json.load(f)

    return await run_investigation(
        snapshot_a=snapshot_a,
        snapshot_b=snapshot_b,
        failure_trace=failure_trace,
        confidence_threshold=confidence_threshold,
        max_iterations=max_iterations,
    )


async def stream_investigation_events(
    snapshot_a: dict[str, Any],
    snapshot_b: dict[str, Any],
    failure_trace: str,
    confidence_threshold: float = 0.7,
    max_iterations: int = 3,
) -> AsyncIterator[dict[str, Any]]:
    """Stream investigation progress as events.

    Use this for real-time progress updates in UIs.

    Args:
        snapshot_a: Serialized working environment snapshot
        snapshot_b: Serialized failing environment snapshot
        failure_trace: The error message/stack trace
        confidence_threshold: Minimum confidence to accept
        max_iterations: Maximum hypothesis iterations

    Yields:
        Event dictionaries with node name and output
    """
    initial_state = create_initial_state(
        snapshot_a_dict=snapshot_a,
        snapshot_b_dict=snapshot_b,
        failure_trace=failure_trace,
        confidence_threshold=confidence_threshold,
        max_iterations=max_iterations,
    )

    async for event in stream_investigation(initial_state):
        yield event


def get_investigation_trace(trace_id: str) -> list[TraceEvent]:
    """Get all trace events for an investigation.

    Args:
        trace_id: The investigation trace ID

    Returns:
        List of trace events in order
    """
    return get_trace_store().get_events(trace_id)


def format_trace_log(trace_id: str) -> str:
    """Format trace events as a log string.

    Args:
        trace_id: The investigation trace ID

    Returns:
        Formatted log string
    """
    events = get_investigation_trace(trace_id)
    lines = []
    for event in events:
        lines.append(event.format_log())
    return "\n".join(lines)


async def quick_diagnose(
    failure_trace: str,
    current_env: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Quick diagnosis without full snapshot comparison.

    This is a simplified mode that analyzes just the failure trace
    and optionally the current environment to provide quick suggestions.

    Args:
        failure_trace: The error message/stack trace
        current_env: Optional current environment snapshot

    Returns:
        Quick diagnosis results
    """
    from .nodes.triage import triage_node, _classify_error, _extract_error_type

    # Quick classification
    category, pattern, confidence = _classify_error(failure_trace)
    error_type = _extract_error_type(failure_trace)

    # Build quick suggestions based on category
    suggestions = {
        ErrorCategory.LOCALE.value: [
            "Set LANG=C.UTF-8 or LANG=en_US.UTF-8",
            "Set LC_ALL=C.UTF-8",
            "Check PYTHONIOENCODING=utf-8",
        ],
        ErrorCategory.SSL.value: [
            "Update SSL certificates: pip install certifi",
            "Check SSL_CERT_FILE and SSL_CERT_DIR environment variables",
            "Consider PYTHONHTTPSVERIFY=0 for testing (not production)",
        ],
        ErrorCategory.TIMEZONE.value: [
            "Set TZ environment variable",
            "Install tzdata: pip install tzdata",
            "Check system timezone configuration",
        ],
        ErrorCategory.MISSING_PACKAGE.value: [
            "Install missing packages from requirements.txt",
            "Check for missing system dependencies",
            "Ensure virtual environment is activated",
        ],
        ErrorCategory.VERSION_MISMATCH.value: [
            "Check package versions in requirements.txt",
            "Run pip freeze to see current versions",
            "Consider using pip install --upgrade",
        ],
    }

    return {
        "error_type": error_type,
        "category": category.value,
        "confidence": confidence,
        "matched_pattern": pattern,
        "suggestions": suggestions.get(category.value, ["Review error details manually"]),
    }
