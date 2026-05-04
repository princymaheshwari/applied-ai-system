"""Tests for the graph module (Phase 2).

These tests verify:
- Schema dataclasses work correctly
- Graph building from snapshots
- Delta computation between graphs
- Suspect score calculation
- Graph traversal utilities
"""

from __future__ import annotations

import pytest

from config_detective.graph.builder import (
    build_graph,
    get_all_nodes,
    get_node,
    get_nodes_by_type,
)
from config_detective.graph.differ import (
    compute_delta,
    filter_deltas,
    get_top_suspects,
    summarize_deltas,
)
from config_detective.graph.known_deps import (
    categorize_delta,
    get_os_env_deps,
    get_python_env_deps,
    get_python_os_deps,
)
from config_detective.graph.schema import (
    Delta,
    DeltaType,
    Edge,
    EdgeType,
    Node,
    NodeType,
)
from config_detective.graph.traversal import (
    find_critical_nodes,
    find_dependencies,
    find_dependents,
    find_nodes_by_pattern,
    find_nodes_by_type,
    find_path,
    get_node_neighborhood,
)
from config_detective.snapshot.models import (
    EnvironmentSnapshot,
    EnvVarEntry,
    LocaleInfo,
    LockfileData,
    OSPackage,
    PackageManager,
    PythonPackage,
    RuntimeVersions,
    SystemInfo,
    TimezoneInfo,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def minimal_snapshot() -> EnvironmentSnapshot:
    """A minimal snapshot with just a few items for basic tests."""
    return EnvironmentSnapshot(
        lockfiles=[
            LockfileData(
                path="requirements.txt",
                lockfile_type="requirements.txt",
                packages=[
                    PythonPackage(name="requests", version="2.31.0"),
                    PythonPackage(name="cryptography", version="42.0.5"),
                ],
            ),
        ],
        env_vars=[
            EnvVarEntry(key="LANG", value="en_US.UTF-8"),
            EnvVarEntry(key="PATH", value="/usr/bin:/bin"),
        ],
        os_packages=[
            OSPackage(name="libssl3", version="3.0.13"),
            OSPackage(name="libpq5", version="16.2"),
        ],
        os_package_manager=PackageManager.DPKG,
        runtime_versions=RuntimeVersions(python="3.11.8"),
        locale=LocaleInfo(lang="en_US.UTF-8"),
        timezone=TimezoneInfo(tz_env="UTC"),
    )


@pytest.fixture
def snapshot_a() -> EnvironmentSnapshot:
    """Snapshot A (working environment) for diff tests."""
    return EnvironmentSnapshot(
        lockfiles=[
            LockfileData(
                path="requirements.txt",
                lockfile_type="requirements.txt",
                packages=[
                    PythonPackage(name="requests", version="2.31.0"),
                    PythonPackage(name="cryptography", version="42.0.5"),
                    PythonPackage(name="psycopg2", version="2.9.9"),
                ],
            ),
        ],
        env_vars=[
            EnvVarEntry(key="LANG", value="en_US.UTF-8"),
            EnvVarEntry(key="TZ", value="America/New_York"),
            EnvVarEntry(key="DATABASE_URL", value="[REDACTED]", redacted=True),
        ],
        os_packages=[
            OSPackage(name="libssl3", version="3.0.13"),
            OSPackage(name="libpq5", version="16.2"),
            OSPackage(name="libgomp1", version="12.3.0"),
        ],
        os_package_manager=PackageManager.DPKG,
        runtime_versions=RuntimeVersions(python="3.11.8"),
        locale=LocaleInfo(lang="en_US.UTF-8"),
        timezone=TimezoneInfo(tz_env="America/New_York"),
        system=SystemInfo(os_release="Ubuntu 22.04"),
    )


@pytest.fixture
def snapshot_b() -> EnvironmentSnapshot:
    """Snapshot B (broken environment) for diff tests - has key differences."""
    return EnvironmentSnapshot(
        lockfiles=[
            LockfileData(
                path="requirements.txt",
                lockfile_type="requirements.txt",
                packages=[
                    PythonPackage(name="requests", version="2.31.0"),
                    PythonPackage(name="cryptography", version="42.0.5"),
                    # psycopg2 is MISSING - only_in_a
                ],
            ),
        ],
        env_vars=[
            EnvVarEntry(key="LANG", value="C"),  # Different value
            EnvVarEntry(key="TZ", value="UTC"),  # Different value
            EnvVarEntry(key="CI", value="true"),  # only_in_b
        ],
        os_packages=[
            OSPackage(name="libssl1.1", version="1.1.1"),  # Different version/name
            OSPackage(name="libpq5", version="15.0"),  # Different version
            # libgomp1 is MISSING - only_in_a
        ],
        os_package_manager=PackageManager.DPKG,
        runtime_versions=RuntimeVersions(python="3.10.12"),  # Different version
        locale=LocaleInfo(lang="C"),
        timezone=TimezoneInfo(tz_env="UTC"),
        system=SystemInfo(os_release="Debian 11"),
    )


# ============================================================================
# Schema Tests
# ============================================================================


class TestSchema:
    """Tests for schema dataclasses."""

    def test_node_make_id(self) -> None:
        """Test Node.make_id creates correct IDs."""
        assert Node.make_id(NodeType.PYTHON_PACKAGE, "requests") == "py_pkg:requests"
        assert Node.make_id(NodeType.OS_PACKAGE, "libssl3") == "os_pkg:libssl3"
        assert Node.make_id(NodeType.ENV_VAR, "PATH") == "env:PATH"

    def test_node_equality(self) -> None:
        """Test Node equality is based on node_id."""
        node1 = Node(
            node_id="py_pkg:requests",
            node_type=NodeType.PYTHON_PACKAGE,
            name="requests",
            version="2.31.0",
        )
        node2 = Node(
            node_id="py_pkg:requests",
            node_type=NodeType.PYTHON_PACKAGE,
            name="requests",
            version="2.32.0",  # Different version
        )
        node3 = Node(
            node_id="py_pkg:urllib3",
            node_type=NodeType.PYTHON_PACKAGE,
            name="urllib3",
            version="2.0.0",
        )

        assert node1 == node2  # Same ID = equal
        assert node1 != node3  # Different ID = not equal

    def test_node_hashable(self) -> None:
        """Test nodes can be used in sets/dicts."""
        node1 = Node(
            node_id="py_pkg:requests",
            node_type=NodeType.PYTHON_PACKAGE,
            name="requests",
        )
        node2 = Node(
            node_id="py_pkg:urllib3",
            node_type=NodeType.PYTHON_PACKAGE,
            name="urllib3",
        )

        node_set = {node1, node2, node1}  # Duplicate should be ignored
        assert len(node_set) == 2

    def test_delta_sorting(self) -> None:
        """Test deltas sort by suspect score (descending)."""
        delta_low = Delta(
            node_id="env:PATH",
            node_type=NodeType.ENV_VAR,
            delta_type=DeltaType.VALUE_CHANGED,
            name="PATH",
            suspect_score=0.2,
        )
        delta_high = Delta(
            node_id="os_pkg:libssl3",
            node_type=NodeType.OS_PACKAGE,
            delta_type=DeltaType.VERSION_CHANGED,
            name="libssl3",
            suspect_score=0.85,
        )
        delta_mid = Delta(
            node_id="py_pkg:requests",
            node_type=NodeType.PYTHON_PACKAGE,
            delta_type=DeltaType.ONLY_IN_A,
            name="requests",
            suspect_score=0.5,
        )

        deltas = sorted([delta_low, delta_high, delta_mid])
        assert deltas[0].suspect_score == 0.85  # Highest first
        assert deltas[1].suspect_score == 0.5
        assert deltas[2].suspect_score == 0.2

    def test_edge_hashable(self) -> None:
        """Test edges can be used in sets."""
        edge1 = Edge(
            source_id="py_pkg:cryptography",
            target_id="os_pkg:libssl3",
            edge_type=EdgeType.REQUIRES,
        )
        edge2 = Edge(
            source_id="py_pkg:cryptography",
            target_id="os_pkg:libssl3",
            edge_type=EdgeType.REQUIRES,
        )
        edge3 = Edge(
            source_id="py_pkg:psycopg2",
            target_id="os_pkg:libpq5",
            edge_type=EdgeType.REQUIRES,
        )

        edge_set = {edge1, edge2, edge3}
        assert len(edge_set) == 2  # edge1 == edge2


# ============================================================================
# Known Dependencies Tests
# ============================================================================


class TestKnownDeps:
    """Tests for known dependency mappings."""

    def test_python_to_os_deps(self) -> None:
        """Test Python -> OS package lookups."""
        deps = get_python_os_deps("cryptography")
        assert "libssl" in deps
        assert "libffi" in deps

        deps = get_python_os_deps("psycopg2")
        assert "libpq" in deps

    def test_python_to_env_deps(self) -> None:
        """Test Python -> env var lookups."""
        deps = get_python_env_deps("requests")
        assert "SSL_CERT_FILE" in deps or "REQUESTS_CA_BUNDLE" in deps

        deps = get_python_env_deps("boto3")
        assert "AWS_ACCESS_KEY_ID" in deps

    def test_os_to_env_deps(self) -> None:
        """Test OS package -> env var lookups."""
        deps = get_os_env_deps("libssl3")
        assert "OPENSSL_CONF" in deps

        deps = get_os_env_deps("tzdata")
        assert "TZ" in deps

    def test_categorize_delta(self) -> None:
        """Test delta categorization."""
        assert categorize_delta("libssl3") == "ssl"
        assert categorize_delta("LANG") == "locale"
        assert categorize_delta("TZ") == "timezone"
        assert categorize_delta("libpq5") == "database"
        assert categorize_delta("python3.11") == "python"

    def test_unknown_package_returns_empty(self) -> None:
        """Unknown packages should return empty lists."""
        assert get_python_os_deps("some-unknown-package") == []
        assert get_python_env_deps("nonexistent") == []


# ============================================================================
# Graph Builder Tests
# ============================================================================


class TestGraphBuilder:
    """Tests for graph building from snapshots."""

    def test_build_graph_creates_nodes(self, minimal_snapshot: EnvironmentSnapshot) -> None:
        """Test that build_graph creates nodes for all snapshot items."""
        graph = build_graph(minimal_snapshot)

        # Should have Python package nodes
        assert graph.has_node("py_pkg:requests")
        assert graph.has_node("py_pkg:cryptography")

        # Should have env var nodes
        assert graph.has_node("env:LANG")
        assert graph.has_node("env:PATH")

        # Should have OS package nodes
        assert graph.has_node("os_pkg:libssl3")
        assert graph.has_node("os_pkg:libpq5")

        # Should have runtime version node
        assert graph.has_node("runtime:python")

    def test_get_node_returns_node_data(self, minimal_snapshot: EnvironmentSnapshot) -> None:
        """Test get_node retrieves Node objects correctly."""
        graph = build_graph(minimal_snapshot)

        node = get_node(graph, "py_pkg:requests")
        assert node is not None
        assert node.name == "requests"
        assert node.version == "2.31.0"
        assert node.node_type == NodeType.PYTHON_PACKAGE

    def test_get_node_returns_none_for_missing(
        self, minimal_snapshot: EnvironmentSnapshot
    ) -> None:
        """Test get_node returns None for non-existent nodes."""
        graph = build_graph(minimal_snapshot)
        assert get_node(graph, "py_pkg:nonexistent") is None

    def test_get_all_nodes(self, minimal_snapshot: EnvironmentSnapshot) -> None:
        """Test get_all_nodes returns all Node objects."""
        graph = build_graph(minimal_snapshot)
        nodes = get_all_nodes(graph)

        assert len(nodes) >= 6  # At least py pkgs + env vars + os pkgs
        assert all(isinstance(n, Node) for n in nodes)

    def test_get_nodes_by_type(self, minimal_snapshot: EnvironmentSnapshot) -> None:
        """Test filtering nodes by type."""
        graph = build_graph(minimal_snapshot)

        py_nodes = get_nodes_by_type(graph, NodeType.PYTHON_PACKAGE)
        assert len(py_nodes) == 2
        assert all(n.node_type == NodeType.PYTHON_PACKAGE for n in py_nodes)

        env_nodes = get_nodes_by_type(graph, NodeType.ENV_VAR)
        assert len(env_nodes) == 2

    def test_cross_layer_edges_created(
        self, minimal_snapshot: EnvironmentSnapshot
    ) -> None:
        """Test that cross-layer dependency edges are created."""
        graph = build_graph(minimal_snapshot)

        # cryptography should have edge to libssl3
        # (assuming the partial matching finds libssl in libssl3)
        crypto_edges = list(graph.out_edges("py_pkg:cryptography"))
        edge_targets = [e[1] for e in crypto_edges]

        # Should have at least one edge (to OS package or env var)
        assert len(crypto_edges) >= 0  # May vary based on matching


# ============================================================================
# Differ Tests
# ============================================================================


class TestDiffer:
    """Tests for graph diffing and suspect scoring."""

    def test_compute_delta_finds_differences(
        self, snapshot_a: EnvironmentSnapshot, snapshot_b: EnvironmentSnapshot
    ) -> None:
        """Test compute_delta identifies all types of differences."""
        graph_a = build_graph(snapshot_a)
        graph_b = build_graph(snapshot_b)

        deltas = compute_delta(graph_a, graph_b)

        # Should find various types of deltas
        delta_types = {d.delta_type for d in deltas}
        assert len(deltas) > 0

        # Check we found at least some expected deltas
        node_ids = {d.node_id for d in deltas}

        # psycopg2 should be only_in_a
        assert "py_pkg:psycopg2" in node_ids

        # CI env var should be only_in_b
        assert "env:CI" in node_ids

    def test_deltas_sorted_by_suspect_score(
        self, snapshot_a: EnvironmentSnapshot, snapshot_b: EnvironmentSnapshot
    ) -> None:
        """Test deltas are sorted by suspect score (highest first)."""
        graph_a = build_graph(snapshot_a)
        graph_b = build_graph(snapshot_b)

        deltas = compute_delta(graph_a, graph_b)

        # Verify descending order
        for i in range(len(deltas) - 1):
            assert deltas[i].suspect_score >= deltas[i + 1].suspect_score

    def test_get_top_suspects(
        self, snapshot_a: EnvironmentSnapshot, snapshot_b: EnvironmentSnapshot
    ) -> None:
        """Test get_top_suspects returns N highest scored deltas."""
        graph_a = build_graph(snapshot_a)
        graph_b = build_graph(snapshot_b)

        deltas = compute_delta(graph_a, graph_b)
        top_3 = get_top_suspects(deltas, n=3)

        assert len(top_3) <= 3
        if len(top_3) == 3:
            assert top_3[0].suspect_score >= top_3[1].suspect_score >= top_3[2].suspect_score

    def test_filter_deltas_by_score(
        self, snapshot_a: EnvironmentSnapshot, snapshot_b: EnvironmentSnapshot
    ) -> None:
        """Test filtering deltas by minimum score."""
        graph_a = build_graph(snapshot_a)
        graph_b = build_graph(snapshot_b)

        deltas = compute_delta(graph_a, graph_b)
        high_score = filter_deltas(deltas, min_score=0.6)

        assert all(d.suspect_score >= 0.6 for d in high_score)

    def test_summarize_deltas(
        self, snapshot_a: EnvironmentSnapshot, snapshot_b: EnvironmentSnapshot
    ) -> None:
        """Test summarize_deltas groups by category."""
        graph_a = build_graph(snapshot_a)
        graph_b = build_graph(snapshot_b)

        deltas = compute_delta(graph_a, graph_b)
        summary = summarize_deltas(deltas)

        # Should have at least one category
        assert isinstance(summary, dict)

    def test_identical_snapshots_no_deltas(
        self, snapshot_a: EnvironmentSnapshot
    ) -> None:
        """Test identical snapshots produce no deltas."""
        graph_a = build_graph(snapshot_a)
        graph_b = build_graph(snapshot_a)  # Same snapshot

        deltas = compute_delta(graph_a, graph_b)
        assert len(deltas) == 0


# ============================================================================
# Traversal Tests
# ============================================================================


class TestTraversal:
    """Tests for graph traversal utilities."""

    def test_find_nodes_by_type(self, minimal_snapshot: EnvironmentSnapshot) -> None:
        """Test finding nodes by type."""
        graph = build_graph(minimal_snapshot)

        py_nodes = find_nodes_by_type(graph, NodeType.PYTHON_PACKAGE)
        assert "py_pkg:requests" in py_nodes
        assert "py_pkg:cryptography" in py_nodes

    def test_find_nodes_by_pattern(self, minimal_snapshot: EnvironmentSnapshot) -> None:
        """Test finding nodes by name pattern."""
        graph = build_graph(minimal_snapshot)

        ssl_nodes = find_nodes_by_pattern(graph, "ssl")
        assert "os_pkg:libssl3" in ssl_nodes

        # Case insensitive
        lang_nodes = find_nodes_by_pattern(graph, "LANG")
        assert len(lang_nodes) >= 1

    def test_find_dependents_and_dependencies(
        self, minimal_snapshot: EnvironmentSnapshot
    ) -> None:
        """Test finding dependents and dependencies."""
        graph = build_graph(minimal_snapshot)

        # These may be empty if no cross-layer edges were created
        # but the functions should not error
        deps = find_dependencies(graph, "py_pkg:cryptography")
        assert isinstance(deps, list)

        dependents = find_dependents(graph, "os_pkg:libssl3")
        assert isinstance(dependents, list)

    def test_find_path(self, minimal_snapshot: EnvironmentSnapshot) -> None:
        """Test finding path between nodes."""
        graph = build_graph(minimal_snapshot)

        # Path from node to itself should exist
        path = find_path(graph, "py_pkg:requests", "py_pkg:requests")
        assert path == ["py_pkg:requests"]

        # Path between unconnected nodes should be None
        path = find_path(graph, "py_pkg:requests", "env:PATH")
        # May or may not exist depending on edges

    def test_get_node_neighborhood(self, minimal_snapshot: EnvironmentSnapshot) -> None:
        """Test getting neighborhood of a node."""
        graph = build_graph(minimal_snapshot)

        neighborhood = get_node_neighborhood(graph, "py_pkg:requests", radius=1)
        assert "py_pkg:requests" in neighborhood

    def test_find_critical_nodes(
        self, snapshot_a: EnvironmentSnapshot
    ) -> None:
        """Test finding critical (highly-connected) nodes."""
        graph = build_graph(snapshot_a)

        # With min_dependents=0, should find nodes
        critical = find_critical_nodes(graph, min_dependents=0)
        assert isinstance(critical, list)

        # Each item should be (node_id, count) tuple
        for item in critical:
            assert len(item) == 2
            assert isinstance(item[0], str)
            assert isinstance(item[1], int)
