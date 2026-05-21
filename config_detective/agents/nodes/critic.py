"""Critic node - validates hypotheses and scores confidence.

This node examines the generated hypotheses and performs validation:
- Checks that the hypothesized root cause exists in the delta set
- Validates the fix suggestion makes sense
- Computes final confidence score
- Decides whether to continue iterating or finalize

If confidence < threshold, the system either loops or escalates.
"""

from __future__ import annotations

from typing import Any

from ..state import Hypothesis, InvestigationState, InvestigationStatus
from ..trace import NodeTracer


def _validate_hypothesis(
    hypothesis: dict[str, Any],
    deltas: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    """Validate a hypothesis against the delta set.

    Args:
        hypothesis: The hypothesis to validate
        deltas: All deltas from the diff

    Returns:
        Tuple of (is_valid, validation_issues)
    """
    issues = []
    delta_id = hypothesis.get("delta_id", "")

    # Check that delta_id exists in deltas
    delta_ids = {d.get("node_id", "") for d in deltas}
    if delta_id not in delta_ids:
        issues.append(f"Delta '{delta_id}' not found in environment diff")

    # Check that fix_suggestion is non-empty
    if not hypothesis.get("fix_suggestion"):
        issues.append("Missing fix suggestion")

    # Check that explanation is reasonable
    explanation = hypothesis.get("explanation", "")
    if len(explanation) < 20:
        issues.append("Explanation too brief")

    return len(issues) == 0, issues


def _compute_final_confidence(
    hypothesis: dict[str, Any],
    deltas: list[dict[str, Any]],
    similar_cases: list[dict[str, Any]],
    external_evidence: list[dict[str, Any]],
    error_category: str,
) -> float:
    """Compute final confidence score with validation adjustments.

    Scoring breakdown:
    - Delta suspect score: 30%
    - Memory hit (similar past case): 25%
    - External evidence: 25%
    - Error-delta correlation: 20%

    Args:
        hypothesis: The hypothesis
        deltas: All deltas
        similar_cases: Similar past cases
        external_evidence: External evidence
        error_category: The error category

    Returns:
        Final confidence (0.0-1.0)
    """
    confidence = 0.0
    delta_id = hypothesis.get("delta_id", "")

    # 1. Delta suspect score (30%)
    for delta in deltas:
        if delta.get("node_id") == delta_id:
            suspect_score = delta.get("suspect_score", 0.5)
            confidence += suspect_score * 0.30
            break

    # 2. Memory hit (25%)
    for case in similar_cases:
        root_cause = case.get("root_cause", "").lower()
        if delta_id.lower() in root_cause or root_cause in delta_id.lower():
            similarity = case.get("similarity", 0.5)
            confidence += similarity * 0.25
            break
    else:
        # Partial credit if we have any similar cases
        if similar_cases:
            best_similarity = max(c.get("similarity", 0) for c in similar_cases)
            confidence += best_similarity * 0.10

    # 3. External evidence (25%)
    relevant_evidence = []
    for ev in external_evidence:
        # Check if evidence mentions the delta
        title = ev.get("title", "").lower()
        content = ev.get("content", "").lower()
        delta_key = delta_id.split(":")[-1].lower() if ":" in delta_id else delta_id.lower()

        if delta_key in title or delta_key in content:
            relevant_evidence.append(ev)

    if relevant_evidence:
        best_relevance = max(e.get("relevance_score", 0.5) for e in relevant_evidence)
        confidence += best_relevance * 0.25
    elif external_evidence:
        # Partial credit for having evidence
        confidence += 0.05

    # 4. Error-delta correlation (20%)
    node_type = ""
    for delta in deltas:
        if delta.get("node_id") == delta_id:
            node_type = delta.get("node_type", "")
            break

    correlation_score = 0.5  # Base
    if error_category == "locale" and node_type == "env_var":
        correlation_score = 0.9
    elif error_category == "ssl" and node_type in ("env_var", "python_package"):
        correlation_score = 0.85
    elif error_category == "missing_package" and node_type == "python_package":
        correlation_score = 0.9
    elif error_category == "version_mismatch" and node_type == "python_package":
        correlation_score = 0.9

    confidence += correlation_score * 0.20

    return min(1.0, confidence)


def critic_node(state: InvestigationState) -> dict[str, Any]:
    """Validate hypotheses and decide next action.

    Args:
        state: Current investigation state

    Returns:
        Updated state fields
    """
    trace_id = state.get("trace_id", "unknown")

    with NodeTracer(trace_id, "critic") as tracer:
        tracer.progress("Validating hypotheses...")

        hypotheses = state.get("hypotheses", [])
        deltas = state.get("deltas", [])
        similar_cases = state.get("similar_cases", [])
        external_evidence = state.get("external_evidence", [])
        error_category = state.get("error_category", "unknown")
        confidence_threshold = state.get("confidence_threshold", 0.7)
        iteration = state.get("iteration", 1)
        max_iterations = state.get("max_iterations", 3)

        if not hypotheses:
            tracer.warning("No hypotheses to validate")
            return {
                "confidence": 0.0,
                "should_continue": False,
                "status": InvestigationStatus.NEEDS_HUMAN_REVIEW.value,
                "reasoning_chain": state.get("reasoning_chain", []) + [
                    "Critic: No hypotheses to validate - human review needed"
                ],
            }

        # Validate each hypothesis
        valid_hypotheses = []
        for h in hypotheses:
            is_valid, issues = _validate_hypothesis(h, deltas)
            if is_valid:
                valid_hypotheses.append(h)
                tracer.progress(f"Hypothesis '{h.get('delta_id')}' validated")
            else:
                tracer.warning(f"Hypothesis '{h.get('delta_id')}' invalid: {issues}")

        if not valid_hypotheses:
            tracer.warning("All hypotheses failed validation")
            return {
                "confidence": 0.0,
                "should_continue": iteration < max_iterations,
                "status": InvestigationStatus.IN_PROGRESS.value if iteration < max_iterations else InvestigationStatus.NEEDS_HUMAN_REVIEW.value,
                "reasoning_chain": state.get("reasoning_chain", []) + [
                    f"Critic: All hypotheses failed validation (iteration {iteration}/{max_iterations})"
                ],
            }

        # Score each valid hypothesis
        tracer.progress("Computing final confidence scores...")
        scored_hypotheses = []
        for h in valid_hypotheses:
            final_confidence = _compute_final_confidence(
                h, deltas, similar_cases, external_evidence, error_category
            )
            h_copy = dict(h)
            h_copy["confidence"] = final_confidence
            scored_hypotheses.append(h_copy)

        # Sort by confidence
        scored_hypotheses.sort(key=lambda x: x.get("confidence", 0), reverse=True)

        # Select best hypothesis
        best = scored_hypotheses[0]
        best_confidence = best.get("confidence", 0)

        tracer.progress(f"Best hypothesis: '{best.get('delta_id')}' with confidence {best_confidence:.0%}")

        # Decide whether to continue
        should_continue = best_confidence < confidence_threshold and iteration < max_iterations

        if should_continue:
            status = InvestigationStatus.IN_PROGRESS.value
            reasoning = [
                f"Critic: Best confidence {best_confidence:.0%} below threshold {confidence_threshold:.0%}. "
                f"Iteration {iteration}/{max_iterations} - continuing..."
            ]
        elif best_confidence >= confidence_threshold:
            status = InvestigationStatus.IN_PROGRESS.value
            reasoning = [
                f"Critic: Confidence {best_confidence:.0%} meets threshold. "
                f"Selected root cause: '{best.get('delta_id')}'"
            ]
        else:
            status = InvestigationStatus.NEEDS_HUMAN_REVIEW.value
            reasoning = [
                f"Critic: Max iterations reached with confidence {best_confidence:.0%}. "
                f"Human review recommended."
            ]

        tracer.set_result({
            "best_hypothesis": best.get("delta_id"),
            "confidence": best_confidence,
            "should_continue": should_continue,
        })

        return {
            "hypotheses": scored_hypotheses,
            "selected_hypothesis": best,
            "confidence": best_confidence,
            "should_continue": should_continue,
            "status": status,
            "reasoning_chain": state.get("reasoning_chain", []) + reasoning,
        }
