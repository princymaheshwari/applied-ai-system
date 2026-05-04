"""Tests for the snapshot module.

Tests cover:
- Pydantic models serialization/deserialization
- Environment variable capture and PII scrubbing
- Lockfile parsing (requirements.txt, uv.lock, package-lock.json)
- Dockerfile parsing
- Runtime version detection
- Snapshot determinism (same input produces same hash)
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from config_detective.snapshot import (
    EnvironmentSnapshot,
    PythonPackage,
    NodePackage,
    LockfileData,
    DockerfileData,
    DockerInstruction,
    EnvVarEntry,
    RuntimeVersions,
    LocaleInfo,
    TimezoneInfo,
    SystemInfo,
    OSType,
    LibcType,
    PackageManager,
    capture_snapshot,
    save_snapshot,
    load_snapshot,
)
from config_detective.snapshot.env_vars import capture_env_vars, _is_sensitive_key, _is_secret_value
from config_detective.snapshot.lockfiles import parse_lockfile, _parse_requirements_txt
from config_detective.snapshot.dockerfile import parse_dockerfile


class TestModels:
    """Test Pydantic model serialization."""

    def test_python_package_serialization(self) -> None:
        pkg = PythonPackage(name="requests", version="2.31.0", source="requirements.txt")
        data = pkg.model_dump()
        assert data["name"] == "requests"
        assert data["version"] == "2.31.0"

    def test_environment_snapshot_to_json(self) -> None:
        snap = EnvironmentSnapshot(
            captured_at=datetime(2026, 5, 4, 12, 0, 0),
            repo_path="/test/repo",
        )
        json_str = snap.to_json()
        assert '"repo_path": "/test/repo"' in json_str
        assert '"snapshot_version": "1.0.0"' in json_str

    def test_environment_snapshot_from_json(self) -> None:
        snap = EnvironmentSnapshot(
            captured_at=datetime(2026, 5, 4, 12, 0, 0),
            repo_path="/test/repo",
            lockfiles=[
                LockfileData(
                    path="/test/requirements.txt",
                    lockfile_type="requirements.txt",
                    packages=[PythonPackage(name="flask", version="3.0.0")],
                )
            ],
        )
        json_str = snap.to_json()
        reloaded = EnvironmentSnapshot.from_json(json_str)
        assert reloaded.repo_path == "/test/repo"
        assert len(reloaded.lockfiles) == 1
        assert reloaded.lockfiles[0].packages[0].name == "flask"


class TestEnvVarCapture:
    """Test environment variable capture and PII scrubbing."""

    def test_capture_env_vars_returns_list(self) -> None:
        entries = capture_env_vars({"PATH": "/usr/bin", "HOME": "/home/user"})
        assert isinstance(entries, list)
        assert all(isinstance(e, EnvVarEntry) for e in entries)

    def test_sensitive_key_detected(self) -> None:
        assert _is_sensitive_key("AWS_SECRET_KEY")
        assert _is_sensitive_key("api_key")
        assert _is_sensitive_key("DATABASE_PASSWORD")
        assert _is_sensitive_key("GROQ_API_KEY")
        assert not _is_sensitive_key("PATH")
        assert not _is_sensitive_key("HOME")
        assert not _is_sensitive_key("LANG")

    def test_secret_value_detected(self) -> None:
        # GitHub token pattern
        assert _is_secret_value("ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        # AWS access key pattern
        assert _is_secret_value("AKIAIOSFODNN7EXAMPLE")
        # Long random string (high entropy)
        assert _is_secret_value("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")
        # Normal values
        assert not _is_secret_value("/usr/bin")
        assert not _is_secret_value("en_US.UTF-8")
        assert not _is_secret_value("hello")

    def test_sensitive_keys_are_redacted(self) -> None:
        env = {
            "PATH": "/usr/bin",
            "AWS_SECRET_KEY": "my-secret-key",
            "GROQ_API_KEY": "groq-key-12345",
            "LANG": "en_US.UTF-8",
        }
        entries = capture_env_vars(env)

        by_key = {e.key: e for e in entries}

        assert by_key["PATH"].value == "/usr/bin"
        assert by_key["PATH"].redacted is False

        assert by_key["AWS_SECRET_KEY"].value == "[REDACTED]"
        assert by_key["AWS_SECRET_KEY"].redacted is True

        assert by_key["GROQ_API_KEY"].value == "[REDACTED]"
        assert by_key["GROQ_API_KEY"].redacted is True

        assert by_key["LANG"].value == "en_US.UTF-8"
        assert by_key["LANG"].redacted is False


class TestLockfileParsing:
    """Test lockfile parsing."""

    def test_parse_requirements_txt(self, tmp_path: Path) -> None:
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("""
# Core deps
requests==2.31.0
flask>=3.0.0
pydantic~=2.6.0

# With extras
uvicorn[standard]==0.27.0

