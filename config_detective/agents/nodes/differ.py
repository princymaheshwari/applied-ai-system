"""Differ node - computes and prioritizes deltas between environments.

This node uses the Phase 2 graph/differ module to find all differences
between the working and failing environments, then ranks them by
suspiciousness.
"""

from __future__ import annotations

from typing import Any

from ..state import ErrorCategory, InvestigationState
from ..trace import NodeTracer


# Map error categories to delta types that are more suspicious
CATEGORY_DELTA_WEIGHTS: dict[str, list[str]] = {
    ErrorCategory.LOCALE.value: ["env_var", "locale", "timezone"],
    ErrorCategory.SSL.value: ["env_var", "python_package", "os_package"],
    ErrorCategory.TIMEZONE.value: ["env_var", "timezone", "locale"],
    ErrorCategory.PYTHON_VERSION.value: ["runtime", "python_package"],
    ErrorCategory.MISSING_PACKAGE.value: ["python_package", "os_package"],
    ErrorCategory.VERSION_MISMATCH.value: ["python_package", "os_package"],
    ErrorCategory.ENV_VAR.value: ["env_var"],
}


def differ_node(state: InvestigationState) -> dict[str, Any]:
    """Compute deltas between environment graphs.

    Args:
        state: Current investigation state

    Returns:
        Updated state fields
    """
    trace_id = state.get("trace_id", "unknown")

    with NodeTracer(trace_id, "differ") as tracer:
        tracer.progress("Rebuilding graphs for diff...")

        # Import here to avoid circular imports
        from config_detective.snapshot.models import EnvironmentSnapshot
        from config_detective.graph import build_graph, compute_delta, get_top_suspects

        # Rebuild graphs (they aren't serializable)
        try:
            snapshot_a = EnvironmentSnapshot.model_validate(state.get("snapshot_a_dict", {}))
            snapshot_b = EnvironmentSnapshot.model_validate(state.get("snapshot_b_dict", {}))
            graph_a = build_graph(snapshot_a)
            graph_b = build_graph(snapshot_b)
        except Exception as e:
            tracer.error(f"Failed to rebuild graphs: {e}")
            return {
                "deltas": [],
                "top_deltas": [],
                "reasoning_chain": state.get("reasoning_chain", []) + [
                    f"Differ: Failed to rebuild graphs - {e}"
                ],
            }

        tracer.progress("Computing deltas...")
        deltas = compute_delta(graph_a, graph_b)
        tracer.progress(f"Found {len(deltas)} deltas")

        # Get top suspects
        top_suspects = get_top_suspects(deltas, limit=10)
        tracer.progress(f"Top {len(top_suspects)} suspects identified")

        # Serialize deltas for state
        deltas_serialized = []
        for delta in deltas:
            deltas_serialized.append({
                "node_id": delta.node_id,
                "node_type": delta.node_type.value if hasattr(delta.node_type, "value") else str(delta.node_type),
                "delta_type": delta.delta_type.value if hasattr(delta.delta_type, "value") else str(delta.delta_type),
                "value_a": delta.value_a,
                "value_b": delta.value_b,
                "suspect_score": delta.suspect_score,
            })

        top_deltas_serialized = []
        for delta in top_suspects:
            top_deltas_serialized.append({
                "node_id": delta.node_id,
                "node_type": delta.node_type.value if hasattr(delta.node_type, "value") else str(delta.node_type),
                "delta_type": delta.delta_type.value if hasattr(delta.delta_type, "value") else str(delta.delta_type),
                "value_a": delta.value_a,
                "value_b": delta.value_b,
                "suspect_score": delta.suspect_score,
            })

        # Extract package names for later retrieval
        packages_with_deltas = []
        for delta in deltas:
            node_id = delta.node_id
            if node_id.startswith("pkg:"):
                # Extract package name from "pkg:package_name"
                pkg_name = node_id.split(":", 1)[1] if ":" in node_id else node_id
                packages_with_deltas.append(pkg_name)

        tracer.set_result({
            "total_deltas": len(deltas),
            "top_deltas": len(top_suspects),
            "packages_with_deltas": len(packages_with_deltas),
        })

        # Build reasoning
        if top_suspects:
            top_delta = top_suspects[0]
            reasoning = [
                f"Differ: Found {len(deltas)} deltas. Top suspect: {top_delta.node_id} "
                f"(score: {top_delta.suspect_score:.2f})"
            ]
        else:
            reasoning = [f"Differ: Found {len(deltas)} deltas, no clear suspects"]

        return {
            "deltas": deltas_serialized,
            "top_deltas": top_deltas_serialized,
            "packages_with_deltas": packages_with_deltas,
            "reasoning_chain": state.get("reasoning_chain", []) + reasoning,
        }
