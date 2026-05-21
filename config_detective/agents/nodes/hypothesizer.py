"""Hypothesizer node - generates root cause hypotheses.

This node synthesizes information from all previous nodes to generate
k=3 ranked hypotheses about the root cause of the configuration bug.

Each hypothesis includes:
- The suspected delta (root cause)
- An explanation of why it might cause the error
- A proposed fix
- Initial confidence estimate
"""

from __future__ import annotations

from typing import Any

from ..state import ErrorCategory, Hypothesis, InvestigationState
from ..trace import NodeTracer


# Fix templates based on error category and delta type
FIX_TEMPLATES: dict[str, dict[str, str]] = {
    "env_var": {
        ErrorCategory.LOCALE.value: "Set environment variable {node_id} to '{value_a}' (working value)",
        ErrorCategory.SSL.value: "Configure SSL environment: {node_id}='{value_a}'",
        ErrorCategory.TIMEZONE.value: "Set timezone: {node_id}='{value_a}'",
        "default": "Set {node_id}='{value_a}'",
    },
    "python_package": {
        "version_changed": "Pin {pkg_name} to version {value_a}",
        "only_in_a": "Install missing package: pip install {pkg_name}=={value_a}",
        "only_in_b": "Remove conflicting package: pip uninstall {pkg_name}",
        "default": "Update {pkg_name} from {value_b} to {value_a}",
    },
    "os_package": {
        "only_in_a": "Install missing system package: {pkg_name}",
        "default": "Update system package: {pkg_name}",
    },
    "runtime": {
        "default": "Ensure runtime version matches: {value_a}",
    },
}


def _generate_explanation(
    delta: dict[str, Any],
    error_category: str,
    error_type: str,
    similar_cases: list[dict[str, Any]],
    external_evidence: list[dict[str, Any]],
) -> str:
    """Generate an explanation for why a delta might be the root cause.

    Args:
        delta: The suspected delta
        error_category: Classified error category
        error_type: The error type
        similar_cases: Similar past cases
        external_evidence: External evidence

    Returns:
        Explanation string
    """
    node_id = delta.get("node_id", "")
    node_type = delta.get("node_type", "")
    delta_type = delta.get("delta_type", "")
    value_a = delta.get("value_a", "")
    value_b = delta.get("value_b", "")

    parts = []

    # Start with the basic difference
    if delta_type == "version_changed":
        parts.append(f"The version of '{node_id}' changed from {value_a} to {value_b}.")
    elif delta_type == "only_in_a":
        parts.append(f"'{node_id}' is present in the working environment but missing in the failing one.")
    elif delta_type == "only_in_b":
        parts.append(f"'{node_id}' is present in the failing environment but not in the working one.")
    elif delta_type == "value_changed":
        parts.append(f"The value of '{node_id}' changed from '{value_a}' to '{value_b}'.")
    else:
        parts.append(f"Difference detected in '{node_id}': {delta_type}")

    # Connect to error type
    if error_category == ErrorCategory.LOCALE.value and "LANG" in node_id.upper():
        parts.append(f"This locale setting directly affects character encoding, which can cause {error_type}.")
    elif error_category == ErrorCategory.SSL.value and "SSL" in node_id.upper():
        parts.append(f"This SSL configuration affects certificate verification, related to {error_type}.")
    elif error_category == ErrorCategory.PYTHON_VERSION.value and node_type == "runtime":
        parts.append(f"Python version differences can cause syntax or feature compatibility issues.")
    elif node_type == "python_package":
        parts.append(f"Package version mismatches can introduce breaking API changes.")

    # Add evidence from similar cases
    for case in similar_cases[:1]:
        if case.get("root_cause", "").lower() in node_id.lower():
            parts.append(
                f"A similar past case was resolved by addressing '{case.get('root_cause')}'."
            )
            break

    # Add evidence from external sources
    for ev in external_evidence[:2]:
        if node_id.split(":")[-1].lower() in ev.get("title", "").lower():
            parts.append(f"External evidence supports this: '{ev.get('title')[:80]}...'")
            break

    return " ".join(parts)


def _generate_fix(
    delta: dict[str, Any],
    error_category: str,
) -> tuple[str, str | None]:
    """Generate a fix suggestion for a delta.

    Args:
        delta: The suspected delta
        error_category: Classified error category

    Returns:
        Tuple of (fix_suggestion, fix_code)
    """
    node_id = delta.get("node_id", "")
    node_type = delta.get("node_type", "")
    delta_type = delta.get("delta_type", "")
    value_a = delta.get("value_a", "")
    value_b = delta.get("value_b", "")

    # Extract package name from node_id
    pkg_name = node_id.split(":")[-1] if ":" in node_id else node_id

    # Get template
    type_templates = FIX_TEMPLATES.get(node_type, {})
    template = type_templates.get(error_category, type_templates.get(delta_type, type_templates.get("default", "")))

    if not template:
        template = "Align {node_id} with the working environment value"

    # Format template
    suggestion = template.format(
        node_id=node_id,
        pkg_name=pkg_name,
        value_a=value_a,
        value_b=value_b,
    )

    # Generate concrete fix code
    fix_code = None
    if node_type == "env_var":
        fix_code = f"export {node_id.replace('env:', '')}='{value_a}'"
    elif node_type == "python_package" and delta_type == "version_changed":
        fix_code = f"pip install {pkg_name}=={value_a}"
    elif node_type == "python_package" and delta_type == "only_in_a":
        fix_code = f"pip install {pkg_name}=={value_a}"

    return suggestion, fix_code