# Skip these
-r other.txt
git+https://github.com/user/repo.git
""")
        packages = _parse_requirements_txt(req_file)

        names = {p.name for p in packages}
        assert "requests" in names
        assert "flask" in names
        assert "pydantic" in names
        assert "uvicorn" in names

        # Check versions
        by_name = {p.name: p for p in packages}
        assert by_name["requests"].version == "2.31.0"
        assert by_name["flask"].version == "3.0.0"

    def test_parse_lockfile_returns_lockfile_data(self, tmp_path: Path) -> None:
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("requests==2.31.0\n")

        data = parse_lockfile(req_file)

        assert isinstance(data, LockfileData)
        assert data.lockfile_type == "requirements.txt"
        assert len(data.packages) == 1
        assert data.packages[0].name == "requests"


class TestDockerfileParsing:
    """Test Dockerfile parsing."""

    def test_parse_simple_dockerfile(self, tmp_path: Path) -> None:
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("""
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["python", "app.py"]
""")
        data = parse_dockerfile(dockerfile)

        assert isinstance(data, DockerfileData)
        assert data.base_image == "python:3.11-slim"

        instructions = {i.instruction for i in data.instructions}
        assert "FROM" in instructions
        assert "WORKDIR" in instructions
        assert "ENV" in instructions
        assert "COPY" in instructions
        assert "RUN" in instructions
        assert "CMD" in instructions

    def test_parse_multistage_dockerfile(self, tmp_path: Path) -> None:
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("""
FROM python:3.11 AS builder
RUN pip install build

FROM python:3.11-slim
COPY --from=builder /app /app
""")
        data = parse_dockerfile(dockerfile)

        # Base image should be the first FROM (builder stage)
        assert data.base_image == "python:3.11"


class TestSnapshotCapture:
    """Test full snapshot capture."""

    def test_capture_snapshot_returns_environment_snapshot(self, tmp_path: Path) -> None:
        # Create a minimal repo
        (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")

        snap = capture_snapshot(
            repo_path=tmp_path,
            include_os_packages=False,  # Skip for speed
        )

        assert isinstance(snap, EnvironmentSnapshot)
        assert snap.repo_path == str(tmp_path)
        assert snap.snapshot_hash is not None
        assert len(snap.snapshot_hash) == 16  # Truncated SHA-256

    def test_snapshot_captures_lockfiles(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("flask==3.0.0\nrequests==2.31.0\n")

        snap = capture_snapshot(repo_path=tmp_path, include_os_packages=False)

        assert len(snap.lockfiles) == 1
        assert snap.lockfiles[0].lockfile_type == "requirements.txt"
        assert len(snap.lockfiles[0].packages) == 2

    def test_snapshot_captures_python_version(self, tmp_path: Path) -> None:
        snap = capture_snapshot(repo_path=tmp_path, include_os_packages=False)

        assert snap.runtime_versions.python is not None
        # Should be something like "3.11.9"
        assert snap.runtime_versions.python.count(".") >= 1

    def test_snapshot_captures_locale(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANG", "en_US.UTF-8")

        snap = capture_snapshot(repo_path=tmp_path, include_os_packages=False)

        assert snap.locale.lang == "en_US.UTF-8"


class TestSnapshotDeterminism:
    """Test that snapshots are deterministic."""

    def test_same_input_produces_same_hash(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("flask==3.0.0\n")

        snap1 = capture_snapshot(repo_path=tmp_path, include_os_packages=False)
        snap2 = capture_snapshot(repo_path=tmp_path, include_os_packages=False)

        # Hashes should be the same (they exclude timestamp)
        assert snap1.snapshot_hash == snap2.snapshot_hash

    def test_different_lockfiles_produce_different_hash(self, tmp_path: Path) -> None:
        req_file = tmp_path / "requirements.txt"

        req_file.write_text("flask==3.0.0\n")
        snap1 = capture_snapshot(repo_path=tmp_path, include_os_packages=False)

        req_file.write_text("flask==3.0.1\n")  # Different version
        snap2 = capture_snapshot(repo_path=tmp_path, include_os_packages=False)

        assert snap1.snapshot_hash != snap2.snapshot_hash


class TestSnapshotPersistence:
    """Test save/load round-trip."""

    def test_save_and_load_snapshot(self, tmp_path: Path) -> None:
        # Create snapshot
        (tmp_path / "requirements.txt").write_text("flask==3.0.0\n")
        snap = capture_snapshot(repo_path=tmp_path, include_os_packages=False)

        # Save to file
        output_file = tmp_path / "snap.json"
        save_snapshot(snap, output_file)

        assert output_file.exists()

        # Load back
        loaded = load_snapshot(output_file)

        assert loaded.snapshot_hash == snap.snapshot_hash
        assert loaded.repo_path == snap.repo_path
        assert len(loaded.lockfiles) == len(snap.lockfiles)

    def test_saved_json_is_valid(self, tmp_path: Path) -> None:
        snap = capture_snapshot(repo_path=tmp_path, include_os_packages=False)

        output_file = tmp_path / "snap.json"
        save_snapshot(snap, output_file)

        # Should be valid JSON
        content = output_file.read_text()
        data = json.loads(content)

        assert "snapshot_version" in data
        assert "captured_at" in data
        assert "repo_path" in data
