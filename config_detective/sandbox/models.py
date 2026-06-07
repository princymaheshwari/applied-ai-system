"""Data models for sandbox verification results.

These models define the input/output contracts for sandbox runners.
Both the Docker runner and subprocess fallback produce the same result types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4


class VerificationStatus(str, Enum):
    """Outcome of a sandbox verification run."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class SandboxBackend(str, Enum):
    """Which sandbox backend executed the verification."""

    DOCKER = "docker"
    SUBPROCESS = "subprocess"
    NONE = "none"


@dataclass
class FixCandidate:
    """A single fix to verify inside the sandbox.

    Represents a one-delta change to apply on top of the failing environment
    before re-running the failing command.

    Attributes:
        fix_id: Unique identifier for tracking
        delta_id: Which delta node this fix targets (e.g. "env:LANG")
        fix_type: Category of fix (env_var, package_pin, dockerfile_layer, os_package)
        description: Human-readable description of the fix
        env_overrides: Env vars to set/override in the sandbox
        commands: Shell commands to run before the failing command
            (e.g. "pip install cryptography==41.0.0")
        dockerfile_patch: If the fix modifies a Dockerfile line, the
            (line_index, old_value, new_value) tuple
    """

    fix_id: str = field(default_factory=lambda: str(uuid4())[:8])
    delta_id: str = ""
    fix_type: str = ""
    description: str = ""
    env_overrides: dict[str, str] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    dockerfile_patch: tuple[int, str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "fix_id": self.fix_id,
            "delta_id": self.delta_id,
            "fix_type": self.fix_type,
            "description": self.description,
            "env_overrides": self.env_overrides,
            "commands": self.commands,
            "dockerfile_patch": list(self.dockerfile_patch) if self.dockerfile_patch else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FixCandidate":
        """Deserialize from dictionary."""
        patch = data.get("dockerfile_patch")
        return cls(
            fix_id=data.get("fix_id", str(uuid4())[:8]),
            delta_id=data.get("delta_id", ""),
            fix_type=data.get("fix_type", ""),
            description=data.get("description", ""),
            env_overrides=data.get("env_overrides", {}),
            commands=data.get("commands", []),
            dockerfile_patch=tuple(patch) if patch else None,
        )


@dataclass
class VerificationResult:
    """Result of running one fix candidate in the sandbox.

    Attributes:
        fix_id: Which fix candidate this result belongs to
        delta_id: The targeted delta node
        status: Overall verification outcome
        exit_code_before: Exit code of the failing command *without* the fix
        exit_code_after: Exit code of the failing command *with* the fix applied
        stderr_before: Stderr captured without the fix (may be truncated)
        stderr_after: Stderr captured with the fix (may be truncated)
        failure_signature_present: Whether the original failure trace fingerprint
            still appears in stderr_after
        fix_verified: True only if exit_code went from non-zero to zero AND the
            failure signature disappeared
        backend: Which sandbox backend ran this verification
        duration_ms: Wall-clock time for this single run
        error_message: If status is ERROR, the reason
        container_id: Docker container ID (if Docker backend)
    """

    fix_id: str = ""
    delta_id: str = ""
    status: VerificationStatus = VerificationStatus.SKIPPED
    exit_code_before: int | None = None
    exit_code_after: int | None = None
    stderr_before: str = ""
    stderr_after: str = ""
    failure_signature_present: bool = True
    fix_verified: bool = False
    backend: SandboxBackend = SandboxBackend.NONE
    duration_ms: int = 0
    error_message: str = ""
    container_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "fix_id": self.fix_id,
            "delta_id": self.delta_id,
            "status": self.status.value,
            "exit_code_before": self.exit_code_before,
            "exit_code_after": self.exit_code_after,
            "stderr_before": self.stderr_before[:2000],
            "stderr_after": self.stderr_after[:2000],
            "failure_signature_present": self.failure_signature_present,
            "fix_verified": self.fix_verified,
            "backend": self.backend.value,
            "duration_ms": self.duration_ms,
            "error_message": self.error_message,
            "container_id": self.container_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VerificationResult":
        """Deserialize from dictionary."""
        return cls(
            fix_id=data.get("fix_id", ""),
            delta_id=data.get("delta_id", ""),
            status=VerificationStatus(data.get("status", "skipped")),
            exit_code_before=data.get("exit_code_before"),
            exit_code_after=data.get("exit_code_after"),
            stderr_before=data.get("stderr_before", ""),
            stderr_after=data.get("stderr_after", ""),
            failure_signature_present=data.get("failure_signature_present", True),
            fix_verified=data.get("fix_verified", False),
            backend=SandboxBackend(data.get("backend", "none")),
            duration_ms=data.get("duration_ms", 0),
            error_message=data.get("error_message", ""),
            container_id=data.get("container_id", ""),
        )


@dataclass
class SandboxConfig:
    """Resource caps and configuration for sandbox runs.

    Attributes:
        max_runs: Maximum sandbox runs per investigation
        max_duration_seconds: Wall-clock timeout per individual run
        max_total_seconds: Total wall-clock budget for all runs combined
        memory_limit: Container memory limit (Docker only), e.g. "512m"
        cpu_period: CPU period in microseconds (Docker only)
        cpu_quota: CPU quota in microseconds (Docker only)
        network_disabled: Whether to disable networking in containers
        auto_remove: Whether to auto-remove containers after exit
    """

    max_runs: int = 10
    max_duration_seconds: int = 300
    max_total_seconds: int = 300
    memory_limit: str = "512m"
    cpu_period: int = 100000
    cpu_quota: int = 50000
    network_disabled: bool = True
    auto_remove: bool = True

    @classmethod
    def from_env(cls) -> "SandboxConfig":
        """Load config from environment variables with defaults."""
        import os
        return cls(
            max_runs=int(os.getenv("MAX_SANDBOX_RUNS", "10")),
            max_duration_seconds=int(os.getenv("MAX_INVESTIGATION_SECONDS", "300")),
            max_total_seconds=int(os.getenv("MAX_INVESTIGATION_SECONDS", "300")),
        )