def _estimate_confidence(
    delta: dict[str, Any],
    error_category: str,
    similar_cases: list[dict[str, Any]],
    external_evidence: list[dict[str, Any]],
) -> float:
    """Estimate confidence for a hypothesis.

    Args:
        delta: The suspected delta
        error_category: Classified error category
        similar_cases: Similar past cases
        external_evidence: External evidence

    Returns:
        Confidence score (0.0-1.0)
    """
    confidence = 0.3  # Base confidence

    # Add delta suspect score (up to +0.2)
    suspect_score = delta.get("suspect_score", 0.5)
    confidence += min(0.2, suspect_score * 0.2)

    # Check if delta type matches error category (up to +0.15)
    node_type = delta.get("node_type", "")
    if error_category == ErrorCategory.LOCALE.value and node_type == "env_var":
        confidence += 0.15
    elif error_category == ErrorCategory.MISSING_PACKAGE.value and node_type == "python_package":
        confidence += 0.15
    elif error_category == ErrorCategory.VERSION_MISMATCH.value and node_type == "python_package":
        confidence += 0.15

    # Boost from similar cases (up to +0.2)
    node_id = delta.get("node_id", "")
    for case in similar_cases:
        if case.get("root_cause", "").lower() in node_id.lower():
            confidence += 0.2
            break
        elif case.get("similarity", 0) > 0.8:
            confidence += 0.1
            break

    # Boost from external evidence (up to +0.15)
    for ev in external_evidence:
        if ev.get("relevance_score", 0) > 0.7:
            confidence += 0.1
            break
        elif ev.get("relevance_score", 0) > 0.5:
            confidence += 0.05
            break

    return min(1.0, confidence)


def hypothesizer_node(state: InvestigationState) -> dict[str, Any]:
    """Generate root cause hypotheses.

    Args:
        state: Current investigation state

    Returns:
        Updated state fields
    """
    trace_id = state.get("trace_id", "unknown")

    with NodeTracer(trace_id, "hypothesizer") as tracer:
        iteration = state.get("iteration", 0) + 1
        tracer.progress(f"Generating hypotheses (iteration {iteration})...")

        # Get context from state
        error_category = state.get("error_category", ErrorCategory.UNKNOWN.value)
        error_type = state.get("error_type", "")
        top_deltas = state.get("top_deltas", [])
        similar_cases = state.get("similar_cases", [])
        external_evidence = state.get("external_evidence", [])

        if not top_deltas:
            tracer.warning("No deltas to generate hypotheses from")
            return {
                "hypotheses": [],
                "iteration": iteration,
                "reasoning_chain": state.get("reasoning_chain", []) + [
                    "Hypothesizer: No deltas available for hypothesis generation"
                ],
            }

        # Generate k=3 hypotheses from top deltas
        k = 3
        hypotheses = []

        for i, delta in enumerate(top_deltas[:k]):
            tracer.progress(f"Generating hypothesis {i + 1}/{k}...")

            # Generate explanation
            explanation = _generate_explanation(
                delta, error_category, error_type, similar_cases, external_evidence
            )

            # Generate fix
            fix_suggestion, fix_code = _generate_fix(delta, error_category)

            # Estimate confidence
            confidence = _estimate_confidence(
                delta, error_category, similar_cases, external_evidence
            )

            # Build supporting evidence list
            supporting = []
            supporting.append(f"Suspect score: {delta.get('suspect_score', 0):.2f}")
            for case in similar_cases[:1]:
                if case.get("similarity", 0) > 0.5:
                    supporting.append(f"Similar case: {case.get('root_cause', 'N/A')}")
            for ev in external_evidence[:1]:
                if delta.get("node_id", "") in str(ev):
                    supporting.append(f"External: {ev.get('source', 'N/A')}")

            hypothesis = Hypothesis(
                rank=i + 1,
                delta_id=delta.get("node_id", ""),
                delta_type=delta.get("delta_type", ""),
                explanation=explanation,
                fix_suggestion=fix_suggestion,
                fix_code=fix_code,
                confidence=confidence,
                supporting_evidence=supporting,
            )
            hypotheses.append(hypothesis)

        # Sort by confidence
        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        for i, h in enumerate(hypotheses):
            h.rank = i + 1

        tracer.set_result({
            "hypotheses_generated": len(hypotheses),
            "top_confidence": hypotheses[0].confidence if hypotheses else 0,
        })

        # Serialize hypotheses
        hypotheses_serialized = [h.to_dict() for h in hypotheses]

        # Build reasoning
        if hypotheses:
            top = hypotheses[0]
            reasoning = [
                f"Hypothesizer: Generated {len(hypotheses)} hypotheses. "
                f"Top hypothesis: '{top.delta_id}' (confidence: {top.confidence:.0%})"
            ]
        else:
            reasoning = ["Hypothesizer: Could not generate hypotheses"]

        return {
            "hypotheses": hypotheses_serialized,
            "iteration": iteration,
            "reasoning_chain": state.get("reasoning_chain", []) + reasoning,
        }
