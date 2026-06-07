"""Guardrails node — enforces PII scrubbing, hallucination checks, and resource caps.

This node runs after the Verifier and before the Critic. It:
1. Scrubs any secrets from the failure trace before it flows further
2. Validates that all hypotheses reference real deltas (hallucination guard)
3. Checks iteration and time caps
4. Scans fix_code for dangerous refusal patterns

If guardrails reject something, the node updates the state to reflect the
rejection and may force the investigation to stop or escalate.
"""

from __future__ import annotations

import logging
from typing import Any

from config_detective.guardrails import (
    run_guardrails,
    LimitsConfig,
)
from ..state import InvestigationState, InvestigationStatus
from ..trace import NodeTracer

logger = logging.getLogger(__name__)


def guardrails_node(state: InvestigationState) -> dict[str, Any]:
    """Run all guardrails on the current state.

    Args:
        state: Current investigation state

    Returns:
        Updated state fields with guardrails results
    """
    trace_id = state.get("trace_id", "unknown")

    with NodeTracer(trace_id, "guardrails") as tracer:
        tracer.progress("Running guardrails checks...")

        config = LimitsConfig.from_env()

        updates = run_guardrails(state, config)

        passed = updates.get("guardrails_passed", True)
        issues = updates.get("guardrails_issues", [])

        if passed:
            tracer.progress("All guardrails passed")
            reasoning = ["Guardrails: All checks passed"]
        else:
            tracer.warning(f"Guardrails flagged {len(issues)} issue(s)")
            for issue in issues:
                tracer.warning(f"  - {issue}")

            reasoning = [
                f"Guardrails: {len(issues)} issue(s) detected: "
                + "; ".join(issues[:3])
            ]

            hallucination_issues = [i for i in issues if "HALLUCINATION" in i]
            refusal_issues = [i for i in issues if "Refused" in i]
            limit_issues = [i for i in issues if "limit reached" in i.lower()]

            if refusal_issues:
                updates["status"] = InvestigationStatus.FAILED.value
                updates["should_continue"] = False
                reasoning.append(
                    "Guardrails: Investigation blocked due to refusal pattern"
                )

            if limit_issues:
                updates["should_continue"] = False
                reasoning.append(
                    "Guardrails: Resource limit reached — stopping iteration"
                )

            if hallucination_issues:
                remaining = updates.get("hypotheses", state.get("hypotheses", []))
                if not remaining:
                    updates["should_continue"] = False
                    updates["status"] = InvestigationStatus.NEEDS_HUMAN_REVIEW.value
                    reasoning.append(
                        "Guardrails: All hypotheses rejected as hallucinations — human review needed"
                    )

        tracer.set_result({
            "passed": passed,
            "issues_count": len(issues),
        })

        updates["reasoning_chain"] = state.get("reasoning_chain", []) + reasoning

        return updates
