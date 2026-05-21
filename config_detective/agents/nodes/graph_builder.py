"""Graph builder node - constructs environment graphs from snapshots.

This node takes the serialized snapshots from state and builds
NetworkX graphs using the Phase 2 graph module.
"""

from __future__ import annotations

from typing import Any

from ..state import InvestigationState
from ..trace import NodeTracer


def graph_builder_node(state: InvestigationState) -> dict[str, Any]:
    """Build environment graphs from snapshots.

    Args:
        state: Current investigation state

    Returns:
        Updated state fields
    """
    trace_id = state.get("trace_id", "unknown")

    with NodeTracer(trace_id, "builder") as tracer:
        tracer.progress("Loading snapshots...")

        # Import here to avoid circular imports
        from config_detective.snapshot.models import EnvironmentSnapshot
        from config_detective.graph import build_graph

        # Deserialize snapshots
        snap_a_dict = state.get("snapshot_a_dict", {})
        snap_b_dict = state.get("snapshot_b_dict", {})

        try:
            snapshot_a = EnvironmentSnapshot.model_validate(snap_a_dict)
            snapshot_b = EnvironmentSnapshot.model_validate(snap_b_dict)
        except Exception as e:
            tracer.error(f"Failed to parse snapshots: {e}")
            return {
                "graphs_built": False,
                "reasoning_chain": state.get("reasoning_chain", []) + [
                    f"Graph Builder: Failed to parse snapshots - {e}"
                ],
            }

        tracer.progress("Building graph for working environment (A)...")
        graph_a = build_graph(snapshot_a)
        tracer.progress(f"Graph A: {graph_a.number_of_nodes()} nodes, {graph_a.number_of_edges()} edges")

        tracer.progress("Building graph for failing environment (B)...")
        graph_b = build_graph(snapshot_b)
        tracer.progress(f"Graph B: {graph_b.number_of_nodes()} nodes, {graph_b.number_of_edges()} edges")

        tracer.set_result({
            "graph_a_nodes": graph_a.number_of_nodes(),
            "graph_b_nodes": graph_b.number_of_nodes(),
        })

        # Store graph data in state
        # Note: We store node counts since graphs aren't directly serializable
        # The actual graphs will be rebuilt in the differ node

        reasoning = [
            f"Graph Builder: Built graphs - A has {graph_a.number_of_nodes()} nodes, "
            f"B has {graph_b.number_of_nodes()} nodes"
        ]

        return {
            "graph_a_nodes": graph_a.number_of_nodes(),
            "graph_b_nodes": graph_b.number_of_nodes(),
            "graphs_built": True,
            "reasoning_chain": state.get("reasoning_chain", []) + reasoning,
        }
