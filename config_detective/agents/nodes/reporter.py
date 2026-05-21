"""Reporter node - generates the final investigation report.

This node compiles all findings into a comprehensive report including:
- Root cause identification
- Fix recommendation
- Confidence level
- Full reasoning chain
- Supporting evidence
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from ..state import (
    ErrorCategory,
    InvestigationReport,
    InvestigationState,
    InvestigationStatus,
)
from ..trace import NodeTracer


def _format_report_markdown(
    report: InvestigationReport,
    hypothesis: dict[str, Any] | None,
    reasoning_chain: list[str],
    similar_cases: list[dict[str, Any]],
    external_evidence: list[dict[str, Any]],
) -> str:
    """Format the report as markdown.

    Args:
        report: The investigation report
        hypothesis: The selected hypothesis
        reasoning_chain: Full reasoning chain
        similar_cases: Similar past cases
        external_evidence: External evidence

    Returns:
        Markdown-formatted report
    """
    lines = []
    lines.append("# CONFIG DETECTIVE Investigation Report")
    lines.append(f"\n**Trace ID:** {report.trace_id}")
    lines.append(f"**Status:** {report.status.value}")
    lines.append(f"**Generated:** {report.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"**Duration:** {report.duration_ms}ms")

    lines.append("\n## Root Cause Analysis")
    lines.append(f"\n**Category:** {report.root_cause_category.value}")
    lines.append(f"**Confidence:** {report.confidence:.0%}")

    if report.root_cause_delta_id:
        lines.append(f"\n### Identified Root Cause")
        lines.append(f"\n`{report.root_cause_delta_id}`")
        lines.append(f"\n{report.root_cause_explanation}")

    lines.append("\n## Recommended Fix")
    if report.fix_suggestion:
        lines.append(f"\n{report.fix_suggestion}")
        if report.fix_code:
            lines.append(f"\n```bash\n{report.fix_code}\n```")
    else:
        lines.append("\nNo fix recommendation available. Human review recommended.")

    # Reasoning chain
    if reasoning_chain:
        lines.append("\n## Investigation Steps")
        for i, step in enumerate(reasoning_chain, 1):
            lines.append(f"\n{i}. {step}")

    # Supporting evidence
    if similar_cases or external_evidence:
        lines.append("\n## Supporting Evidence")

        if similar_cases:
            lines.append("\n### Similar Past Cases")
            for case in similar_cases[:3]:
                lines.append(f"\n- **Case:** {case.get('case_id', 'N/A')[:8]}...")
                lines.append(f"  - Root cause: {case.get('root_cause', 'N/A')}")
                lines.append(f"  - Fix: {case.get('fix_applied', 'N/A')}")
                lines.append(f"  - Similarity: {case.get('similarity', 0):.0%}")

        if external_evidence:
            lines.append("\n### External References")
            for ev in external_evidence[:5]:
                lines.append(f"\n- [{ev.get('title', 'N/A')[:60]}...]({ev.get('url', '#')})")
                lines.append(f"  - Source: {ev.get('source', 'N/A')}")
                lines.append(f"  - Relevance: {ev.get('relevance_score', 0):.0%}")

    # Summary stats
    lines.append("\n## Summary")
    lines.append(f"\n- **Iterations:** {report.iterations}")
    lines.append(f"- **Similar cases found:** {report.similar_cases_found}")
    lines.append(f"- **External evidence found:** {report.external_evidence_found}")

    if report.status == InvestigationStatus.NEEDS_HUMAN_REVIEW:
        lines.append("\n---")
        lines.append("\n**Note:** This investigation requires human review. "
                    "The confidence level is below the threshold, or validation failed.")

    return "\n".join(lines)


def reporter_node(state: InvestigationState) -> dict[str, Any]:
    """Generate the final investigation report.

    Args:
        state: Current investigation state

    Returns:
        Updated state fields
    """
    trace_id = state.get("trace_id", "unknown")

    with NodeTracer(trace_id, "reporter") as tracer:
        tracer.progress("Compiling final report...")

        # Gather data from state
        selected_hypothesis = state.get("selected_hypothesis")
        confidence = state.get("confidence", 0.0)
        error_category = state.get("error_category", ErrorCategory.UNKNOWN.value)
        similar_cases = state.get("similar_cases", [])
        external_evidence = state.get("external_evidence", [])
        reasoning_chain = state.get("reasoning_chain", [])
        iteration = state.get("iteration", 1)
        start_time = state.get("start_time", "")

        # Calculate duration
        duration_ms = 0
        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time)
                duration_ms = int((datetime.utcnow() - start_dt).total_seconds() * 1000)
            except ValueError:
                pass

        # Determine status
        if confidence >= state.get("confidence_threshold", 0.7):
            status = InvestigationStatus.COMPLETED
        elif selected_hypothesis:
            status = InvestigationStatus.NEEDS_HUMAN_REVIEW
        else:
            status = InvestigationStatus.FAILED

        # Build report
        report = InvestigationReport(
            trace_id=trace_id,
            status=status,
            root_cause_delta_id=selected_hypothesis.get("delta_id") if selected_hypothesis else None,
            root_cause_category=ErrorCategory(error_category) if error_category in [e.value for e in ErrorCategory] else ErrorCategory.UNKNOWN,
            root_cause_explanation=selected_hypothesis.get("explanation", "") if selected_hypothesis else "",
            fix_suggestion=selected_hypothesis.get("fix_suggestion", "") if selected_hypothesis else "",
            fix_code=selected_hypothesis.get("fix_code") if selected_hypothesis else None,
            confidence=confidence,
            reasoning_chain=reasoning_chain,
            similar_cases_found=len(similar_cases),
            external_evidence_found=len(external_evidence),
            iterations=iteration,
            duration_ms=duration_ms,
        )

        tracer.progress("Formatting report...")

        # Generate markdown report
        report_markdown = _format_report_markdown(
            report, selected_hypothesis, reasoning_chain, similar_cases, external_evidence
        )

        tracer.set_result({
            "status": status.value,
            "confidence": confidence,
            "duration_ms": duration_ms,
        })

        # Final reasoning entry
        final_reasoning = [
            f"Reporter: Investigation complete. Status: {status.value}, "
            f"Confidence: {confidence:.0%}, Duration: {duration_ms}ms"
        ]

        return {
            "report": report.to_dict(),
            "status": status.value,
            "reasoning_chain": reasoning_chain + final_reasoning,
        }
