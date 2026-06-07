"""Docker SDK sandbox runner — ephemeral containers with resource caps.

This module builds disposable Docker containers to empirically verify
candidate fixes. The verification protocol:

1. Build a container matching the *failing* environment.
2. Run the failing command — capture exit code + stderr (baseline).
3. Apply the candidate fix (env var swap, package pin, etc.).
4. Re-run the same command — capture exit code + stderr.
5. The fix is "verified" iff exit code goes from non-zero to zero
   AND the original failure-signature fingerprint disappears from stderr.

Resource caps are enforced via Docker's HostConfig:
- Memory limit (default 512 MB)
- CPU quota
- Network disabled (no internet from inside sandbox)
- Auto-remove on exit
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from .models import (
    FixCandidate,
    SandboxBackend,
    SandboxConfig,
    VerificationResult,
    VerificationStatus,
)

logger = logging.getLogger(__name__)

try:
    import docker
    from docker.errors import (
        APIError,
        BuildError,
        ContainerError,
        DockerException,
        ImageNotFound,
    )

    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False


def is_docker_available() -> bool:
    """Check whether Docker daemon is reachable."""
    if not HAS_DOCKER:
        return False
    try:
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


def _extract_failure_signature(failure_trace: str) -> str:
    """Extract a fingerprint from the failure trace for comparison.

    Focuses on the error type/message, stripping file paths and line numbers
    so the signature is stable across different container paths.
    """
    patterns = [
        r"(\w+Error): (.+?)(?:\n|$)",
        r"(\w+Exception): (.+?)(?:\n|$)",
        r"(\w+Warning): (.+?)(?:\n|$)",
        r"error: (.+?)(?:\n|$)",
        r"FATAL: (.+?)(?:\n|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, failure_trace, re.IGNORECASE)
        if match:
            return match.group(0).strip()

    lines = failure_trace.strip().splitlines()
    if lines:
        return lines[-1].strip()[:200]
    return failure_trace[:200]


def _build_baseline_command(failing_command: str, env_overrides: dict[str, str]) -> str:
    """Wrap the failing command with env overrides for shell execution."""
    parts = []
    for key, value in env_overrides.items():
        parts.append(f"export {key}='{value}'")
    if parts:
        parts.append(failing_command)
        return " && ".join(parts)
    return failing_command


def _build_fix_script(
    fix: FixCandidate,
    failing_command: str,
) -> str:
    """Build a shell script that applies the fix, then runs the failing command."""
    lines = ["#!/bin/sh", "set -e"]

    for key, value in fix.env_overrides.items():
        lines.append(f"export {key}='{value}'")

    for cmd in fix.commands:
        lines.append(cmd)

    lines.append("set +e")
    lines.append(failing_command)
    lines.append("exit $?")

    return "\n".join(lines)


def verify_fix_docker(
    fix: FixCandidate,
    base_image: str,
    failing_command: str,
    failure_trace: str,
    config: SandboxConfig | None = None,
) -> VerificationResult:
    """Verify a single fix candidate inside a Docker container.

    Args:
        fix: The fix to verify
        base_image: Docker image matching the failing environment
            (e.g. "python:3.11-slim-bullseye")
        failing_command: The command that fails in the bad environment
        failure_trace: The original failure trace for signature matching
        config: Resource caps; uses defaults if None

    Returns:
        VerificationResult with pass/fail and captured output
    """
    if not HAS_DOCKER:
        return VerificationResult(
            fix_id=fix.fix_id,
            delta_id=fix.delta_id,
            status=VerificationStatus.ERROR,
            backend=SandboxBackend.DOCKER,
            error_message="docker Python package is not installed",
        )

    config = config or SandboxConfig()
    signature = _extract_failure_signature(failure_trace)
    start_time = time.time()
    container_id = ""

    try:
        client = docker.from_env()
    except Exception as exc:
        return VerificationResult(
            fix_id=fix.fix_id,
            delta_id=fix.delta_id,
            status=VerificationStatus.ERROR,
            backend=SandboxBackend.DOCKER,
            error_message=f"Cannot connect to Docker daemon: {exc}",
            duration_ms=int((time.time() - start_time) * 1000),
        )

    # --- Step 1: Run the failing command WITHOUT the fix (baseline) ---
    exit_code_before: int | None = None
    stderr_before = ""
    try:
        baseline_result = client.containers.run(
            image=base_image,
            command=["sh", "-c", failing_command],
            detach=False,
            stdout=True,
            stderr=True,
            remove=config.auto_remove,
            mem_limit=config.memory_limit,
            cpu_period=config.cpu_period,
            cpu_quota=config.cpu_quota,
            network_disabled=config.network_disabled,
        )
        stderr_before = baseline_result.decode("utf-8", errors="replace") if baseline_result else ""
        exit_code_before = 0
    except ContainerError as exc:
        exit_code_before = exc.exit_status
        stderr_before = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
    except (APIError, ImageNotFound) as exc:
        return VerificationResult(
            fix_id=fix.fix_id,
            delta_id=fix.delta_id,
            status=VerificationStatus.ERROR,
            backend=SandboxBackend.DOCKER,
            error_message=f"Docker baseline run failed: {exc}",
            duration_ms=int((time.time() - start_time) * 1000),
        )

    # --- Step 2: Run WITH the fix applied ---
    fix_script = _build_fix_script(fix, failing_command)
    exit_code_after: int | None = None
    stderr_after = ""

    try:
        container = client.containers.run(
            image=base_image,
            command=["sh", "-c", fix_script],
            detach=True,
            stdout=True,
            stderr=True,
            mem_limit=config.memory_limit,
            cpu_period=config.cpu_period,
            cpu_quota=config.cpu_quota,
            network_disabled=config.network_disabled,
        )
        container_id = container.short_id

        wait_result = container.wait(timeout=config.max_duration_seconds)
        exit_code_after = wait_result.get("StatusCode", -1)

        logs = container.logs(stdout=True, stderr=True)
        stderr_after = logs.decode("utf-8", errors="replace") if logs else ""

        if config.auto_remove:
            try:
                container.remove(force=True)
            except Exception:
                pass

    except ContainerError as exc:
        exit_code_after = exc.exit_status
        stderr_after = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
    except Exception as exc:
        elapsed = int((time.time() - start_time) * 1000)
        error_msg = str(exc)
        status = VerificationStatus.TIMEOUT if "timed out" in error_msg.lower() else VerificationStatus.ERROR
        return VerificationResult(
            fix_id=fix.fix_id,
            delta_id=fix.delta_id,
            status=status,
            exit_code_before=exit_code_before,
            stderr_before=stderr_before[:2000],
            backend=SandboxBackend.DOCKER,
            error_message=f"Docker fix run failed: {error_msg}",
            duration_ms=elapsed,
            container_id=container_id,
        )

    # --- Step 3: Determine if the fix is verified ---
    signature_still_present = signature.lower() in stderr_after.lower() if signature else True

    fix_verified = (
        exit_code_before is not None
        and exit_code_before != 0
        and exit_code_after == 0
        and not signature_still_present
    )

    if exit_code_before == 0:
        status = VerificationStatus.SKIPPED
    elif fix_verified:
        status = VerificationStatus.PASSED
    else:
        status = VerificationStatus.FAILED

    elapsed = int((time.time() - start_time) * 1000)

    return VerificationResult(
        fix_id=fix.fix_id,
        delta_id=fix.delta_id,
        status=status,
        exit_code_before=exit_code_before,
        exit_code_after=exit_code_after,
        stderr_before=stderr_before[:2000],
        stderr_after=stderr_after[:2000],
        failure_signature_present=signature_still_present,
        fix_verified=fix_verified,
        backend=SandboxBackend.DOCKER,
        duration_ms=elapsed,
        container_id=container_id,
    )


def verify_fixes_docker(
    fixes: list[FixCandidate],
    base_image: str,
    failing_command: str,
    failure_trace: str,
    config: SandboxConfig | None = None,
) -> list[VerificationResult]:
    """Verify multiple fix candidates sequentially, respecting resource caps.

    Stops early if:
    - A fix is verified (pass) — no need to test the rest
    - max_runs cap is hit
    - max_total_seconds is exceeded

    Args:
        fixes: Ordered list of fix candidates to test
        base_image: Docker image for the failing environment
        failing_command: The failing shell command
        failure_trace: Original failure trace
        config: Resource limits

    Returns:
        List of VerificationResult, one per tested fix
    """
    config = config or SandboxConfig()
    results: list[VerificationResult] = []
    total_start = time.time()

    for i, fix in enumerate(fixes):
        if i >= config.max_runs:
            logger.warning("Hit max sandbox runs cap (%d)", config.max_runs)
            break

        elapsed_total = time.time() - total_start
        if elapsed_total >= config.max_total_seconds:
            logger.warning("Hit total time cap (%.0fs)", elapsed_total)
            break

        logger.info(
            "Verifying fix %d/%d: %s (%s)",
            i + 1, len(fixes), fix.delta_id, fix.description,
        )

        result = verify_fix_docker(
            fix=fix,
            base_image=base_image,
            failing_command=failing_command,
            failure_trace=failure_trace,
            config=config,
        )
        results.append(result)

        if result.fix_verified:
            logger.info("Fix verified! Stopping early: %s", fix.delta_id)
            break

    return results
