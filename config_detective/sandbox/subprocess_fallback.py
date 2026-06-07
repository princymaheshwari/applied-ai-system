"""Subprocess-based sandbox fallback for Windows and Docker-less environments.

When Docker is unavailable (common on Windows without Docker Desktop, or in
CI environments without DinD), this module provides partial verification
using isolated subprocesses. It cannot achieve full environment isolation
but can verify env-var fixes and simple package-version fixes by:

1. Creating a temporary directory as the working directory.
2. Optionally creating a temporary venv for package-level fixes.
3. Setting the candidate fix's env vars in the subprocess environment.
4. Running the failing command and capturing exit code + stderr.
5. Comparing against the original failure signature.

Limitations vs Docker:
- No OS-package isolation (cannot test "install libpq-dev" fixes)
- No filesystem isolation (commands share the host filesystem)
- No base-image simulation (cannot reproduce Alpine vs Debian differences)
- No resource caps (relies on OS-level process limits)
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .models import (
    FixCandidate,
    SandboxBackend,
    SandboxConfig,
    VerificationResult,
    VerificationStatus,
)
from .docker_runner import _extract_failure_signature

logger = logging.getLogger(__name__)


def _create_temp_venv(base_dir: Path) -> Path | None:
    """Create a temporary venv for package isolation.

    Returns the venv path, or None if creation fails.
    """
    venv_path = base_dir / ".sandbox_venv"
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_path)],
            capture_output=True,
            timeout=60,
            check=True,
        )
        return venv_path
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Failed to create sandbox venv: %s", exc)
        return None


def _get_venv_python(venv_path: Path) -> str:
    """Get the Python executable path inside a venv."""
    if sys.platform == "win32":
        return str(venv_path / "Scripts" / "python.exe")
    return str(venv_path / "bin" / "python")


def _run_command(
    command: str,
    env: dict[str, str],
    cwd: str,
    timeout: int,
) -> tuple[int, str, str]:
    """Run a shell command and capture output.

    Returns:
        Tuple of (exit_code, stdout, stderr)
    """
    shell = True
    if sys.platform == "win32":
        shell_cmd = command
    else:
        shell_cmd = command

    try:
        result = subprocess.run(
            shell_cmd,
            shell=shell,
            env=env,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            text=True,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except OSError as exc:
        return -1, "", f"Failed to execute command: {exc}"


def verify_fix_subprocess(
    fix: FixCandidate,
    failing_command: str,
    failure_trace: str,
    config: SandboxConfig | None = None,
    working_dir: str | None = None,
) -> VerificationResult:
    """Verify a fix candidate using a subprocess (no Docker required).

    Args:
        fix: The fix to verify
        failing_command: The command that fails
        failure_trace: Original failure trace for signature matching
        config: Resource limits
        working_dir: Working directory override; uses a temp dir if None

    Returns:
        VerificationResult
    """
    config = config or SandboxConfig()
    signature = _extract_failure_signature(failure_trace)
    start_time = time.time()
    temp_dir: str | None = None

    try:
        if working_dir:
            cwd = working_dir
        else:
            temp_dir = tempfile.mkdtemp(prefix="config_detective_sandbox_")
            cwd = temp_dir

        base_env = os.environ.copy()

        # --- Step 1: Run the failing command without the fix (baseline) ---
        exit_code_before, _, stderr_before = _run_command(
            command=failing_command,
            env=base_env,
            cwd=cwd,
            timeout=config.max_duration_seconds,
        )

        # --- Step 2: Apply fix and run again ---
        fix_env = base_env.copy()
        fix_env.update(fix.env_overrides)

        # If there are setup commands (pip install, etc.), run them first
        for cmd in fix.commands:
            cmd_exit, _, cmd_stderr = _run_command(
                command=cmd,
                env=fix_env,
                cwd=cwd,
                timeout=config.max_duration_seconds,
            )
            if cmd_exit != 0:
                elapsed = int((time.time() - start_time) * 1000)
                return VerificationResult(
                    fix_id=fix.fix_id,
                    delta_id=fix.delta_id,
                    status=VerificationStatus.ERROR,
                    exit_code_before=exit_code_before,
                    stderr_before=stderr_before[:2000],
                    backend=SandboxBackend.SUBPROCESS,
                    error_message=f"Setup command failed: {cmd} -> {cmd_stderr[:500]}",
                    duration_ms=elapsed,
                )

        exit_code_after, _, stderr_after = _run_command(
            command=failing_command,
            env=fix_env,
            cwd=cwd,
            timeout=config.max_duration_seconds,
        )

        # --- Step 3: Evaluate ---
        signature_still_present = signature.lower() in stderr_after.lower() if signature else True

        fix_verified = (
            exit_code_before != 0
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
            backend=SandboxBackend.SUBPROCESS,
            duration_ms=elapsed,
        )

    except Exception as exc:
        elapsed = int((time.time() - start_time) * 1000)
        return VerificationResult(
            fix_id=fix.fix_id,
            delta_id=fix.delta_id,
            status=VerificationStatus.ERROR,
            backend=SandboxBackend.SUBPROCESS,
            error_message=f"Subprocess verification error: {exc}",
            duration_ms=elapsed,
        )

    finally:
        if temp_dir:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass


def verify_fixes_subprocess(
    fixes: list[FixCandidate],
    failing_command: str,
    failure_trace: str,
    config: SandboxConfig | None = None,
) -> list[VerificationResult]:
    """Verify multiple fix candidates via subprocess, respecting caps.

    Stops early on first verified fix or when resource caps are hit.

    Args:
        fixes: Ordered list of fixes to test
        failing_command: The failing command
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
            "Subprocess verifying fix %d/%d: %s (%s)",
            i + 1, len(fixes), fix.delta_id, fix.description,
        )

        result = verify_fix_subprocess(
            fix=fix,
            failing_command=failing_command,
            failure_trace=failure_trace,
            config=config,
        )
        results.append(result)

        if result.fix_verified:
            logger.info("Fix verified via subprocess! Stopping early: %s", fix.delta_id)
            break

    return results
