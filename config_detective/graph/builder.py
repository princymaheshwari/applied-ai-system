"""Graph builder module.

This module builds a NetworkX directed graph from an EnvironmentSnapshot.
The graph represents the environment's configuration as interconnected nodes,
allowing for cross-layer dependency analysis.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import networkx as nx

from .known_deps import get_os_env_deps, get_python_env_deps, get_python_os_deps
from .schema import Edge, EdgeType, Node, NodeType

if TYPE_CHECKING:
    from ..snapshot.models import EnvironmentSnapshot


def build_graph(snapshot: "EnvironmentSnapshot") -> nx.DiGraph:
    """Build a dependency graph from an environment snapshot.

    Creates nodes for all configuration items (packages, env vars, etc.)
    and edges for known dependencies between them.

    Args:
        snapshot: The environment snapshot to convert to a graph

    Returns:
        A NetworkX directed graph with Node objects as node data
        and Edge objects as edge data.
    """
    graph = nx.DiGraph()

    # Add all nodes first
    _add_python_packages(graph, snapshot)
    _add_node_packages(graph, snapshot)
    _add_os_packages(graph, snapshot)
    _add_env_vars(graph, snapshot)
    _add_dockerfiles(graph, snapshot)
    _add_runtime_versions(graph, snapshot)
    _add_locale_settings(graph, snapshot)
    _add_timezone_settings(graph, snapshot)
    _add_system_info(graph, snapshot)

    # Then add edges based on known dependencies
    _add_cross_layer_edges(graph, snapshot)

    return graph


def _add_python_packages(graph: nx.DiGraph, snapshot: "EnvironmentSnapshot") -> None:
    """Add Python package nodes from all lockfiles."""
    for lockfile in snapshot.lockfiles:
        if lockfile.lockfile_type not in (
            "requirements.txt",
            "uv.lock",
            "Pipfile.lock",
            "poetry.lock",
        ):
            continue

        for pkg in lockfile.packages:
            node_id = Node.make_id(NodeType.PYTHON_PACKAGE, pkg.name)

            # Skip if already added (might be in multiple lockfiles)
            if graph.has_node(node_id):
                continue

            node = Node(
                node_id=node_id,
                node_type=NodeType.PYTHON_PACKAGE,
                name=pkg.name,
                version=pkg.version,
                metadata={
                    "source": lockfile.path,
                    "lockfile_type": lockfile.lockfile_type,
                },
            )
            graph.add_node(node_id, data=node)


def _add_node_packages(graph: nx.DiGraph, snapshot: "EnvironmentSnapshot") -> None:
    """Add Node.js package nodes from package-lock.json / yarn.lock."""
    for lockfile in snapshot.lockfiles:
        if lockfile.lockfile_type not in ("package-lock.json", "yarn.lock"):
            continue

        for pkg in lockfile.packages:
            node_id = Node.make_id(NodeType.NODE_PACKAGE, pkg.name)

            if graph.has_node(node_id):
                continue

            node = Node(
                node_id=node_id,
                node_type=NodeType.NODE_PACKAGE,
                name=pkg.name,
                version=pkg.version,
                metadata={
                    "source": lockfile.path,
                    "lockfile_type": lockfile.lockfile_type,
                },
            )
            graph.add_node(node_id, data=node)


def _add_os_packages(graph: nx.DiGraph, snapshot: "EnvironmentSnapshot") -> None:
    """Add OS package nodes."""
    for pkg in snapshot.os_packages:
        node_id = Node.make_id(NodeType.OS_PACKAGE, pkg.name)

        node = Node(
            node_id=node_id,
            node_type=NodeType.OS_PACKAGE,
            name=pkg.name,
            version=pkg.version,
            metadata={
                "architecture": pkg.architecture,
                "description": pkg.description,
                "package_manager": snapshot.os_package_manager.value,
            },
        )
        graph.add_node(node_id, data=node)


def _add_env_vars(graph: nx.DiGraph, snapshot: "EnvironmentSnapshot") -> None:
    """Add environment variable nodes."""
    for env in snapshot.env_vars:
        node_id = Node.make_id(NodeType.ENV_VAR, env.key)

        node = Node(
            node_id=node_id,
            node_type=NodeType.ENV_VAR,
            name=env.key,
            value=env.value if not env.redacted else "[REDACTED]",
            metadata={"redacted": env.redacted},
        )
        graph.add_node(node_id, data=node)


def _add_dockerfiles(graph: nx.DiGraph, snapshot: "EnvironmentSnapshot") -> None:
    """Add Dockerfile instruction nodes."""
    for dockerfile in snapshot.dockerfiles:
        # Add a node for the Dockerfile itself
        df_node_id = Node.make_id(NodeType.LOCKFILE, dockerfile.path)
        df_node = Node(
            node_id=df_node_id,
            node_type=NodeType.LOCKFILE,
            name=dockerfile.path,
            metadata={"base_image": dockerfile.base_image},
        )
        graph.add_node(df_node_id, data=df_node)

        # Add nodes for significant instructions (FROM, RUN, ENV, ARG)
        for i, instr in enumerate(dockerfile.instructions):
            if instr.instruction.upper() not in ("FROM", "RUN", "ENV", "ARG", "COPY"):
                continue

            instr_id = f"docker:{dockerfile.path}:{i}:{instr.instruction}"
            node = Node(
                node_id=instr_id,
                node_type=NodeType.DOCKERFILE_INSTRUCTION,
                name=f"{instr.instruction}",
                value=instr.value,
                metadata={
                    "dockerfile": dockerfile.path,
                    "line": instr.start_line,
                },
            )
            graph.add_node(instr_id, data=node)

            # Link instruction to Dockerfile
            edge = Edge(
                source_id=instr_id,
                target_id=df_node_id,
                edge_type=EdgeType.DEFINED_IN,
            )
            graph.add_edge(instr_id, df_node_id, data=edge)


def _add_runtime_versions(graph: nx.DiGraph, snapshot: "EnvironmentSnapshot") -> None:
    """Add runtime version nodes."""
    rv = snapshot.runtime_versions

    if rv.python:
        node_id = Node.make_id(NodeType.RUNTIME_VERSION, "python")
        node = Node(
            node_id=node_id,
            node_type=NodeType.RUNTIME_VERSION,
            name="python",
            version=rv.python,
            metadata={"implementation": rv.python_implementation},
        )
        graph.add_node(node_id, data=node)

    if rv.node:
        node_id = Node.make_id(NodeType.RUNTIME_VERSION, "node")
        node = Node(
            node_id=node_id,
            node_type=NodeType.RUNTIME_VERSION,
            name="node",
            version=rv.node,
            metadata={"npm_version": rv.npm},
        )
        graph.add_node(node_id, data=node)

    for runtime_name in ("ruby", "go", "rust", "java"):
        version = getattr(rv, runtime_name, None)
        if version:
            node_id = Node.make_id(NodeType.RUNTIME_VERSION, runtime_name)
            node = Node(
                node_id=node_id,
                node_type=NodeType.RUNTIME_VERSION,
                name=runtime_name,
                version=version,
            )
            graph.add_node(node_id, data=node)


def _add_locale_settings(graph: nx.DiGraph, snapshot: "EnvironmentSnapshot") -> None:
    """Add locale setting nodes."""
    loc = snapshot.locale

    if loc.lang:
        node_id = Node.make_id(NodeType.LOCALE_SETTING, "LANG")
        node = Node(
            node_id=node_id,
            node_type=NodeType.LOCALE_SETTING,
            name="LANG",
            value=loc.lang,
        )
        graph.add_node(node_id, data=node)

    if loc.lc_all:
        node_id = Node.make_id(NodeType.LOCALE_SETTING, "LC_ALL")
        node = Node(
            node_id=node_id,
            node_type=NodeType.LOCALE_SETTING,
            name="LC_ALL",
            value=loc.lc_all,
        )
        graph.add_node(node_id, data=node)

    if loc.lc_ctype:
        node_id = Node.make_id(NodeType.LOCALE_SETTING, "LC_CTYPE")
        node = Node(
            node_id=node_id,
            node_type=NodeType.LOCALE_SETTING,
            name="LC_CTYPE",
            value=loc.lc_ctype,
        )
        graph.add_node(node_id, data=node)


def _add_timezone_settings(graph: nx.DiGraph, snapshot: "EnvironmentSnapshot") -> None:
    """Add timezone setting nodes."""
    tz = snapshot.timezone

    # Determine the effective timezone value
    tz_value = tz.tz_env or tz.etc_timezone or tz.etc_localtime_link

    if tz_value:
        node_id = Node.make_id(NodeType.TIMEZONE_SETTING, "TZ")
        node = Node(
            node_id=node_id,
            node_type=NodeType.TIMEZONE_SETTING,
            name="TZ",
            value=tz_value,
            metadata={
                "tz_env": tz.tz_env,
                "etc_timezone": tz.etc_timezone,
                "etc_localtime_link": tz.etc_localtime_link,
            },
        )
        graph.add_node(node_id, data=node)


def _add_system_info(graph: nx.DiGraph, snapshot: "EnvironmentSnapshot") -> None:
    """Add system info nodes."""
    sys = snapshot.system

    # OS type node
    if sys.os_type.value != "unknown":
        node_id = Node.make_id(NodeType.SYSTEM_INFO, "os")
        node = Node(
            node_id=node_id,
            node_type=NodeType.SYSTEM_INFO,
            name="os",
            value=sys.os_type.value,
            metadata={
                "release": sys.os_release,
                "kernel": sys.kernel,
            },
        )
        graph.add_node(node_id, data=node)

    # Architecture node
    if sys.architecture:
        node_id = Node.make_id(NodeType.SYSTEM_INFO, "architecture")
        node = Node(
            node_id=node_id,
            node_type=NodeType.SYSTEM_INFO,
            name="architecture",
            value=sys.architecture,
        )
        graph.add_node(node_id, data=node)

    # Libc node (critical for binary compatibility)
    if sys.libc_type.value != "unknown":
        node_id = Node.make_id(NodeType.SYSTEM_INFO, "libc")
        node = Node(
            node_id=node_id,
            node_type=NodeType.SYSTEM_INFO,
            name="libc",
            value=sys.libc_type.value,
            version=sys.libc_version,
        )
        graph.add_node(node_id, data=node)


def _add_cross_layer_edges(graph: nx.DiGraph, snapshot: "EnvironmentSnapshot") -> None:
    """Add edges between nodes based on known dependencies.

    This is where the graph becomes powerful - connecting Python packages
    to the OS packages they require, and both to the env vars that configure them.
    """
    # Collect all node IDs by type for faster lookup
    python_packages = [
        nid for nid in graph.nodes() if nid.startswith(f"{NodeType.PYTHON_PACKAGE.value}:")
    ]
    os_packages = [
        nid for nid in graph.nodes() if nid.startswith(f"{NodeType.OS_PACKAGE.value}:")
    ]
    env_vars = [
        nid for nid in graph.nodes() if nid.startswith(f"{NodeType.ENV_VAR.value}:")
    ]

    # Build lookup dicts
    os_pkg_lookup = {nid.split(":", 1)[1].lower(): nid for nid in os_packages}
    env_var_lookup = {nid.split(":", 1)[1].upper(): nid for nid in env_vars}

    # Connect Python packages to OS packages they require
    for py_node_id in python_packages:
        pkg_name = py_node_id.split(":", 1)[1]
        os_deps = get_python_os_deps(pkg_name)

        for os_dep in os_deps:
            # Try to find a matching OS package (could be partial match)
            os_dep_lower = os_dep.lower()
            for os_name, os_node_id in os_pkg_lookup.items():
                if os_dep_lower in os_name or os_name in os_dep_lower:
                    edge = Edge(
                        source_id=py_node_id,
                        target_id=os_node_id,
                        edge_type=EdgeType.REQUIRES,
                    )
                    graph.add_edge(py_node_id, os_node_id, data=edge)

        # Connect Python packages to env vars they read
        env_deps = get_python_env_deps(pkg_name)
        for env_dep in env_deps:
            env_node_id = env_var_lookup.get(env_dep.upper())
            if env_node_id:
                edge = Edge(
                    source_id=py_node_id,
                    target_id=env_node_id,
                    edge_type=EdgeType.READS,
                )
                graph.add_edge(py_node_id, env_node_id, data=edge)

    # Connect OS packages to env vars that configure them
    for os_node_id in os_packages:
        pkg_name = os_node_id.split(":", 1)[1]
        env_deps = get_os_env_deps(pkg_name)

        for env_dep in env_deps:
            env_node_id = env_var_lookup.get(env_dep.upper())
            if env_node_id:
                edge = Edge(
                    source_id=os_node_id,
                    target_id=env_node_id,
                    edge_type=EdgeType.CONFIGURED_BY,
                )
                graph.add_edge(os_node_id, env_node_id, data=edge)


def get_node(graph: nx.DiGraph, node_id: str) -> Node | None:
    """Get a Node object from the graph by its ID."""
    if not graph.has_node(node_id):
        return None
    return graph.nodes[node_id].get("data")


def get_all_nodes(graph: nx.DiGraph) -> list[Node]:
    """Get all Node objects from the graph."""
    return [
        graph.nodes[nid]["data"]
        for nid in graph.nodes()
        if "data" in graph.nodes[nid]
    ]


def get_nodes_by_type(graph: nx.DiGraph, node_type: NodeType) -> list[Node]:
    """Get all nodes of a specific type."""
    prefix = f"{node_type.value}:"
    return [
        graph.nodes[nid]["data"]
        for nid in graph.nodes()
        if nid.startswith(prefix) and "data" in graph.nodes[nid]
    ]
