"""Environment Graph RAG module.

Multi-layer NetworkX graph with typed nodes (PythonPackage, OSPackage, EnvVar,
DockerfileLayer, etc.) and cross-layer edges, plus the differ that extracts
ranked deltas between two snapshots.

Main entry points:
- build_graph(snapshot) -> nx.DiGraph: Convert a snapshot to a graph
- compute_delta(graph_a, graph_b) -> list[Delta]: Find differences between graphs
- find_dependents/find_dependencies: Traverse the graph for impact analysis
"""

from .builder import (
    build_graph,
    get_all_nodes,
    get_node,
    get_nodes_by_type,
)
from .differ import (
    compute_delta,
    filter_deltas,
    get_top_suspects,
    summarize_deltas,
)
from .known_deps import (
    categorize_delta,
    get_os_env_deps,
    get_python_env_deps,
    get_python_os_deps,
)
from .schema import (
    Delta,
    DeltaType,
    Edge,
    EdgeType,
    HIGH_RISK_PATTERNS,
    LOW_RISK_ENV_VARS,
    NODE_TYPE_WEIGHTS,
    Node,
    NodeType,
)
from .traversal import (
    compute_centrality,
    find_all_paths,
    find_critical_nodes,
    find_dependencies,
    find_dependents,
    find_nodes_by_pattern,
    find_nodes_by_type,
    find_path,
    get_connected_components,
    get_edge_type,
    get_node_neighborhood,
)

__all__ = [
    # Schema
    "Node",
    "NodeType",
    "Edge",
    "EdgeType",
    "Delta",
    "DeltaType",
    "NODE_TYPE_WEIGHTS",
    "HIGH_RISK_PATTERNS",
    "LOW_RISK_ENV_VARS",
    # Builder
    "build_graph",
    "get_node",
    "get_all_nodes",
    "get_nodes_by_type",
    # Differ
    "compute_delta",
    "filter_deltas",
    "get_top_suspects",
    "summarize_deltas",
    # Known dependencies
    "get_python_os_deps",
    "get_python_env_deps",
    "get_os_env_deps",
    "categorize_delta",
    # Traversal
    "find_dependents",
    "find_dependencies",
    "find_path",
    "find_all_paths",
    "get_node_neighborhood",
    "find_nodes_by_type",
    "find_nodes_by_pattern",
    "get_edge_type",
    "get_connected_components",
    "compute_centrality",
    "find_critical_nodes",
]
