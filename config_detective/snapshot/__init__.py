"""Snapshot layer - captures environment configuration into deterministic JSON.

This module provides the core snapshot functionality for CONFIG DETECTIVE:
capturing lockfiles, Dockerfiles, environment variables, OS packages,
runtime versions, locale, timezone, and system info.

Main entry points:
    - capture_snapshot(): Capture a complete environment snapshot
    - save_snapshot(): Save a snapshot to JSON file
    - load_snapshot(): Load a snapshot from JSON file

Example:
    from config_detective.snapshot import capture_snapshot, save_snapshot

    snapshot = capture_snapshot(repo_path=".")
    save_snapshot(snapshot, "snap.json")
"""

from config_detective.snapshot.models import (
    DockerfileData,
    DockerInstruction,
    EnvironmentSnapshot,
    EnvVarEntry,
    LibcType,
    LocaleInfo,
    LockfileData,
    NodePackage,
    OSPackage,
    OSType,
    PackageManager,
    PythonPackage,
    RuntimeVersions,
    SystemInfo,
    TimezoneInfo,
)
from config_detective.snapshot.snapshot import (
    capture_snapshot,
    load_snapshot,
    save_snapshot,
)

__all__ = [
    # Main functions
    "capture_snapshot",
    "save_snapshot",
    "load_snapshot",
    # Models
    "EnvironmentSnapshot",
    "LockfileData",
    "PythonPackage",
    "NodePackage",
    "DockerfileData",
    "DockerInstruction",
    "EnvVarEntry",
    "OSPackage",
    "RuntimeVersions",
    "LocaleInfo",
    "TimezoneInfo",
    "SystemInfo",
    # Enums
    "PackageManager",
    "LibcType",
    "OSType",
]
