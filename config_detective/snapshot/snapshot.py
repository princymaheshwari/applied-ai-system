"""Main snapshot orchestrator.

This module coordinates all the sub-modules to capture a complete
environment snapshot. It handles errors gracefully so a failure in
one area (e.g., OS packages) doesn't prevent capturing the rest.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

from config_detective.snapshot.dockerfile import capture_dockerfiles
from config_detective.snapshot.env_vars import capture_env_vars
from config_detective.snapshot.locale_tz import capture_locale, capture_timezone
from config_detective.snapshot.lockfiles import capture_lockfiles
from config_detective.snapshot.models import EnvironmentSnapshot
from config_detective.snapshot.os_packages import capture_os_packages
from config_detective.snapshot.runtime_versions import capture_runtime_versions
from config_detective.snapshot.system_info import capture_system_info

logger = logging.getLogger(__name__)


def _compute_snapshot_hash(snapshot: EnvironmentSnapshot) -> str:
    """Compute a deterministic hash of the snapshot contents.

    The hash excludes:
    - captured_at (timestamp changes every run)
    - snapshot_hash (circular dependency)
    - capture_errors (non-deterministic)

    This allows comparing two snapshots for logical equality even if
    they were captured at different times.
    """
    # Create a copy of the data without the excluded fields
    data = snapshot.model_dump()
    data.pop("captured_at", None)
    data.pop("snapshot_hash", None)
    data.pop("capture_errors", None)

    # Sort keys for determinism
    json_str = json.dumps(data, sort_keys=True, default=str)

    # Use SHA-256, truncated for readability
    return hashlib.sha256(json_str.encode("utf-8")).hexdigest()[:16]


def capture_snapshot(
    repo_path: Path | str | None = None,
    include_os_packages: bool = True,
    include_env_vars: bool = True,
    include_lockfiles: bool = True,
    include_dockerfiles: bool = True,
) -> EnvironmentSnapshot:
    """Capture a complete environment snapshot.

    Args:
        repo_path: Path to the repository to snapshot. Defaults to current directory.
        include_os_packages: Whether to capture OS-level packages (slow on some systems).
        include_env_vars: Whether to capture environment variables.
        include_lockfiles: Whether to parse lockfiles in the repo.
        include_dockerfiles: Whether to parse Dockerfiles in the repo.

    Returns:
        EnvironmentSnapshot containing all captured data.

    Errors are logged and collected in capture_errors but don't stop the capture.
    """
    if repo_path is None:
        repo_path = Path.cwd()
    else:
        repo_path = Path(repo_path).resolve()

    errors: list[str] = []

    # Start with basic info
    snapshot = EnvironmentSnapshot(
        captured_at=datetime.utcnow(),
        repo_path=str(repo_path),
    )

    # Capture lockfiles
    if include_lockfiles:
        try:
            snapshot.lockfiles = capture_lockfiles(repo_path)
            logger.debug(f"Captured {len(snapshot.lockfiles)} lockfile(s)")
        except Exception as e:
            msg = f"Failed to capture lockfiles: {e}"
            logger.warning(msg)
            errors.append(msg)

    # Capture Dockerfiles
    if include_dockerfiles:
        try:
            snapshot.dockerfiles = capture_dockerfiles(repo_path)
            logger.debug(f"Captured {len(snapshot.dockerfiles)} Dockerfile(s)")
        except Exception as e:
            msg = f"Failed to capture Dockerfiles: {e}"
            logger.warning(msg)
            errors.append(msg)

    # Capture environment variables
    if include_env_vars:
        try:
            snapshot.env_vars = capture_env_vars()
            redacted_count = sum(1 for e in snapshot.env_vars if e.redacted)
            logger.debug(
                f"Captured {len(snapshot.env_vars)} env vars "
                f"({redacted_count} redacted)"
            )
        except Exception as e:
            msg = f"Failed to capture environment variables: {e}"
            logger.warning(msg)
            errors.append(msg)

    # Capture OS packages (can be slow)
    if include_os_packages:
        try:
            packages, pkg_manager = capture_os_packages()
            snapshot.os_packages = packages
            snapshot.os_package_manager = pkg_manager
            logger.debug(
                f"Captured {len(packages)} OS packages via {pkg_manager.value}"
            )
        except Exception as e:
            msg = f"Failed to capture OS packages: {e}"
            logger.warning(msg)
            errors.append(msg)

    # Capture runtime versions
    try:
        snapshot.runtime_versions = capture_runtime_versions(repo_path)
        logger.debug(f"Python: {snapshot.runtime_versions.python}")
    except Exception as e:
        msg = f"Failed to capture runtime versions: {e}"
        logger.warning(msg)
        errors.append(msg)

    # Capture locale settings
    try:
        snapshot.locale = capture_locale()
        logger.debug(f"Locale: LANG={snapshot.locale.lang}")
    except Exception as e:
        msg = f"Failed to capture locale: {e}"
        logger.warning(msg)
        errors.append(msg)

    # Capture timezone
    try:
        snapshot.timezone = capture_timezone()
        logger.debug(f"Timezone: TZ={snapshot.timezone.tz_env}")
    except Exception as e:
        msg = f"Failed to capture timezone: {e}"
        logger.warning(msg)
        errors.append(msg)

    # Capture system info
    try:
        snapshot.system = capture_system_info()
        logger.debug(
            f"System: {snapshot.system.os_type.value} "
            f"{snapshot.system.architecture} "
            f"{snapshot.system.libc_type.value}"
        )
    except Exception as e:
        msg = f"Failed to capture system info: {e}"
        logger.warning(msg)
        errors.append(msg)

    # Store any errors encountered
    snapshot.capture_errors = errors

    # Compute deterministic hash
    snapshot.snapshot_hash = _compute_snapshot_hash(snapshot)

    return snapshot


def save_snapshot(snapshot: EnvironmentSnapshot, output_path: Path | str) -> None:
    """Save a snapshot to a JSON file.

    Args:
        snapshot: The snapshot to save.
        output_path: Path to write the JSON file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    json_content = snapshot.to_json(indent=2)
    output_path.write_text(json_content, encoding="utf-8")

    logger.info(f"Snapshot saved to {output_path}")


def load_snapshot(input_path: Path | str) -> EnvironmentSnapshot:
    """Load a snapshot from a JSON file.

    Args:
        input_path: Path to the JSON file.

    Returns:
        Parsed EnvironmentSnapshot.
    """
    input_path = Path(input_path)
    json_content = input_path.read_text(encoding="utf-8")
    return EnvironmentSnapshot.from_json(json_content)
