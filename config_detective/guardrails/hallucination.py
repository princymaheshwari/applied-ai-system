"""Hallucination guard — validates that agent claims are grounded in evidence.

The core invariant: **every root-cause node the agent reports must exist in
the actual delta set.** If the agent claims "the root cause is env:JAVA_HOME"
but JAVA_HOME doesn't appear anywhere in the environment diff, the claim is
fabricated and must be rejected.

This module provides two levels of validation:
1. **Delta existence check** — the claimed delta_id must appear in the deltas
2. **Hypothesis consistency check** — fix_code must reference real config items

When a hallucination is detected, the guard returns a rejection with the
reason, so the orchestrator can loop back or escalate to human review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    """Result of validating a hypothesis against the delta set."""

    is_valid: bool = True
    issues: list[str] = field(default_factory=list)
    hypothesis_id: str = ""
    delta_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "issues": self.issues,
            "hypothesis_id": self.hypothesis_id,
            "delta_id": self.delta_id,
        }


def _normalize_id(node_id: str) -> str:
    """Normalize a node ID for comparison.

    Strips prefixes like "env:", "python_package:", etc. and lowercases.
    """
    parts = node_id.split(":")
    return parts[-1].strip().lower() if parts else node_id.lower()


def _extract_delta_ids(deltas: list[dict[str, Any]]) -> set[str]:
    """Build a set of all delta node IDs (both raw and normalized)."""
    ids: set[str] = set()
    for d in deltas:
        raw = d.get("node_id", "")
        if raw:
            ids.add(raw)
            ids.add(raw.lower())
            ids.add(_normalize_id(raw))
    return ids


def validate_hypothesis(
    hypothesis: dict[str, Any],
    deltas: list[dict[str, Any]],
) -> ValidationResult:
    """Validate a single hypothesis against the delta set.

    Checks:
    1. delta_id exists in the actual deltas
    2. explanation is non-trivial
    3. fix_suggestion is present
    4. fix_code doesn't reference non-existent config items

    Args:
        hypothesis: Serialized Hypothesis dict
        deltas: All deltas from the differ node

    Returns:
        ValidationResult with is_valid and any issues
    """
    issues: list[str] = []
    delta_id = hypothesis.get("delta_id", "")
    hypothesis_id = hypothesis.get("id", "unknown")

    # Check 1: delta_id must exist in deltas
    delta_id_set = _extract_delta_ids(deltas)
    if delta_id and delta_id not in delta_id_set and delta_id.lower() not in delta_id_set and _normalize_id(delta_id) not in delta_id_set:
        issues.append(
            f"HALLUCINATION: Claimed root cause '{delta_id}' does not exist in "
            f"the environment delta set ({len(deltas)} deltas checked)"
        )

    # Check 2: explanation must be non-trivial
    explanation = hypothesis.get("explanation", "")
    if len(explanation.strip()) < 10:
        issues.append("Explanation is missing or too brief to be meaningful")

    # Check 3: fix_suggestion must be present
    fix_suggestion = hypothesis.get("fix_suggestion", "")
    if not fix_suggestion.strip():
        issues.append("No fix suggestion provided")

    # Check 4: fix_code should reference real items
    fix_code = hypothesis.get("fix_code", "") or ""
    if fix_code:
        env_var_refs = re.findall(r"(?:export\s+|ENV\s+)(\w+)\s*=", fix_code, re.IGNORECASE)
        for ref in env_var_refs:
            ref_candidates = [f"env:{ref}", ref, ref.lower()]
            if not any(c in delta_id_set or c.lower() in delta_id_set for c in ref_candidates):
                if ref.upper() not in ("PATH", "HOME", "USER", "SHELL", "TERM", "PWD"):
                    issues.append(
                        f"fix_code references '{ref}' which is not in the delta set"
                    )

    return ValidationResult(
        is_valid=len(issues) == 0,
        issues=issues,
        hypothesis_id=hypothesis_id,
        delta_id=delta_id,
    )


def validate_hypotheses(
    hypotheses: list[dict[str, Any]],
    deltas: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[ValidationResult]]:
    """Validate all hypotheses and separate valid from rejected.

    Args:
        hypotheses: List of serialized Hypothesis dicts
        deltas: All deltas from the differ node

    Returns:
        Tuple of (valid_hypotheses, rejected_hypotheses, all_results)
    """
    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    results: list[ValidationResult] = []

    for h in hypotheses:
        result = validate_hypothesis(h, deltas)
        results.append(result)
        if result.is_valid:
            valid.append(h)
        else:
            rejected.append(h)

    return valid, rejected, results


def validate_report(
    report: dict[str, Any],
    deltas: list[dict[str, Any]],
) -> ValidationResult:
    """Validate a final report before it is emitted.

    Ensures the reported root_cause_delta_id exists in the delta set.

    Args:
        report: Serialized InvestigationReport
        deltas: All deltas

    Returns:
        ValidationResult
    """
    delta_id = report.get("root_cause_delta_id", "")
    issues: list[str] = []

    if delta_id:
        delta_id_set = _extract_delta_ids(deltas)
        if (
            delta_id not in delta_id_set
            and delta_id.lower() not in delta_id_set
            and _normalize_id(delta_id) not in delta_id_set
        ):
            issues.append(
                f"HALLUCINATION: Report claims root cause '{delta_id}' "
                f"which does not exist in the delta set"
            )

    explanation = report.get("root_cause_explanation", "")
    if delta_id and len(explanation.strip()) < 10:
        issues.append("Report root cause explanation is too brief")

    return ValidationResult(
        is_valid=len(issues) == 0,
        issues=issues,
        delta_id=delta_id,
    )
