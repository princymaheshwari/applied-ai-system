"""Schema definitions for the environment graph.

This module defines the typed nodes, edges, and deltas that make up the
environment graph. Using dataclasses and enums ensures type safety and
makes the graph structure self-documenting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    """Types of nodes in the environment graph."""

    # Package layers
    PYTHON_PACKAGE = "py_pkg"
    NODE_PACKAGE = "node_pkg"
    OS_PACKAGE = "os_pkg"

    # Configuration layers
    ENV_VAR = "env"
    DOCKERFILE_INSTRUCTION = "docker"

    # Runtime layers
    RUNTIME_VERSION = "runtime"
    LOCALE_SETTING = "locale"
    TIMEZONE_SETTING = "tz"
    SYSTEM_INFO = "system"

    # Special
    LOCKFILE = "lockfile"


class EdgeType(str, Enum):
    """Types of edges (relationships) between nodes."""

    # Dependency relationships
    REQUIRES = "requires"
    DEPENDS_ON = "depends_on"
    INSTALLED_BY = "installed_by"

    # Configuration relationships
    CONFIGURED_BY = "configured_by"
    READS = "reads"
    AFFECTS = "affects"

    # Containment relationships
    DEFINED_IN = "defined_in"
    PART_OF = "part_of"


class DeltaType(str, Enum):
    """Types of differences between two snapshots."""

    ONLY_IN_A = "only_in_a"
    ONLY_IN_B = "only_in_b"
    VERSION_CHANGED = "version_changed"
    VALUE_CHANGED = "value_changed"


@dataclass
class Node:
    """A node in the environment graph.

    Each node represents a single configuration item: a package, an env var,
    a runtime version, etc.

    Attributes:
        node_id: Unique identifier in format "type:name" (e.g., "py_pkg:requests")
        node_type: The type of this node
        name: Human-readable name (e.g., "requests")
        version: Version string if applicable (e.g., "2.31.0")
        value: Value string if applicable (e.g., env var value)
        metadata: Additional type-specific data
    """

    node_id: str
    node_type: NodeType
    name: str
    version: str | None = None
    value: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def make_id(cls, node_type: NodeType, name: str) -> str:
        """Create a canonical node ID from type and name."""
        return f"{node_type.value}:{name}"

    def __hash__(self) -> int:
        return hash(self.node_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Node):
            return False
        return self.node_id == other.node_id


@dataclass
class Edge:
    """An edge (relationship) between two nodes.

    Attributes:
        source_id: Node ID of the source node
        target_id: Node ID of the target node
        edge_type: The type of relationship
        metadata: Additional edge-specific data
    """

    source_id: str
    target_id: str
    edge_type: EdgeType
    metadata: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash((self.source_id, self.target_id, self.edge_type))


@dataclass
class Delta:
    """A difference between two environment graphs.

    Represents a single configuration item that differs between snapshot A
    and snapshot B, with a suspect score indicating how likely this delta
    is to be the root cause of a failure.

    Attributes:
        node_id: The node ID that differs
        node_type: The type of the differing node
        delta_type: The kind of difference (only_in_a, version_changed, etc.)
        name: Human-readable name of the differing item
        value_a: The value/version in snapshot A (None if only_in_b)
        value_b: The value/version in snapshot B (None if only_in_a)
        suspect_score: 0.0-1.0 score of how suspicious this delta is
        impacted_by: List of node IDs that depend on this node
        impacts: List of node IDs that this node depends on
        category: High-level category for grouping (e.g., "locale", "ssl", "timezone")
    """

    node_id: str
    node_type: NodeType
    delta_type: DeltaType
    name: str
    value_a: str | None = None
    value_b: str | None = None
    suspect_score: float = 0.5
    impacted_by: list[str] = field(default_factory=list)
    impacts: list[str] = field(default_factory=list)
    category: str | None = None

    def __lt__(self, other: "Delta") -> bool:
        """Sort deltas by suspect score (descending)."""
        return self.suspect_score > other.suspect_score


# Suspect score weights by node type
# Higher = more likely to be a root cause
NODE_TYPE_WEIGHTS: dict[NodeType, float] = {
    NodeType.OS_PACKAGE: 0.8,
    NodeType.PYTHON_PACKAGE: 0.7,
    NodeType.NODE_PACKAGE: 0.7,
    NodeType.RUNTIME_VERSION: 0.75,
    NodeType.LOCALE_SETTING: 0.65,
    NodeType.TIMEZONE_SETTING: 0.6,
    NodeType.ENV_VAR: 0.5,
    NodeType.DOCKERFILE_INSTRUCTION: 0.55,
    NodeType.SYSTEM_INFO: 0.7,
    NodeType.LOCKFILE: 0.3,
}

# Known high-risk patterns (package/env var names that commonly cause issues)
HIGH_RISK_PATTERNS: list[str] = [
    "ssl",
    "openssl",
    "libssl",
    "crypto",
    "cryptography",
    "locale",
    "lang",
    "lc_",
    "timezone",
    "tz",
    "glibc",
    "musl",
    "libc",
    "python",
    "node",
    "gcc",
    "libstdc++",
    "libpq",
    "postgres",
    "mysql",
    "sqlite",
    "encoding",
    "utf",
    "ascii",
]

# Env vars that are usually noise (low suspect score)
LOW_RISK_ENV_VARS: set[str] = {
    "PATH",
    "HOME",
    "USER",
    "SHELL",
    "TERM",
    "HOSTNAME",
    "PWD",
    "OLDPWD",
    "SHLVL",
    "LOGNAME",
    "EDITOR",
    "VISUAL",
    "PAGER",
    "LESS",
    "LS_COLORS",
    "XDG_SESSION_ID",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "SSH_CLIENT",
    "SSH_CONNECTION",
    "SSH_TTY",
    "DISPLAY",
    "COLORTERM",
    "TERM_PROGRAM",
    "_",
}
