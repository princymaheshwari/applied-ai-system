"""Runtime version detection for Python, Node.js, Ruby, and other languages.

Detects versions from:
1. Direct command execution (python --version, node --version, etc.)
2. Version files (.python-version, .nvmrc, .ruby-version, etc.)
3. Environment variables (PYTHON_VERSION in Docker, etc.)
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from config_detective.snapshot.models import RuntimeVersions


def _run_version_command(cmd: list[str], timeout: int = 5) -> str | None:
    """Run a version command and extract the version string."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = result.stdout.strip() or result.stderr.strip()
        return output if output else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _extract_version(output: str | None, pattern: str = r"(\d+\.\d+(?:\.\d+)?)") -> str | None:
    """Extract a version number from command output."""
    if not output:
        return None
    match = re.search(pattern, output)
    return match.group(1) if match else None


def _read_version_file(repo_path: Path, filename: str) -> str | None:
    """Read a version from a version file in the repo."""
    version_file = repo_path / filename
    if version_file.exists():
        try:
            content = version_file.read_text(encoding="utf-8").strip()
            # Handle various formats
            # .python-version: 3.11.4
            # .nvmrc: v18.17.0 or 18.17.0
            # .ruby-version: 3.2.2
            version = content.split("\n")[0].strip()
            version = version.lstrip("v")  # Remove leading 'v' if present
            return version if version else None
        except OSError:
            return None
    return None


def detect_python_version(repo_path: Path | None = None) -> tuple[str | None, str | None]:
    """Detect Python version and implementation.

    Returns:
        Tuple of (version, implementation) e.g., ("3.11.4", "CPython")
    """
    # Current interpreter (most accurate for this environment)
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    implementation = platform.python_implementation()

    # Also check environment variable (common in Docker)
    env_version = os.environ.get("PYTHON_VERSION")
    if env_version:
        # Environment variable might override
        pass  # We prefer the actual running version

    # Check version file if repo path provided
    if repo_path:
        file_version = _read_version_file(repo_path, ".python-version")
        if file_version:
            # Version file is what the project expects, note if different
            pass  # We still return the actual running version

    return version, implementation


def detect_node_version(repo_path: Path | None = None) -> tuple[str | None, str | None]:
    """Detect Node.js and npm versions.

    Returns:
        Tuple of (node_version, npm_version)
    """
    node_version: str | None = None
    npm_version: str | None = None

    # Check if node is available
    if shutil.which("node"):
        output = _run_version_command(["node", "--version"])
        node_version = _extract_version(output)

    # Check npm
    if shutil.which("npm"):
        output = _run_version_command(["npm", "--version"])
        npm_version = _extract_version(output)

    # Check version file
    if repo_path:
        file_version = _read_version_file(repo_path, ".nvmrc")
        if file_version and not node_version:
            node_version = file_version

        # Also check .node-version (used by nodenv)
        file_version = _read_version_file(repo_path, ".node-version")
        if file_version and not node_version:
            node_version = file_version

    return node_version, npm_version


def detect_ruby_version(repo_path: Path | None = None) -> str | None:
    """Detect Ruby version."""
    ruby_version: str | None = None

    if shutil.which("ruby"):
        output = _run_version_command(["ruby", "--version"])
        ruby_version = _extract_version(output)

    if repo_path:
        file_version = _read_version_file(repo_path, ".ruby-version")
        if file_version:
            ruby_version = file_version

    return ruby_version


def detect_go_version() -> str | None:
    """Detect Go version."""
    if shutil.which("go"):
        output = _run_version_command(["go", "version"])
        # "go version go1.21.0 linux/amd64"
        return _extract_version(output, r"go(\d+\.\d+(?:\.\d+)?)")
    return None


def detect_rust_version() -> str | None:
    """Detect Rust version."""
    if shutil.which("rustc"):
        output = _run_version_command(["rustc", "--version"])
        return _extract_version(output)
    return None


def detect_java_version() -> str | None:
    """Detect Java version."""
    if shutil.which("java"):
        output = _run_version_command(["java", "-version"])
        # Java outputs to stderr and has various formats:
        # "openjdk version "17.0.1" 2021-10-19"
        # "java version "1.8.0_301""
        version = _extract_version(output, r'"(\d+(?:\.\d+)*)"')
        if version:
            # Normalize old-style "1.8.0" to "8.0"
            if version.startswith("1."):
                version = version[2:]
        return version
    return None


def capture_runtime_versions(repo_path: Path | None = None) -> RuntimeVersions:
    """Capture all detected runtime versions.

    Args:
        repo_path: Optional path to check for version files like .python-version

    Returns:
        RuntimeVersions model with all detected versions
    """
    python_version, python_impl = detect_python_version(repo_path)
    node_version, npm_version = detect_node_version(repo_path)

    return RuntimeVersions(
        python=python_version,
        python_implementation=python_impl,
        node=node_version,
        npm=npm_version,
        ruby=detect_ruby_version(repo_path),
        go=detect_go_version(),
        rust=detect_rust_version(),
        java=detect_java_version(),
    )
