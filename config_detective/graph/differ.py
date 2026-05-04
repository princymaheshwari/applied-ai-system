"""Graph differ module.

This module computes the differences (deltas) between two environment graphs
and assigns suspect scores to each delta based on how likely it is to be
the root cause of a configuration-related bug.
"""

from __future__ import annotations

import networkx as nx

from .builder import get_node
from .known_deps import categorize_delta
from .schema import (
    Delta,
    DeltaType,
    HIGH_RISK_PATTERNS,
    LOW_RISK_ENV_VARS,
    NODE_TYPE_WEIGHTS,
    Node,
    NodeType,
)


def compute_delta(graph_a: nx.DiGraph, graph_b: nx.DiGraph) -> list[Delta]:
    """Compute the differences between two environment graphs.

    Compares graph_a (typically the "working" environment) with graph_b
    (typically the "broken" environment) and returns a ranked list of
    differences.

    Args:
        graph_a: The first environment graph (reference/working)
        graph_b: The second environment graph (target/broken)

    Returns:
        A list of Delta objects, sorted by suspect_score (highest first)
    """
    deltas: list[Delta] = []

    nodes_a = set(graph_a.nodes())
    nodes_b = set(graph_b.nodes())

    # Find nodes only in A
    only_in_a = nodes_a - nodes_b
    for node_id in only_in_a:
        node = get_node(graph_a, node_id)
        if node:
            delta = _create_delta(
                node=node,
                delta_type=DeltaType.ONLY_IN_A,
                value_a=node.version or node.value,
                value_b=None,
                graph_a=graph_a,
                graph_b=graph_b,
            )
            deltas.append(delta)

    # Find nodes only in B
    only_in_b = nodes_b - nodes_a
    for node_id in only_in_b:
        node = get_node(graph_b, node_id)
        if node:
            delta = _create_delta(
                node=node,
                delta_type=DeltaType.ONLY_IN_B,
                value_a=None,
                value_b=node.version or node.value,
                graph_a=graph_a,
                graph_b=graph_b,
            )
            deltas.append(delta)

    # Find nodes that exist in both but have changed
    common_nodes = nodes_a & nodes_b
    for node_id in common_nodes:
        node_a = get_node(graph_a, node_id)
        node_b = get_node(graph_b, node_id)

        if not node_a or not node_b:
            continue

        # Check for version changes
        if node_a.version != node_b.version and (node_a.version or node_b.version):
            delta = _create_delta(
                node=node_a,
                delta_type=DeltaType.VERSION_CHANGED,
                value_a=node_a.version,
                value_b=node_b.version,
                graph_a=graph_a,
                graph_b=graph_b,
            )
            deltas.append(delta)

        # Check for value changes (env vars, locale, etc.)
        elif node_a.value != node_b.value and (node_a.value or node_b.value):
            delta = _create_delta(
                node=node_a,
                delta_type=DeltaType.VALUE_CHANGED,
                value_a=node_a.value,
                value_b=node_b.value,
                graph_a=graph_a,
                graph_b=graph_b,
            )
            deltas.append(delta)

    # Sort by suspect score (highest first)
    deltas.sort()

    return deltas


def _create_delta(
    node: Node,
    delta_type: DeltaType,
    value_a: str | None,
    value_b: str | None,
    graph_a: nx.DiGraph,
    graph_b: nx.DiGraph,
) -> Delta:
    """Create a Delta object with computed suspect score and impact analysis."""
    # Get nodes that depend on this node (in either graph)
    impacted_by = _find_dependents(node.node_id, graph_a) | _find_dependents(
        node.node_id, graph_b
    )

    # Get nodes that this node depends on
    impacts = _find_dependencies(node.node_id, graph_a) | _find_dependencies(
        node.node_id, graph_b
    )

    # Compute suspect score
    suspect_score = _compute_suspect_score(
        node=node,
        delta_type=delta_type,
        impacted_count=len(impacted_by),
    )

    # Categorize the delta
    category = categorize_delta(node.name)

    return Delta(
        node_id=node.node_id,
        node_type=node.node_type,
        delta_type=delta_type,
        name=node.name,
        value_a=value_a,
        value_b=value_b,
        suspect_score=suspect_score,
        impacted_by=sorted(impacted_by),
        impacts=sorted(impacts),
        category=category,
    )


def _compute_suspect_score(
    node: Node,
    delta_type: DeltaType,
    impacted_count: int,
) -> float:
    """Compute a suspect score for a delta.

    The score is 0.0-1.0, where higher means more likely to be the root cause.

    Factors:
    - Node type weight (OS packages score higher than env vars)
    - High-risk pattern matching (ssl, locale, etc.)
    - Cross-layer impact (more dependents = higher score)
    - Low-risk env var penalty (PATH, HOME, etc.)
    - Delta type (only_in_a/b scores higher than value_changed)
    """
    # Start with base weight for node type
    base_score = NODE_TYPE_WEIGHTS.get(node.node_type, 0.5)

    # High-risk pattern bonus
    name_lower = node.name.lower()
    for pattern in HIGH_RISK_PATTERNS:
        if pattern in name_lower:
            base_score = min(1.0, base_score + 0.15)
            break

    # Low-risk env var penalty
    if node.node_type == NodeType.ENV_VAR and node.name.upper() in LOW_RISK_ENV_VARS:
        base_score = max(0.1, base_score - 0.3)

    # Cross-layer impact bonus (more dependents = more suspicious)
    if impacted_count > 0:
        impact_bonus = min(0.2, impacted_count * 0.05)
        base_score = min(1.0, base_score + impact_bonus)

    # Delta type adjustment
    if delta_type in (DeltaType.ONLY_IN_A, DeltaType.ONLY_IN_B):
        # Missing packages are more suspicious than version changes
        base_score = min(1.0, base_score + 0.05)
    elif delta_type == DeltaType.VERSION_CHANGED:
        # Version changes in packages are quite suspicious
        base_score = min(1.0, base_score + 0.03)

    # Clamp to valid range
    return max(0.0, min(1.0, base_score))


def _find_dependents(node_id: str, graph: nx.DiGraph) -> set[str]:
    """Find all nodes that depend on the given node (incoming edges)."""
    if not graph.has_node(node_id):
        return set()

    # Predecessors = nodes that have edges pointing TO this node
    # i.e., nodes that depend on this node
    return set(graph.predecessors(node_id))


def _find_dependencies(node_id: str, graph: nx.DiGraph) -> set[str]:
    """Find all nodes that the given node depends on (outgoing edges)."""
    if not graph.has_node(node_id):
        return set()

    # Successors = nodes that this node has edges pointing TO
    # i.e., nodes that this node depends on
    return set(graph.successors(node_id))


def summarize_deltas(deltas: list[Delta]) -> dict[str, int]:
    """Summarize deltas by category.

    Returns a dict of category -> count for quick overview.
    """
    summary: dict[str, int] = {}
    for delta in deltas:
        cat = delta.category or "other"
        summary[cat] = summary.get(cat, 0) + 1
    return summary


def filter_deltas(
    deltas: list[Delta],
    min_score: float = 0.0,
    node_types: list[NodeType] | None = None,
    categories: list[str] | None = None,
) -> list[Delta]:
    """Filter deltas by score, type, or category."""
    result = []
    for delta in deltas:
        if delta.suspect_score < min_score:
            continue
        if node_types and delta.node_type not in node_types:
            continue
        if categories and delta.category not in categories:
            continue
        result.append(delta)
    return result


def get_top_suspects(deltas: list[Delta], n: int = 5) -> list[Delta]:
    """Get the top N most suspicious deltas."""
    return sorted(deltas)[:n]
