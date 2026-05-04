"""Graph traversal utilities.

This module provides utilities for traversing the environment graph to
analyze dependencies and impacts. These functions support cross-layer
analysis, allowing you to trace how a change in one layer (e.g., OS package)
affects other layers (e.g., Python packages).
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

import networkx as nx

from .builder import get_node
from .schema import EdgeType, Node, NodeType

if TYPE_CHECKING:
    pass


def find_dependents(
    graph: nx.DiGraph,
    node_id: str,
    max_depth: int | None = None,
) -> list[str]:
    """Find all nodes that depend on the given node (directly or indirectly).

    Traverses the graph backwards to find all predecessors of the given node.
    These are nodes that have edges pointing TO the target node.

    Args:
        graph: The environment graph
        node_id: The node to find dependents of
        max_depth: Maximum traversal depth (None for unlimited)

    Returns:
        List of node IDs that depend on the given node, sorted by distance
    """
    if not graph.has_node(node_id):
        return []

    # BFS to find all predecessors (nodes that depend on this node)
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(node_id, 0)])
    result: list[tuple[str, int]] = []

    while queue:
        current, depth = queue.popleft()

        if current in visited:
            continue
        visited.add(current)

        # Don't add the starting node to results
        if current != node_id:
            result.append((current, depth))

        # Check depth limit
        if max_depth is not None and depth >= max_depth:
            continue

        # Add predecessors to queue
        for pred in graph.predecessors(current):
            if pred not in visited:
                queue.append((pred, depth + 1))

    # Sort by distance (closest first)
    result.sort(key=lambda x: x[1])
    return [nid for nid, _ in result]


def find_dependencies(
    graph: nx.DiGraph,
    node_id: str,
    max_depth: int | None = None,
) -> list[str]:
    """Find all nodes that the given node depends on (directly or indirectly).

    Traverses the graph forwards to find all successors of the given node.
    These are nodes that the target node has edges pointing TO.

    Args:
        graph: The environment graph
        node_id: The node to find dependencies of
        max_depth: Maximum traversal depth (None for unlimited)

    Returns:
        List of node IDs that the given node depends on, sorted by distance
    """
    if not graph.has_node(node_id):
        return []

    # BFS to find all successors (nodes this node depends on)
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(node_id, 0)])
    result: list[tuple[str, int]] = []

    while queue:
        current, depth = queue.popleft()

        if current in visited:
            continue
        visited.add(current)

        # Don't add the starting node to results
        if current != node_id:
            result.append((current, depth))

        # Check depth limit
        if max_depth is not None and depth >= max_depth:
            continue

        # Add successors to queue
        for succ in graph.successors(current):
            if succ not in visited:
                queue.append((succ, depth + 1))

    # Sort by distance (closest first)
    result.sort(key=lambda x: x[1])
    return [nid for nid, _ in result]


def find_path(
    graph: nx.DiGraph,
    source_id: str,
    target_id: str,
) -> list[str] | None:
    """Find the shortest path between two nodes.

    Args:
        graph: The environment graph
        source_id: Starting node ID
        target_id: Target node ID

    Returns:
        List of node IDs from source to target, or None if no path exists
    """
    if not graph.has_node(source_id) or not graph.has_node(target_id):
        return None

    try:
        path = nx.shortest_path(graph, source_id, target_id)
        return list(path)
    except nx.NetworkXNoPath:
        return None


def find_all_paths(
    graph: nx.DiGraph,
    source_id: str,
    target_id: str,
    max_length: int = 5,
) -> list[list[str]]:
    """Find all simple paths between two nodes (up to a maximum length).

    Args:
        graph: The environment graph
        source_id: Starting node ID
        target_id: Target node ID
        max_length: Maximum path length to consider

    Returns:
        List of paths, where each path is a list of node IDs
    """
    if not graph.has_node(source_id) or not graph.has_node(target_id):
        return []

    try:
        paths = list(
            nx.all_simple_paths(graph, source_id, target_id, cutoff=max_length)
        )
        return [list(p) for p in paths]
    except nx.NetworkXError:
        return []


def get_node_neighborhood(
    graph: nx.DiGraph,
    node_id: str,
    radius: int = 1,
) -> dict[str, Node | None]:
    """Get all nodes within a certain distance of the given node.

    Args:
        graph: The environment graph
        node_id: The center node
        radius: Maximum distance from center

    Returns:
        Dict mapping node_id -> Node for all nodes in neighborhood
    """
    if not graph.has_node(node_id):
        return {}

    # Get nodes within radius using undirected view
    undirected = graph.to_undirected()
    neighborhood: dict[str, Node | None] = {}

    # BFS from node_id
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(node_id, 0)])

    while queue:
        current, depth = queue.popleft()

        if current in visited:
            continue
        visited.add(current)

        neighborhood[current] = get_node(graph, current)

        if depth >= radius:
            continue

        for neighbor in undirected.neighbors(current):
            if neighbor not in visited:
                queue.append((neighbor, depth + 1))

    return neighborhood


def find_nodes_by_type(graph: nx.DiGraph, node_type: NodeType) -> list[str]:
    """Find all nodes of a specific type.

    Args:
        graph: The environment graph
        node_type: The type to filter by

    Returns:
        List of node IDs matching the type
    """
    prefix = f"{node_type.value}:"
    return [nid for nid in graph.nodes() if nid.startswith(prefix)]


def find_nodes_by_pattern(graph: nx.DiGraph, pattern: str) -> list[str]:
    """Find all nodes whose name contains the given pattern.

    Args:
        graph: The environment graph
        pattern: Substring to search for (case-insensitive)

    Returns:
        List of matching node IDs
    """
    pattern_lower = pattern.lower()
    return [nid for nid in graph.nodes() if pattern_lower in nid.lower()]


def get_edge_type(graph: nx.DiGraph, source_id: str, target_id: str) -> EdgeType | None:
    """Get the edge type between two nodes.

    Args:
        graph: The environment graph
        source_id: Source node ID
        target_id: Target node ID

    Returns:
        The EdgeType, or None if no edge exists
    """
    if not graph.has_edge(source_id, target_id):
        return None

    edge_data = graph.edges[source_id, target_id].get("data")
    if edge_data:
        return edge_data.edge_type
    return None


def get_connected_components(graph: nx.DiGraph) -> list[set[str]]:
    """Get all weakly connected components in the graph.

    Returns:
        List of sets, where each set contains node IDs in a component
    """
    undirected = graph.to_undirected()
    return [set(comp) for comp in nx.connected_components(undirected)]


def compute_centrality(graph: nx.DiGraph) -> dict[str, float]:
    """Compute degree centrality for all nodes.

    Nodes with higher centrality are more connected and thus more
    "important" in the dependency graph.

    Returns:
        Dict mapping node_id -> centrality score (0.0-1.0)
    """
    return nx.degree_centrality(graph)


def find_critical_nodes(
    graph: nx.DiGraph,
    min_dependents: int = 3,
) -> list[tuple[str, int]]:
    """Find nodes that many other nodes depend on.

    These are "critical" nodes whose changes would have wide impact.

    Args:
        graph: The environment graph
        min_dependents: Minimum number of dependents to be considered critical

    Returns:
        List of (node_id, dependent_count) tuples, sorted by count descending
    """
    critical: list[tuple[str, int]] = []

    for node_id in graph.nodes():
        dependents = find_dependents(graph, node_id, max_depth=2)
        if len(dependents) >= min_dependents:
            critical.append((node_id, len(dependents)))

    # Sort by dependent count (highest first)
    critical.sort(key=lambda x: x[1], reverse=True)
    return critical
