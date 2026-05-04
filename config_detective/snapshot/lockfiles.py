"""Lockfile parsing for Python and Node.js dependencies.

Supports:
- requirements.txt (pip)
- uv.lock (uv)
- Pipfile.lock (pipenv)
- package-lock.json (npm)
- yarn.lock (yarn) - basic support

Each parser extracts package names and versions into a unified format.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from config_detective.snapshot.models import (
    LockfileData,
    NodePackage,
    PythonPackage,
)


# Common lockfile names to search for
PYTHON_LOCKFILES = [
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-prod.txt",
    "requirements-test.txt",
    "requirements.lock",
    "uv.lock",
    "Pipfile.lock",
    "poetry.lock",
    "pdm.lock",
]

NODE_LOCKFILES = [
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
]


def _parse_requirements_txt(path: Path) -> list[PythonPackage]:
    """Parse a requirements.txt file.

    Handles:
    - package==1.0.0
    - package>=1.0.0
    - package~=1.0.0
    - package[extra]==1.0.0
    - -r other_file.txt (ignored)
    - # comments
    """
    packages: list[PythonPackage] = []
    content = path.read_text(encoding="utf-8", errors="replace")

    for line in content.splitlines():
        line = line.strip()

        # Skip empty lines, comments, and includes
        if not line or line.startswith("#") or line.startswith("-"):
            continue

        # Skip URLs and git references
        if "://" in line or line.startswith("git+"):
            continue

        # Parse package==version, package>=version, etc.
        # Pattern: package_name[extras] operator version
        match = re.match(
            r"^([a-zA-Z0-9_-]+(?:\[[^\]]+\])?)\s*([<>=!~]+)\s*([^\s;#]+)",
            line,
        )
        if match:
            name = match.group(1).split("[")[0]  # Remove extras
            version = match.group(3)
            packages.append(
                PythonPackage(name=name, version=version, source=path.name)
            )
        else:
            # Package without version specifier
            match = re.match(r"^([a-zA-Z0-9_-]+)", line)
            if match:
                packages.append(
                    PythonPackage(
                        name=match.group(1), version="*", source=path.name
                    )
                )

    return packages


def _parse_uv_lock(path: Path) -> list[PythonPackage]:
    """Parse a uv.lock file (TOML-like format).

    uv.lock uses a custom format with [[package]] sections.
    """
    packages: list[PythonPackage] = []
    content = path.read_text(encoding="utf-8", errors="replace")

    # Simple regex-based parsing for [[package]] sections
    # Example:
    # [[package]]
    # name = "requests"
    # version = "2.31.0"

    current_name: str | None = None
    current_version: str | None = None

    for line in content.splitlines():
        line = line.strip()

        if line == "[[package]]":
            # Save previous package if complete
            if current_name and current_version:
                packages.append(
                    PythonPackage(
                        name=current_name, version=current_version, source="uv.lock"
                    )
                )
            current_name = None
            current_version = None
            continue

        name_match = re.match(r'^name\s*=\s*"([^"]+)"', line)
        if name_match:
            current_name = name_match.group(1)
            continue

        version_match = re.match(r'^version\s*=\s*"([^"]+)"', line)
        if version_match:
            current_version = version_match.group(1)
            continue

    # Don't forget the last package
    if current_name and current_version:
        packages.append(
            PythonPackage(
                name=current_name, version=current_version, source="uv.lock"
            )
        )

    return packages


def _parse_pipfile_lock(path: Path) -> list[PythonPackage]:
    """Parse a Pipfile.lock file (JSON format)."""
    packages: list[PythonPackage] = []
    content = path.read_text(encoding="utf-8", errors="replace")

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return packages

    # Pipfile.lock has "default" and "develop" sections
    for section in ["default", "develop"]:
        if section not in data:
            continue

        for name, info in data[section].items():
            if isinstance(info, dict) and "version" in info:
                version = info["version"].lstrip("=")
                packages.append(
                    PythonPackage(name=name, version=version, source="Pipfile.lock")
                )

    return packages


def _parse_poetry_lock(path: Path) -> list[PythonPackage]:
    """Parse a poetry.lock file (TOML format).

    Similar structure to uv.lock with [[package]] sections.
    """
    packages: list[PythonPackage] = []
    content = path.read_text(encoding="utf-8", errors="replace")

    current_name: str | None = None
    current_version: str | None = None

    for line in content.splitlines():
        line = line.strip()

        if line == "[[package]]":
            if current_name and current_version:
                packages.append(
                    PythonPackage(
                        name=current_name, version=current_version, source="poetry.lock"
                    )
                )
            current_name = None
            current_version = None
            continue

        name_match = re.match(r'^name\s*=\s*"([^"]+)"', line)
        if name_match:
            current_name = name_match.group(1)
            continue

        version_match = re.match(r'^version\s*=\s*"([^"]+)"', line)
        if version_match:
            current_version = version_match.group(1)
            continue

    if current_name and current_version:
        packages.append(
            PythonPackage(
                name=current_name, version=current_version, source="poetry.lock"
            )
        )

    return packages


def _parse_package_lock_json(path: Path) -> list[NodePackage]:
    """Parse a package-lock.json file (npm)."""
    packages: list[NodePackage] = []
    content = path.read_text(encoding="utf-8", errors="replace")

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return packages

    # npm v7+ uses "packages" key, older versions use "dependencies"
    if "packages" in data:
        for pkg_path, info in data["packages"].items():
            if not pkg_path:  # Skip the root entry
                continue
            # Extract package name from path like "node_modules/lodash"
            name = pkg_path.split("node_modules/")[-1]
            if "/" in name and not name.startswith("@"):
                continue  # Skip nested deps that aren't scoped packages
            version = info.get("version", "")
            if name and version:
                packages.append(
                    NodePackage(name=name, version=version, source="package-lock.json")
                )
    elif "dependencies" in data:
        def extract_deps(deps: dict[str, Any], parent: str = "") -> None:
            for name, info in deps.items():
                if isinstance(info, dict) and "version" in info:
                    packages.append(
                        NodePackage(
                            name=name,
                            version=info["version"],
                            source="package-lock.json",
                        )
                    )
                    # Recursively handle nested dependencies
                    if "dependencies" in info:
                        extract_deps(info["dependencies"], name)

        extract_deps(data["dependencies"])

    return packages


def _parse_yarn_lock(path: Path) -> list[NodePackage]:
    """Parse a yarn.lock file (yarn v1 format).

    Basic parsing - yarn.lock has a custom format.
    """
    packages: list[NodePackage] = []
    content = path.read_text(encoding="utf-8", errors="replace")

    # yarn.lock v1 format:
    # "package@^1.0.0":
    #   version "1.2.3"
    current_name: str | None = None

    for line in content.splitlines():
        # Package declaration line: "lodash@^4.17.0", lodash@^4.17.0:
        if not line.startswith(" ") and ("@" in line or line.endswith(":")):
            # Extract package name (before the version specifier)
            match = re.match(r'^"?(@?[^@"]+)@', line)
            if match:
                current_name = match.group(1)
            continue

        # Version line: "  version "1.2.3""
        if current_name and line.strip().startswith("version"):
            version_match = re.match(r'\s+version\s+"([^"]+)"', line)
            if version_match:
                packages.append(
                    NodePackage(
                        name=current_name,
                        version=version_match.group(1),
                        source="yarn.lock",
                    )
                )
                current_name = None

    return packages


def parse_lockfile(path: Path) -> LockfileData:
    """Parse a single lockfile and return structured data.

    Automatically detects the lockfile type based on filename.
    """
    filename = path.name.lower()
    packages: list[PythonPackage | NodePackage] = []
    lockfile_type = filename

    if filename == "requirements.txt" or filename.startswith("requirements"):
        packages = _parse_requirements_txt(path)
        lockfile_type = "requirements.txt"
    elif filename == "uv.lock":
        packages = _parse_uv_lock(path)
        lockfile_type = "uv.lock"
    elif filename == "pipfile.lock":
        packages = _parse_pipfile_lock(path)
        lockfile_type = "Pipfile.lock"
    elif filename == "poetry.lock":
        packages = _parse_poetry_lock(path)
        lockfile_type = "poetry.lock"
    elif filename == "package-lock.json":
        packages = _parse_package_lock_json(path)
        lockfile_type = "package-lock.json"
    elif filename == "yarn.lock":
        packages = _parse_yarn_lock(path)
        lockfile_type = "yarn.lock"

    return LockfileData(
        path=str(path),
        lockfile_type=lockfile_type,
        packages=packages,
    )


def find_lockfiles(repo_path: Path) -> list[Path]:
    """Find all lockfiles in a repository.

    Searches recursively but skips common non-relevant directories.
    """
    skip_dirs = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "dist",
        "build",
        ".tox",
        ".nox",
        "legacy",  # Skip legacy folder in our project
    }

    lockfiles: list[Path] = []
    all_lockfile_names = set(PYTHON_LOCKFILES + NODE_LOCKFILES)

    for item in repo_path.rglob("*"):
        # Skip directories we don't care about
        if any(skip in item.parts for skip in skip_dirs):
            continue

        if item.is_file() and item.name in all_lockfile_names:
            lockfiles.append(item)

    return sorted(lockfiles)


def capture_lockfiles(repo_path: Path) -> list[LockfileData]:
    """Find and parse all lockfiles in a repository."""
    lockfile_paths = find_lockfiles(repo_path)
    return [parse_lockfile(p) for p in lockfile_paths]
