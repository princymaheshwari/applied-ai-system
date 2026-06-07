"""Sandbox verifier — empirical verification of candidate fixes.

This module is the core differentiator of CONFIG DETECTIVE: every reported
root cause is backed by a reproducible experiment, not LLM intuition.

The verification protocol:
1. Build a sandbox matching the *failing* environment.
2. Run the failing command — capture exit code + stderr (baseline).
3. Apply only the candidate fix (one-delta change).
4. Re-run the failing command inside the sandbox.
5. The fix is "verified" iff exit code goes from non-zero to zero
   AND the failure-trace fingerprint disappears from stderr.

Two backends:
- **Docker** (preferred): Full isolation via Docker SDK ephemeral containers
  with resource caps (memory, CPU, network-disabled, auto-remove).
- **Subprocess** (fallback): Partial verification using isolated subprocesses
  when Docker is unavailable (Windows without Docker Desktop, CI without DinD).

Usage:
    from config_detective.sandbox import verify_fixes, is_sandbox_available

    if is_sandbox_available():
        results = verify_fixes(
            fixes=candidates,
            base_image="python:3.11-slim-bullseye",
            failing_command="python -c 'import ssl'",
            failure_trace="...",
        )
        for r in results:
            print(f"{r.delta_id}: verified={r.fix_verified}")
"""

from .docker_runner import is_docker_available, verify_fix_docker, verify_fixes_docker
from .models import (
    FixCandidate,
    SandboxBackend,
    SandboxConfig,
    VerificationResult,
    VerificationStatus,
)
from .subprocess_fallback import verify_fix_subprocess, verify_fixes_subprocess


def get_sandbox_backend() -> SandboxBackend:
    """Detect the best available sandbox backend.

    Returns DOCKER if Docker daemon is reachable, SUBPROCESS otherwise.
    """
    if is_docker_available():
        return SandboxBackend.DOCKER
    return SandboxBackend.SUBPROCESS


def is_sandbox_available() -> bool:
    """Check whether any sandbox backend is available.

    The subprocess fallback is always available, so this always returns True.
    Use get_sandbox_backend() to check which backend will be used.
    """
    return True


def verify_fixes(
    fixes: list[FixCandidate],
    failing_command: str,
    failure_trace: str,
    base_image: str = "python:3.11-slim",
    config: SandboxConfig | None = None,
    force_backend: SandboxBackend | None = None,
) -> list[VerificationResult]:
    """Verify fix candidates using the best available backend.

    Automatically selects Docker if available, falls back to subprocess.
    Override with force_backend if needed.

    Args:
        fixes: Ordered list of fix candidates (highest priority first)
        failing_command: The command that fails in the bad environment
        failure_trace: Original failure trace for signature matching
        base_image: Docker image for the failing environment (Docker only)
        config: Resource caps; uses env-based defaults if None
        force_backend: Override automatic backend selection

    Returns:
        List of VerificationResult, one per tested fix.
        Stops early on first verified fix or when resource caps are hit.
    """
    config = config or SandboxConfig.from_env()
    backend = force_backend or get_sandbox_backend()

    if backend == SandboxBackend.DOCKER:
        return verify_fixes_docker(
            fixes=fixes,
            base_image=base_image,
            failing_command=failing_command,
            failure_trace=failure_trace,
            config=config,
        )
    else:
        return verify_fixes_subprocess(
            fixes=fixes,
            failing_command=failing_command,
            failure_trace=failure_trace,
            config=config,
        )


__all__ = [
    "FixCandidate",
    "SandboxBackend",
    "SandboxConfig",
    "VerificationResult",
    "VerificationStatus",
    "get_sandbox_backend",
    "is_docker_available",
    "is_sandbox_available",
    "verify_fix_docker",
    "verify_fix_subprocess",
    "verify_fixes",
    "verify_fixes_docker",
    "verify_fixes_subprocess",
]
