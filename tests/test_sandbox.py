"""Tests for the sandbox verifier module (Phase 6).

This module tests:
- Data models (FixCandidate, VerificationResult, SandboxConfig)
- Failure signature extraction
- Docker runner (with mocked Docker SDK)
- Subprocess fallback (with mocked subprocess calls)
- Backend auto-selection logic
- Resource cap enforcement (max_runs, max_total_seconds)
- Verifier agent node integration
- End-to-end verification flow
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from config_detective.sandbox.models import (
    FixCandidate,
    SandboxBackend,
    SandboxConfig,
    VerificationResult,
    VerificationStatus,
)
from config_detective.sandbox.docker_runner import (
    _extract_failure_signature,
    verify_fix_docker,
    verify_fixes_docker,
)
from config_detective.sandbox.subprocess_fallback import (
    verify_fix_subprocess,
    verify_fixes_subprocess,
)
from config_detective.sandbox import (
    get_sandbox_backend,
    is_sandbox_available,
    verify_fixes,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def simple_env_fix() -> FixCandidate:
    """A simple env-var fix candidate."""
    return FixCandidate(
        fix_id="fix-001",
        delta_id="env:LANG",
        fix_type="env_var",
        description="Set LANG to en_US.UTF-8",
        env_overrides={"LANG": "en_US.UTF-8"},
    )


@pytest.fixture
def pip_install_fix() -> FixCandidate:
    """A package-install fix candidate."""
    return FixCandidate(
        fix_id="fix-002",
        delta_id="python_package:cryptography",
        fix_type="python_package",
        description="Pin cryptography to 41.0.0",
        commands=["pip install cryptography==41.0.0"],
    )


@pytest.fixture
def locale_failure_trace() -> str:
    """A failure trace with a locale-related error."""
    return (
        "Traceback (most recent call last):\n"
        '  File "app.py", line 42, in process\n'
        "    data = f.read()\n"
        "UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2"
    )


@pytest.fixture
def tight_config() -> SandboxConfig:
    """Config with tight resource caps for testing."""
    return SandboxConfig(
        max_runs=2,
        max_duration_seconds=10,
        max_total_seconds=30,
        memory_limit="256m",
    )


# =============================================================================
# Model tests
# =============================================================================


class TestFixCandidate:
    """Tests for the FixCandidate data model."""

    def test_default_creation(self):
        fix = FixCandidate()
        assert fix.fix_id  # auto-generated
        assert fix.delta_id == ""
        assert fix.env_overrides == {}
        assert fix.commands == []
        assert fix.dockerfile_patch is None

    def test_creation_with_values(self, simple_env_fix):
        assert simple_env_fix.fix_id == "fix-001"
        assert simple_env_fix.delta_id == "env:LANG"
        assert simple_env_fix.env_overrides == {"LANG": "en_US.UTF-8"}

    def test_serialization_round_trip(self, simple_env_fix):
        d = simple_env_fix.to_dict()
        restored = FixCandidate.from_dict(d)
        assert restored.fix_id == simple_env_fix.fix_id
        assert restored.delta_id == simple_env_fix.delta_id
        assert restored.env_overrides == simple_env_fix.env_overrides

    def test_serialization_with_dockerfile_patch(self):
        fix = FixCandidate(
            dockerfile_patch=(5, "FROM python:3.10", "FROM python:3.11"),
        )
        d = fix.to_dict()
        assert d["dockerfile_patch"] == [5, "FROM python:3.10", "FROM python:3.11"]
        restored = FixCandidate.from_dict(d)
        assert restored.dockerfile_patch == (5, "FROM python:3.10", "FROM python:3.11")

    def test_serialization_without_dockerfile_patch(self):
        fix = FixCandidate()
        d = fix.to_dict()
        assert d["dockerfile_patch"] is None
        restored = FixCandidate.from_dict(d)
        assert restored.dockerfile_patch is None


class TestVerificationResult:
    """Tests for the VerificationResult data model."""

    def test_default_is_skipped(self):
        result = VerificationResult()
        assert result.status == VerificationStatus.SKIPPED
        assert result.fix_verified is False
        assert result.backend == SandboxBackend.NONE

    def test_verified_result(self):
        result = VerificationResult(
            fix_id="fix-001",
            delta_id="env:LANG",
            status=VerificationStatus.PASSED,
            exit_code_before=1,
            exit_code_after=0,
            failure_signature_present=False,
            fix_verified=True,
            backend=SandboxBackend.DOCKER,
            duration_ms=1500,
        )
        assert result.fix_verified is True
        assert result.status == VerificationStatus.PASSED

    def test_serialization_round_trip(self):
        result = VerificationResult(
            fix_id="fix-001",
            delta_id="env:LANG",
            status=VerificationStatus.PASSED,
            exit_code_before=1,
            exit_code_after=0,
            stderr_before="UnicodeDecodeError: 'ascii'",
            stderr_after="",
            failure_signature_present=False,
            fix_verified=True,
            backend=SandboxBackend.DOCKER,
            duration_ms=1500,
            container_id="abc123",
        )
        d = result.to_dict()
        restored = VerificationResult.from_dict(d)
        assert restored.fix_id == "fix-001"
        assert restored.fix_verified is True
        assert restored.backend == SandboxBackend.DOCKER

    def test_stderr_truncation(self):
        long_stderr = "x" * 5000
        result = VerificationResult(stderr_before=long_stderr, stderr_after=long_stderr)
        d = result.to_dict()
        assert len(d["stderr_before"]) == 2000
        assert len(d["stderr_after"]) == 2000


class TestSandboxConfig:
    """Tests for the SandboxConfig data model."""

    def test_defaults(self):
        config = SandboxConfig()
        assert config.max_runs == 10
        assert config.max_duration_seconds == 300
        assert config.memory_limit == "512m"
        assert config.network_disabled is True
        assert config.auto_remove is True

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("MAX_SANDBOX_RUNS", "5")
        monkeypatch.setenv("MAX_INVESTIGATION_SECONDS", "120")
        config = SandboxConfig.from_env()
        assert config.max_runs == 5
        assert config.max_duration_seconds == 120

    def test_from_env_defaults(self, monkeypatch):
        monkeypatch.delenv("MAX_SANDBOX_RUNS", raising=False)
        monkeypatch.delenv("MAX_INVESTIGATION_SECONDS", raising=False)
        config = SandboxConfig.from_env()
        assert config.max_runs == 10
        assert config.max_duration_seconds == 300


# =============================================================================
# Failure signature extraction tests
# =============================================================================


class TestFailureSignature:
    """Tests for failure signature extraction."""

    def test_unicode_error(self, locale_failure_trace):
        sig = _extract_failure_signature(locale_failure_trace)
        assert "UnicodeDecodeError" in sig

    def test_import_error(self):
        trace = "ImportError: No module named 'cryptography'"
        sig = _extract_failure_signature(trace)
        assert "ImportError" in sig

    def test_generic_error(self):
        trace = "error: failed to compile openssl"
        sig = _extract_failure_signature(trace)
        assert "error" in sig.lower()

    def test_multiline_takes_error_line(self):
        trace = (
            "Running tests...\n"
            "test_auth.py::test_login FAILED\n"
            "AssertionError: expected 200, got 500\n"
            "1 failed, 10 passed"
        )
        sig = _extract_failure_signature(trace)
        assert "AssertionError" in sig or "expected" in sig

    def test_empty_trace(self):
        sig = _extract_failure_signature("")
        assert sig == ""

    def test_truncation_of_long_trace(self):
        trace = "x" * 500
        sig = _extract_failure_signature(trace)
        assert len(sig) <= 200


# =============================================================================
# Docker runner tests (mocked)
# =============================================================================


class TestDockerRunner:
    """Tests for Docker-based sandbox verification (mocked Docker SDK)."""

    @patch("config_detective.sandbox.docker_runner.HAS_DOCKER", False)
    def test_no_docker_package(self, simple_env_fix, locale_failure_trace):
        result = verify_fix_docker(
            fix=simple_env_fix,
            base_image="python:3.11-slim",
            failing_command="python app.py",
            failure_trace=locale_failure_trace,
        )
        assert result.status == VerificationStatus.ERROR
        assert "not installed" in result.error_message

    @patch("config_detective.sandbox.docker_runner.HAS_DOCKER", True)
    @patch("config_detective.sandbox.docker_runner.docker")
    def test_docker_daemon_unreachable(self, mock_docker, simple_env_fix, locale_failure_trace):
        mock_docker.from_env.side_effect = Exception("Connection refused")
        result = verify_fix_docker(
            fix=simple_env_fix,
            base_image="python:3.11-slim",
            failing_command="python app.py",
            failure_trace=locale_failure_trace,
        )
        assert result.status == VerificationStatus.ERROR
        assert "Cannot connect" in result.error_message

    @patch("config_detective.sandbox.docker_runner.HAS_DOCKER", True)
    @patch("config_detective.sandbox.docker_runner.docker")
    def test_verified_fix(self, mock_docker, simple_env_fix, locale_failure_trace):
        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client

        # Baseline: fails with the error
        from config_detective.sandbox.docker_runner import ContainerError
        mock_client.containers.run.side_effect = [
            ContainerError(
                container=MagicMock(),
                exit_status=1,
                command="python app.py",
                image="python:3.11-slim",
                stderr=b"UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2",
            ),
        ]

        # Fix run: succeeds
        mock_container = MagicMock()
        mock_container.short_id = "abc123"
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.return_value = b"All tests passed"
        mock_client.containers.run.side_effect = [
            # First call raises ContainerError (baseline)
            ContainerError(
                container=MagicMock(),
                exit_status=1,
                command="python app.py",
                image="python:3.11-slim",
                stderr=b"UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2",
            ),
            # Second call returns the mock container (fix run, detached)
            mock_container,
        ]

        result = verify_fix_docker(
            fix=simple_env_fix,
            base_image="python:3.11-slim",
            failing_command="python app.py",
            failure_trace=locale_failure_trace,
        )
        assert result.fix_verified is True
        assert result.status == VerificationStatus.PASSED
        assert result.exit_code_before == 1
        assert result.exit_code_after == 0
        assert result.backend == SandboxBackend.DOCKER

    @patch("config_detective.sandbox.docker_runner.HAS_DOCKER", True)
    @patch("config_detective.sandbox.docker_runner.docker")
    def test_unverified_fix(self, mock_docker, simple_env_fix, locale_failure_trace):
        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client

        # Baseline: fails
        from config_detective.sandbox.docker_runner import ContainerError
        mock_client.containers.run.side_effect = [
            ContainerError(
                container=MagicMock(),
                exit_status=1,
                command="python app.py",
                image="python:3.11-slim",
                stderr=b"UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2",
            ),
        ]

        # Fix run: also fails
        mock_container = MagicMock()
        mock_container.short_id = "def456"
        mock_container.wait.return_value = {"StatusCode": 1}
        mock_container.logs.return_value = b"UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2"
        mock_client.containers.run.side_effect = [
            ContainerError(
                container=MagicMock(),
                exit_status=1,
                command="python app.py",
                image="python:3.11-slim",
                stderr=b"UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2",
            ),
            mock_container,
        ]

        result = verify_fix_docker(
            fix=simple_env_fix,
            base_image="python:3.11-slim",
            failing_command="python app.py",
            failure_trace=locale_failure_trace,
        )
        assert result.fix_verified is False
        assert result.status == VerificationStatus.FAILED

    def test_max_runs_cap(self, locale_failure_trace):
        fixes = [
            FixCandidate(fix_id=f"fix-{i}", delta_id=f"env:VAR{i}")
            for i in range(5)
        ]
        config = SandboxConfig(max_runs=2)

        with patch(
            "config_detective.sandbox.docker_runner.verify_fix_docker"
        ) as mock_verify:
            mock_verify.return_value = VerificationResult(
                status=VerificationStatus.FAILED,
                fix_verified=False,
            )
            results = verify_fixes_docker(
                fixes=fixes,
                base_image="python:3.11-slim",
                failing_command="python app.py",
                failure_trace=locale_failure_trace,
                config=config,
            )
            assert len(results) <= 2

    def test_early_stop_on_verified(self, locale_failure_trace):
        fixes = [
            FixCandidate(fix_id=f"fix-{i}", delta_id=f"env:VAR{i}")
            for i in range(5)
        ]

        with patch(
            "config_detective.sandbox.docker_runner.verify_fix_docker"
        ) as mock_verify:
            mock_verify.return_value = VerificationResult(
                status=VerificationStatus.PASSED,
                fix_verified=True,
            )
            results = verify_fixes_docker(
                fixes=fixes,
                base_image="python:3.11-slim",
                failing_command="python app.py",
                failure_trace=locale_failure_trace,
            )
            assert len(results) == 1


# =============================================================================
# Subprocess fallback tests
# =============================================================================


class TestSubprocessFallback:
    """Tests for subprocess-based sandbox verification."""

    @patch("config_detective.sandbox.subprocess_fallback._run_command")
    def test_verified_env_var_fix(self, mock_run, simple_env_fix, locale_failure_trace):
        mock_run.side_effect = [
            (1, "", "UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2"),
            (0, "OK", ""),
        ]
        result = verify_fix_subprocess(
            fix=simple_env_fix,
            failing_command="python app.py",
            failure_trace=locale_failure_trace,
            working_dir=".",
        )
        assert result.fix_verified is True
        assert result.status == VerificationStatus.PASSED
        assert result.backend == SandboxBackend.SUBPROCESS
        assert result.exit_code_before == 1
        assert result.exit_code_after == 0

    @patch("config_detective.sandbox.subprocess_fallback._run_command")
    def test_unverified_fix(self, mock_run, simple_env_fix, locale_failure_trace):
        mock_run.side_effect = [
            (1, "", "UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2"),
            (1, "", "UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2"),
        ]
        result = verify_fix_subprocess(
            fix=simple_env_fix,
            failing_command="python app.py",
            failure_trace=locale_failure_trace,
            working_dir=".",
        )
        assert result.fix_verified is False
        assert result.status == VerificationStatus.FAILED

    @patch("config_detective.sandbox.subprocess_fallback._run_command")
    def test_baseline_passes_skips(self, mock_run, simple_env_fix, locale_failure_trace):
        mock_run.side_effect = [
            (0, "OK", ""),
            (0, "OK", ""),
        ]
        result = verify_fix_subprocess(
            fix=simple_env_fix,
            failing_command="python app.py",
            failure_trace=locale_failure_trace,
            working_dir=".",
        )
        assert result.status == VerificationStatus.SKIPPED

    @patch("config_detective.sandbox.subprocess_fallback._run_command")
    def test_setup_command_failure(self, mock_run, pip_install_fix, locale_failure_trace):
        mock_run.side_effect = [
            (1, "", "some error"),
            (1, "", "pip: command not found"),
        ]
        result = verify_fix_subprocess(
            fix=pip_install_fix,
            failing_command="python app.py",
            failure_trace=locale_failure_trace,
            working_dir=".",
        )
        assert result.status == VerificationStatus.ERROR
        assert "Setup command failed" in result.error_message

    @patch("config_detective.sandbox.subprocess_fallback._run_command")
    def test_max_runs_cap_subprocess(self, mock_run, locale_failure_trace):
        fixes = [
            FixCandidate(fix_id=f"fix-{i}", delta_id=f"env:VAR{i}")
            for i in range(5)
        ]
        mock_run.return_value = (1, "", "error")
        config = SandboxConfig(max_runs=2)

        results = verify_fixes_subprocess(
            fixes=fixes,
            failing_command="python app.py",
            failure_trace=locale_failure_trace,
            config=config,
        )
        assert len(results) <= 2

    @patch("config_detective.sandbox.subprocess_fallback._run_command")
    def test_early_stop_subprocess(self, mock_run, locale_failure_trace):
        fixes = [
            FixCandidate(fix_id=f"fix-{i}", delta_id=f"env:VAR{i}")
            for i in range(5)
        ]
        mock_run.side_effect = [
            (1, "", "error"),
            (0, "OK", ""),
        ]

        results = verify_fixes_subprocess(
            fixes=fixes,
            failing_command="python app.py",
            failure_trace=locale_failure_trace,
        )
        assert len(results) == 1
        assert results[0].fix_verified is True


# =============================================================================
# Backend selection tests
# =============================================================================


class TestBackendSelection:
    """Tests for automatic backend selection."""

    def test_is_sandbox_always_available(self):
        assert is_sandbox_available() is True

    @patch("config_detective.sandbox.is_docker_available", return_value=True)
    def test_docker_preferred(self, _):
        assert get_sandbox_backend() == SandboxBackend.DOCKER

    @patch("config_detective.sandbox.is_docker_available", return_value=False)
    def test_subprocess_fallback(self, _):
        assert get_sandbox_backend() == SandboxBackend.SUBPROCESS

    @patch("config_detective.sandbox.is_docker_available", return_value=False)
    @patch("config_detective.sandbox.verify_fixes_subprocess")
    def test_verify_fixes_uses_subprocess_when_no_docker(
        self, mock_sub, mock_docker_check, locale_failure_trace
    ):
        mock_sub.return_value = []
        verify_fixes(
            fixes=[],
            failing_command="python app.py",
            failure_trace=locale_failure_trace,
        )
        mock_sub.assert_called_once()

    @patch("config_detective.sandbox.verify_fixes_docker")
    def test_force_docker_backend(self, mock_docker, locale_failure_trace):
        mock_docker.return_value = []
        verify_fixes(
            fixes=[],
            failing_command="python app.py",
            failure_trace=locale_failure_trace,
            force_backend=SandboxBackend.DOCKER,
        )
        mock_docker.assert_called_once()

    @patch("config_detective.sandbox.verify_fixes_subprocess")
    def test_force_subprocess_backend(self, mock_sub, locale_failure_trace):
        mock_sub.return_value = []
        verify_fixes(
            fixes=[],
            failing_command="python app.py",
            failure_trace=locale_failure_trace,
            force_backend=SandboxBackend.SUBPROCESS,
        )
        mock_sub.assert_called_once()


# =============================================================================
# Verifier agent node tests
# =============================================================================


class TestVerifierNode:
    """Tests for the verifier agent node."""

    @patch("config_detective.agents.nodes.verifier.verify_fixes")
    @patch("config_detective.agents.nodes.verifier.get_sandbox_backend")
    def test_verifier_annotates_hypotheses(self, mock_backend, mock_verify):
        from config_detective.agents.nodes.verifier import verifier_node
        from config_detective.agents.trace import reset_trace_store

        reset_trace_store()
        mock_backend.return_value = SandboxBackend.SUBPROCESS

        mock_verify.return_value = [
            VerificationResult(
                fix_id="fix-001",
                delta_id="env:LANG",
                status=VerificationStatus.PASSED,
                exit_code_before=1,
                exit_code_after=0,
                fix_verified=True,
                backend=SandboxBackend.SUBPROCESS,
                duration_ms=500,
            ),
        ]

        state = {
            "trace_id": "test-123",
            "hypotheses": [
                {
                    "id": "h1",
                    "delta_id": "env:LANG",
                    "delta_type": "value_changed",
                    "fix_suggestion": "Set LANG to en_US.UTF-8",
                    "fix_code": "export LANG='en_US.UTF-8'",
                    "confidence": 0.6,
                    "supporting_evidence": [],
                },
            ],
            "failure_trace": "UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2",
            "snapshot_b_dict": {"runtimes": {"python": "3.11.0"}},
            "reasoning_chain": [],
        }

        result = verifier_node(state)
        hypotheses = result["hypotheses"]
        assert len(hypotheses) == 1
        assert hypotheses[0]["verification"]["fix_verified"] is True
        assert hypotheses[0]["confidence"] > 0.6
        assert "Sandbox verified" in hypotheses[0]["supporting_evidence"][-1]
        assert "Verifier:" in result["reasoning_chain"][-1]

    @patch("config_detective.agents.nodes.verifier.verify_fixes")
    @patch("config_detective.agents.nodes.verifier.get_sandbox_backend")
    def test_verifier_no_hypotheses(self, mock_backend, mock_verify):
        from config_detective.agents.nodes.verifier import verifier_node
        from config_detective.agents.trace import reset_trace_store

        reset_trace_store()
        mock_backend.return_value = SandboxBackend.SUBPROCESS

        state = {
            "trace_id": "test-456",
            "hypotheses": [],
            "failure_trace": "error",
            "snapshot_b_dict": {},
            "reasoning_chain": [],
        }

        result = verifier_node(state)
        assert "skipping sandbox" in result["reasoning_chain"][-1].lower()

    @patch("config_detective.agents.nodes.verifier.verify_fixes")
    @patch("config_detective.agents.nodes.verifier.get_sandbox_backend")
    def test_verifier_penalises_failed(self, mock_backend, mock_verify):
        from config_detective.agents.nodes.verifier import verifier_node
        from config_detective.agents.trace import reset_trace_store

        reset_trace_store()
        mock_backend.return_value = SandboxBackend.SUBPROCESS

        mock_verify.return_value = [
            VerificationResult(
                fix_id="fix-001",
                delta_id="env:LANG",
                status=VerificationStatus.FAILED,
                exit_code_before=1,
                exit_code_after=1,
                fix_verified=False,
                backend=SandboxBackend.SUBPROCESS,
            ),
        ]

        state = {
            "trace_id": "test-789",
            "hypotheses": [
                {
                    "id": "h1",
                    "delta_id": "env:LANG",
                    "delta_type": "value_changed",
                    "fix_suggestion": "Set LANG",
                    "fix_code": "export LANG='en_US.UTF-8'",
                    "confidence": 0.7,
                    "supporting_evidence": [],
                },
            ],
            "failure_trace": "UnicodeDecodeError",
            "snapshot_b_dict": {},
            "reasoning_chain": [],
        }

        result = verifier_node(state)
        assert result["hypotheses"][0]["confidence"] < 0.7

    @patch("config_detective.agents.nodes.verifier.verify_fixes")
    @patch("config_detective.agents.nodes.verifier.get_sandbox_backend")
    def test_verifier_handles_sandbox_exception(self, mock_backend, mock_verify):
        from config_detective.agents.nodes.verifier import verifier_node
        from config_detective.agents.trace import reset_trace_store

        reset_trace_store()
        mock_backend.return_value = SandboxBackend.SUBPROCESS
        mock_verify.side_effect = RuntimeError("Sandbox exploded")

        state = {
            "trace_id": "test-err",
            "hypotheses": [{"id": "h1", "delta_id": "env:X", "fix_code": None, "fix_suggestion": "test", "confidence": 0.5, "supporting_evidence": [], "delta_type": "value_changed"}],
            "failure_trace": "error",
            "snapshot_b_dict": {},
            "reasoning_chain": [],
        }

        result = verifier_node(state)
        assert "failed" in result["reasoning_chain"][-1].lower()


# =============================================================================
# Helper function tests
# =============================================================================


class TestVerifierHelpers:
    """Tests for verifier node helper functions."""

    def test_hypothesis_to_fix_candidate_env_var(self):
        from config_detective.agents.nodes.verifier import _hypothesis_to_fix_candidate

        h = {
            "delta_id": "env:LANG",
            "fix_code": "export LANG='en_US.UTF-8'",
            "fix_suggestion": "Set LANG to en_US.UTF-8",
            "delta_type": "value_changed",
        }
        fix = _hypothesis_to_fix_candidate(h)
        assert fix.delta_id == "env:LANG"
        assert fix.env_overrides.get("LANG") == "en_US.UTF-8"

    def test_hypothesis_to_fix_candidate_pip(self):
        from config_detective.agents.nodes.verifier import _hypothesis_to_fix_candidate

        h = {
            "delta_id": "python_package:requests",
            "fix_code": "pip install requests==2.31.0",
            "fix_suggestion": "Pin requests",
            "delta_type": "version_changed",
        }
        fix = _hypothesis_to_fix_candidate(h)
        assert "pip install requests==2.31.0" in fix.commands

    def test_extract_base_image_from_dockerfile(self):
        from config_detective.agents.nodes.verifier import _extract_base_image

        state = {
            "snapshot_b_dict": {
                "dockerfiles": [{"base_image": "python:3.10-bullseye"}],
            },
        }
        assert _extract_base_image(state) == "python:3.10-bullseye"

    def test_extract_base_image_from_runtime(self):
        from config_detective.agents.nodes.verifier import _extract_base_image

        state = {
            "snapshot_b_dict": {
                "dockerfiles": [],
                "runtime_versions": {"python": "3.12.1"},
            },
        }
        assert _extract_base_image(state) == "python:3.12-slim"

    def test_extract_base_image_fallback(self):
        from config_detective.agents.nodes.verifier import _extract_base_image

        state = {"snapshot_b_dict": {}}
        assert _extract_base_image(state) == "python:3.11-slim"

    def test_extract_failing_command_pytest(self):
        from config_detective.agents.nodes.verifier import _extract_failing_command

        state = {"failure_trace": "pytest test_auth.py FAILED\nAssertionError"}
        cmd = _extract_failing_command(state)
        assert "pytest" in cmd

    def test_extract_failing_command_python(self):
        from config_detective.agents.nodes.verifier import _extract_failing_command

        state = {"failure_trace": "$ python app.py\nTraceback...\nImportError: ssl"}
        cmd = _extract_failing_command(state)
        assert "python" in cmd
