"""Pydantic models for environment snapshot data.

These models define the schema for the snapshot JSON output. Using Pydantic
ensures type safety, validation, and easy serialization/deserialization.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PackageManager(str, Enum):
    """Supported package managers for OS-level packages."""

    DPKG = "dpkg"
    APT = "apt"
    BREW = "brew"
    PACMAN = "pacman"
    RPM = "rpm"
    UNKNOWN = "unknown"


class LibcType(str, Enum):
    """C library type - affects binary compatibility."""

    GLIBC = "glibc"
    MUSL = "musl"
    UNKNOWN = "unknown"


class OSType(str, Enum):
    """Operating system type."""

    LINUX = "linux"
    DARWIN = "darwin"
    WINDOWS = "windows"
    UNKNOWN = "unknown"


class PythonPackage(BaseModel):
    """A single Python package from pip/uv/pipenv."""

    name: str
    version: str
    source: str = Field(
        default="unknown",
        description="Where this was parsed from: requirements.txt, uv.lock, Pipfile.lock, etc.",
    )


class NodePackage(BaseModel):
    """A single Node.js package from package-lock.json or yarn.lock."""

    name: str
    version: str
    source: str = Field(default="package-lock.json")


class OSPackage(BaseModel):
    """A single OS-level package (apt, brew, etc.)."""

    name: str
    version: str
    architecture: str | None = None
    description: str | None = None


class DockerInstruction(BaseModel):
    """A single Dockerfile instruction."""

    instruction: str = Field(description="FROM, RUN, ENV, COPY, ARG, etc.")
    value: str = Field(description="The instruction's argument(s)")
    start_line: int | None = None
    end_line: int | None = None


class EnvVarEntry(BaseModel):
    """A single environment variable with optional redaction info."""

    key: str
    value: str
    redacted: bool = Field(
        default=False, description="True if the value was redacted for security"
    )


class LocaleInfo(BaseModel):
    """Locale-related environment settings."""

    lang: str | None = Field(default=None, description="LANG env var")
    lc_all: str | None = Field(default=None, description="LC_ALL env var")
    lc_ctype: str | None = Field(default=None, description="LC_CTYPE env var")
    language: str | None = Field(default=None, description="LANGUAGE env var")


class TimezoneInfo(BaseModel):
    """Timezone configuration."""

    tz_env: str | None = Field(default=None, description="TZ env var")
    etc_timezone: str | None = Field(
        default=None, description="Contents of /etc/timezone"
    )
    etc_localtime_link: str | None = Field(
        default=None, description="Symlink target of /etc/localtime"
    )


class RuntimeVersions(BaseModel):
    """Detected runtime versions."""

    python: str | None = None
    python_implementation: str | None = Field(
        default=None, description="CPython, PyPy, etc."
    )
    node: str | None = None
    npm: str | None = None
    ruby: str | None = None
    go: str | None = None
    rust: str | None = None
    java: str | None = None


class SystemInfo(BaseModel):
    """Low-level system information."""

    os_type: OSType = OSType.UNKNOWN
    os_release: str | None = Field(default=None, description="e.g., Ubuntu 22.04")
    kernel: str | None = Field(default=None, description="Kernel version string")
    architecture: str | None = Field(default=None, description="x86_64, arm64, etc.")
    libc_type: LibcType = LibcType.UNKNOWN
    libc_version: str | None = None
    hostname: str | None = None


class LockfileData(BaseModel):
    """Parsed lockfile data for a single lockfile."""

    path: str
    lockfile_type: str = Field(
        description="requirements.txt, uv.lock, package-lock.json, etc."
    )
    packages: list[PythonPackage | NodePackage] = Field(default_factory=list)


class DockerfileData(BaseModel):
    """Parsed Dockerfile data."""

    path: str
    base_image: str | None = None
    instructions: list[DockerInstruction] = Field(default_factory=list)


class EnvironmentSnapshot(BaseModel):
    """Complete environment snapshot - the main output of Phase 1.

    This captures everything about an environment that could cause a
    "works on my machine" bug: dependencies, OS packages, env vars,
    locale, timezone, runtime versions, and system info.
    """

    snapshot_version: str = Field(
        default="1.0.0", description="Schema version for forward compatibility"
    )
    captured_at: datetime = Field(
        default_factory=datetime.utcnow, description="When this snapshot was taken"
    )
    snapshot_hash: str | None = Field(
        default=None,
        description="Deterministic hash of snapshot contents (excluding timestamp)",
    )

    repo_path: str | None = Field(
        default=None, description="Path to the repo that was snapshotted"
    )

    lockfiles: list[LockfileData] = Field(
        default_factory=list, description="All parsed lockfiles found in the repo"
    )

    dockerfiles: list[DockerfileData] = Field(
        default_factory=list, description="All parsed Dockerfiles found in the repo"
    )

    env_vars: list[EnvVarEntry] = Field(
        default_factory=list, description="Environment variables (secrets redacted)"
    )

    os_packages: list[OSPackage] = Field(
        default_factory=list, description="OS-level packages from dpkg/apt/brew"
    )
    os_package_manager: PackageManager = Field(
        default=PackageManager.UNKNOWN, description="Which package manager was detected"
    )

    runtime_versions: RuntimeVersions = Field(default_factory=RuntimeVersions)

    locale: LocaleInfo = Field(default_factory=LocaleInfo)

    timezone: TimezoneInfo = Field(default_factory=TimezoneInfo)

    system: SystemInfo = Field(default_factory=SystemInfo)

    capture_errors: list[str] = Field(
        default_factory=list,
        description="Any errors encountered during capture (non-fatal)",
    )

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> "EnvironmentSnapshot":
        """Deserialize from JSON string."""
        return cls.model_validate_json(json_str)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return self.model_dump()
