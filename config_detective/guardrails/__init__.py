"""Guardrails — PII scrubber, hallucination guard, refusal patterns, and resource caps.

This module protects CONFIG DETECTIVE from three classes of failure:

1. **PII/secret leakage** — scrubs API keys, tokens, passwords, JWTs, and
   other secrets from text before it is sent to an LLM or stored in memory.

2. **Hallucination** — validates that every claimed root-cause node exists
   in the actual delta set. Rejects fabricated config items.

3. **Resource abuse** — enforces hard iteration caps, wall-clock time limits,
   and blocks dangerous shell commands (refusal patterns).

Usage:
    from config_detective.guardrails import (
        scrub_text,
        validate_hypotheses,
        check_all_limits,
        run_guardrails,
    )

    # Scrub secrets from text before LLM call
    clean, redactions = scrub_text(user_message)

    # Validate hypotheses against deltas
    valid, rejected, results = validate_hypotheses(hypotheses, deltas)

    # Check resource caps
    limit_result = check_all_limits(state)
    if not limit_result.within_limits:
        print(f"Blocked: {limit_result.reason}")
"""

from .hallucination import (
    ValidationResult,
    validate_hypothesis,
    validate_hypotheses,
    validate_report,
)
from .limits import (
    LimitCheckResult,
    LimitsConfig,
    check_all_limits,
    check_iteration_limit,
    check_refusal_patterns,
    check_time_limit,
)
from .pii import (
    scrub_dict,
    scrub_text,
)

from typing import Any


def run_guardrails(
    state: dict[str, Any],
    config: LimitsConfig | None = None,
) -> dict[str, Any]:
    """Run all guardrails on the current investigation state.

    This is the single entry point that the orchestrator calls. It:
    1. Checks resource limits (iteration, time, refusal patterns)
    2. Validates hypotheses against the delta set (hallucination guard)
    3. Scrubs the failure trace for PII before it flows further

    Args:
        state: Current InvestigationState dict
        config: Resource limits config

    Returns:
        Dict of state updates to merge. Contains:
        - guardrails_passed: bool
        - guardrails_issues: list of issue descriptions
        - hypotheses: filtered to only valid ones (if hallucinations found)
        - failure_trace: scrubbed version
    """
    issues: list[str] = []
    updates: dict[str, Any] = {}

    # --- 1. Resource limits ---
    limit_result = check_all_limits(state, config)
    if not limit_result.within_limits:
        issues.append(limit_result.reason)
        updates["should_continue"] = False

    # --- 2. Hallucination guard ---
    hypotheses = state.get("hypotheses", [])
    deltas = state.get("deltas", [])

    if hypotheses and deltas:
        valid, rejected, results = validate_hypotheses(hypotheses, deltas)
        if rejected:
            for r in results:
                if not r.is_valid:
                    issues.extend(r.issues)
            updates["hypotheses"] = valid

    # --- 3. PII scrub on failure trace ---
    failure_trace = state.get("failure_trace", "")
    if failure_trace:
        scrubbed, redactions = scrub_text(failure_trace)
        if redactions:
            updates["failure_trace"] = scrubbed
            issues.extend(redactions)

    updates["guardrails_passed"] = len(issues) == 0
    updates["guardrails_issues"] = issues

    return updates


__all__ = [
    "LimitCheckResult",
    "LimitsConfig",
    "ValidationResult",
    "check_all_limits",
    "check_iteration_limit",
    "check_refusal_patterns",
    "check_time_limit",
    "run_guardrails",
    "scrub_dict",
    "scrub_text",
    "validate_hypothesis",
    "validate_hypotheses",
    "validate_report",
]
