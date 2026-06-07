"""Verifier node — sandbox-verifies the top hypotheses.

This node takes the hypotheses produced by the Hypothesizer and empirically
tests each one by running the candidate fix in a sandboxed environment.
It sits between the Hypothesizer and the Critic in the orchestrator graph.

The node:
1. Converts each hypothesis into a FixCandidate.
2. Calls the sandbox module (Docker preferred, subprocess fallback).
3. Annotates each hypothesis with verification results.
4. Boosts confidence for verified fixes, penalises unverified ones.
"""

from __future__ import annotations

import logging
from typing import Any

from config_detective.sandbox import (
    FixCandidate,
    SandboxBackend,
    VerificationStatus,
    get_sandbox_backend,
    verify_fixes,
)
from ..state import InvestigationState
from ..trace import NodeTracer

logger = logging.getLogger(__name__)


def _hypothesis_to_fix_candidate(hypothesis: dict[str, Any]) -> FixCandidate:
    """Convert a hypothesis dict into a FixCandidate for the sandbox.

    Maps the hypothesis's delta_id and fix_code into concrete sandbox
    instructions (env overrides, pip commands, etc.).
    """
    delta_id = hypothesis.get("delta_id", "")
    fix_code = hypothesis.get("fix_code", "") or ""
    fix_type = hypothesis.get("delta_type", "")

    env_overrides: dict[str, str] = {}
    commands: list[str] = []

    if delta_id.startswith("env:"):
        var_name = delta_id.replace("env:", "")
        if fix_code.startswith("export "):
            parts = fix_code.replace("export ", "").split("=", 1)
            if len(parts) == 2:
                env_overrides[parts[0]] = parts[1].strip("'\"")
        else:
            value = hypothesis.get("fix_suggestion", "")
            env_overrides[var_name] = value

    elif "pip install" in fix_code:
        commands.append(fix_code)

    elif fix_code:
        commands.append(fix_code)

    return FixCandidate(
        delta_id=delta_id,
        fix_type=fix_type,
        description=hypothesis.get("fix_suggestion", ""),
        env_overrides=env_overrides,
        commands=commands,
    )


def _extract_base_image(state: InvestigationState) -> str:
    """Try to extract the Docker base image from the failing snapshot.

    Falls back to a generic Python slim image.
    """
    snap_b = state.get("snapshot_b_dict", {})

    dockerfiles = snap_b.get("dockerfiles", [])
    for df in dockerfiles:
        base = df.get("base_image")
        if base:
            return base

    runtimes = snap_b.get("runtime_versions") or snap_b.get("runtimes", {})
    if isinstance(runtimes, dict):
        py_version = runtimes.get("python", "")
        if py_version:
            major_minor = ".".join(py_version.split(".")[:2])
            return f"python:{major_minor}-slim"

    return "python:3.11-slim"


def _extract_failing_command(state: InvestigationState) -> str:
    """Extract or construct a command that reproduces the failure.

    Uses the failure trace to guess a reasonable reproduction command.
    """
    trace = state.get("failure_trace", "")

    if "pytest" in trace.lower():
        return "pytest --tb=short -x"
    if "python " in trace.lower():
        for line in trace.splitlines():
            stripped = line.strip()
            if stripped.startswith("python ") or stripped.startswith("python3 "):
                return stripped
    if "import" in trace and "Error" in trace:
        for line in trace.splitlines():
            if "import " in line and "Error" not in line:
                module = line.strip().replace("import ", "").split(".")[0]
                return f"python -c 'import {module}'"

    return f"python -c 'print(\"reproduced\")'"


def verifier_node(state: InvestigationState) -> dict[str, Any]:
    """Sandbox-verify the top hypotheses.

    Args:
        state: Current investigation state

    Returns:
        Updated state fields with verification results annotated
        onto each hypothesis.
    """
    trace_id = state.get("trace_id", "unknown")

    with NodeTracer(trace_id, "verifier") as tracer:
        hypotheses = state.get("hypotheses", [])
        failure_trace = state.get("failure_trace", "")

        if not hypotheses:
            tracer.warning("No hypotheses to verify")
            return {
                "reasoning_chain": state.get("reasoning_chain", []) + [
                    "Verifier: No hypotheses to verify — skipping sandbox"
                ],
            }

        backend = get_sandbox_backend()
        tracer.progress(f"Using sandbox backend: {backend.value}")

        base_image = _extract_base_image(state)
        failing_command = _extract_failing_command(state)

        tracer.progress(
            f"Base image: {base_image}, failing command: {failing_command[:80]}"
        )

        # Convert hypotheses to fix candidates
        fixes = []
        for h in hypotheses:
            fix = _hypothesis_to_fix_candidate(h)
            fixes.append(fix)

        tracer.progress(f"Testing {len(fixes)} fix candidate(s) in sandbox...")

        # Run verification
        try:
            results = verify_fixes(
                fixes=fixes,
                failing_command=failing_command,
                failure_trace=failure_trace,
                base_image=base_image,
                force_backend=backend,
            )
        except Exception as exc:
            tracer.error(f"Sandbox verification failed: {exc}")
            return {
                "reasoning_chain": state.get("reasoning_chain", []) + [
                    f"Verifier: Sandbox verification failed: {exc}"
                ],
            }

        # Build a map from fix_id to result for annotation
        result_by_delta: dict[str, dict[str, Any]] = {}
        for r in results:
            result_by_delta[r.delta_id] = r.to_dict()

        # Annotate hypotheses with verification results
        verified_count = 0
        annotated_hypotheses = []
        for h in hypotheses:
            h_copy = dict(h)
            vr = result_by_delta.get(h.get("delta_id", ""))
            if vr:
                h_copy["verification"] = vr
                if vr.get("fix_verified"):
                    verified_count += 1
                    h_copy["confidence"] = min(1.0, h.get("confidence", 0.5) + 0.2)
                    h_copy["supporting_evidence"] = h.get("supporting_evidence", []) + [
                        f"Sandbox verified ({vr.get('backend', 'unknown')}): "
                        f"exit code {vr.get('exit_code_before')} -> {vr.get('exit_code_after')}"
                    ]
                elif vr.get("status") == VerificationStatus.FAILED.value:
                    h_copy["confidence"] = max(0.0, h.get("confidence", 0.5) - 0.15)
            else:
                h_copy["verification"] = None

            annotated_hypotheses.append(h_copy)

        # Re-sort by confidence after adjustment
        annotated_hypotheses.sort(
            key=lambda x: x.get("confidence", 0), reverse=True
        )
        for i, h in enumerate(annotated_hypotheses):
            h["rank"] = i + 1

        tracer.set_result({
            "backend": backend.value,
            "fixes_tested": len(results),
            "fixes_verified": verified_count,
            "base_image": base_image,
        })

        # Build reasoning
        if verified_count:
            best = next(
                (h for h in annotated_hypotheses if h.get("verification", {}).get("fix_verified")),
                annotated_hypotheses[0],
            )
            reasoning = [
                f"Verifier: {verified_count}/{len(results)} fix(es) verified in sandbox "
                f"(backend: {backend.value}). Best verified fix: '{best.get('delta_id')}'"
            ]
        else:
            reasoning = [
                f"Verifier: 0/{len(results)} fixes verified in sandbox "
                f"(backend: {backend.value}). Proceeding with heuristic confidence."
            ]

        return {
            "hypotheses": annotated_hypotheses,
            "reasoning_chain": state.get("reasoning_chain", []) + reasoning,
        }
