"""Tests for the patcher module (Phase 8a).

Covers:
- Data models (PatchRecord, PatchResult, serialization)
- Unified diff generation (env vars, packages, Dockerfile)
- Target file type detection and inference
- Patch application (dry-run and real)
- Rollback / undo (apply -> undo -> file identical)
- Interactive confirmation (yes/no prompt)
- Full round-trip: apply -> verify -> undo -> verify original
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from config_detective.patcher.models import (
    PatchHunk,
    PatchRecord,
    PatchResult,
    PatchStatus,
    TargetFileType,
)
from config_detective.patcher.unified_diff import (
    apply_fix_to_content,
    create_patch_record,
    detect_target_type,
    generate_diff,
    infer_target_file,
)
from config_detective.patcher.applier import apply_patch, preview_patch
from config_detective.patcher.rollback import (
    BackupStore,
    reset_store_cache,
    undo_patch,
)
from config_detective.patcher.confirm import confirm_apply, confirm_undo


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def clean_store_cache():
    """Reset the backup store cache between tests."""
    reset_store_cache()
    yield
    reset_store_cache()


@pytest.fixture
def env_file_content() -> str:
    return "DATABASE_URL=postgres://localhost/mydb\nLANG=C\nDEBUG=true\n"


@pytest.fixture
def requirements_content() -> str:
    return "flask==2.3.0\nrequests==2.31.0\ncryptography==38.0.0\nnumpy==1.24.0\n"


@pytest.fixture
def dockerfile_content() -> str:
    return (
        "FROM python:3.11-slim\n"
        "WORKDIR /app\n"
        "COPY requirements.txt .\n"
        "RUN pip install -r requirements.txt\n"
        "COPY . .\n"
        "CMD [\"python\", \"app.py\"]\n"
    )


# =============================================================================
# Data Model Tests
# =============================================================================


class TestModels:
    """Tests for patcher data models."""

    def test_patch_record_creation(self):
        record = PatchRecord(target_file="Dockerfile", fix_code="ENV LANG=C.UTF-8")
        assert record.target_file == "Dockerfile"
        assert record.status == PatchStatus.PROPOSED
        assert len(record.patch_id) == 12

    def test_patch_record_serialization(self):
        record = PatchRecord(
            target_file=".env",
            fix_code="LANG=en_US.UTF-8",
            confidence=0.85,
        )
        d = record.to_dict()
        restored = PatchRecord.from_dict(d)
        assert restored.target_file == ".env"
        assert restored.fix_code == "LANG=en_US.UTF-8"
        assert restored.confidence == 0.85

    def test_patch_result_to_dict(self):
        result = PatchResult(
            success=True,
            patch_id="abc123",
            target_file=".env",
            action="applied",
            message="OK",
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["action"] == "applied"

    def test_patch_hunk_to_text(self):
        hunk = PatchHunk(
            old_start=1, old_count=3, new_start=1, new_count=3,
            lines=[" line1", "-old", "+new", " line3"],
        )
        text = hunk.to_text()
        assert "@@ -1,3 +1,3 @@" in text
        assert "-old" in text
        assert "+new" in text

    def test_target_file_type_enum(self):
        assert TargetFileType.DOCKERFILE.value == "Dockerfile"
        assert TargetFileType.ENV_FILE.value == ".env"


# =============================================================================
# Target Type Detection Tests
# =============================================================================


class TestDetection:
    """Tests for target file type detection."""

    def test_detect_dockerfile(self):
        assert detect_target_type("Dockerfile") == TargetFileType.DOCKERFILE
        assert detect_target_type("Dockerfile.dev") == TargetFileType.DOCKERFILE

    def test_detect_requirements(self):
        assert detect_target_type("requirements.txt") == TargetFileType.REQUIREMENTS
        assert detect_target_type("requirements-dev.txt") == TargetFileType.REQUIREMENTS

    def test_detect_env(self):
        assert detect_target_type(".env") == TargetFileType.ENV_FILE
        assert detect_target_type(".env.local") == TargetFileType.ENV_FILE

    def test_detect_compose(self):
        assert detect_target_type("docker-compose.yml") == TargetFileType.DOCKER_COMPOSE

    def test_detect_pyproject(self):
        assert detect_target_type("pyproject.toml") == TargetFileType.PYPROJECT

    def test_detect_generic(self):
        assert detect_target_type("random.txt") == TargetFileType.GENERIC


# =============================================================================
# Diff Generation Tests
# =============================================================================


class TestDiffGeneration:
    """Tests for unified diff generation."""

    def test_env_var_fix_updates_existing(self, env_file_content):
        result = apply_fix_to_content(
            env_file_content, "LANG=en_US.UTF-8", TargetFileType.ENV_FILE
        )
        assert "LANG=en_US.UTF-8" in result
        assert "LANG=C" not in result
        assert "DATABASE_URL=postgres://localhost/mydb" in result

    def test_env_var_fix_adds_new(self, env_file_content):
        result = apply_fix_to_content(
            env_file_content, "NEW_VAR=hello", TargetFileType.ENV_FILE
        )
        assert "NEW_VAR=hello" in result
        assert "LANG=C" in result

    def test_package_fix_updates_version(self, requirements_content):
        result = apply_fix_to_content(
            requirements_content, "pip install cryptography==41.0.0",
            TargetFileType.REQUIREMENTS,
        )
        assert "cryptography==41.0.0" in result
        assert "cryptography==38.0.0" not in result
        assert "flask==2.3.0" in result

    def test_package_fix_adds_new(self, requirements_content):
        result = apply_fix_to_content(
            requirements_content, "pip install newpkg==1.0.0",
            TargetFileType.REQUIREMENTS,
        )
        assert "newpkg==1.0.0" in result
        assert "flask==2.3.0" in result

    def test_dockerfile_env_fix(self, dockerfile_content):
        result = apply_fix_to_content(
            dockerfile_content, "ENV LANG=C.UTF-8", TargetFileType.DOCKERFILE
        )
        assert "ENV LANG=C.UTF-8" in result

    def test_dockerfile_run_fix(self, dockerfile_content):
        result = apply_fix_to_content(
            dockerfile_content, "apt-get install -y locales",
            TargetFileType.DOCKERFILE,
        )
        assert "RUN apt-get install -y locales" in result
        lines = result.splitlines()
        cmd_idx = next(i for i, l in enumerate(lines) if l.startswith("CMD"))
        run_idx = next(i for i, l in enumerate(lines) if "locales" in l)
        assert run_idx < cmd_idx

    def test_generate_diff_produces_unified_format(self):
        original = "line1\nline2\nline3\n"
        modified = "line1\nline2_changed\nline3\n"
        diff = generate_diff(original, modified, "test.txt")
        assert "--- a/test.txt" in diff
        assert "+++ b/test.txt" in diff
        assert "-line2" in diff
        assert "+line2_changed" in diff

    def test_generate_diff_no_changes(self):
        content = "same\n"
        diff = generate_diff(content, content, "test.txt")
        assert diff == ""

    def test_create_patch_record(self, env_file_content):
        patch = create_patch_record(
            fix_code="LANG=en_US.UTF-8",
            target_file=".env",
            content=env_file_content,
            trace_id="test-trace",
            confidence=0.9,
        )
        assert patch.target_file == ".env"
        assert patch.target_type == TargetFileType.ENV_FILE
        assert "LANG" in patch.diff_text
        assert patch.confidence == 0.9
        assert patch.status == PatchStatus.PROPOSED

    def test_export_syntax_in_env_var_fix(self, env_file_content):
        result = apply_fix_to_content(
            env_file_content, "export LANG='en_US.UTF-8'",
            TargetFileType.ENV_FILE,
        )
        assert "en_US.UTF-8" in result
        assert "LANG=C" not in result


# =============================================================================
# Target File Inference Tests
# =============================================================================


class TestInferTarget:
    """Tests for target file inference."""

    def test_infer_pip_install(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask==2.0\n")
        target = infer_target_file("pip install cryptography==41.0.0", repo_path=tmp_path)
        assert target == "requirements.txt"

    def test_infer_apt_install(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM python:3.11\n")
        target = infer_target_file("apt-get install -y locales", repo_path=tmp_path)
        assert target == "Dockerfile"

    def test_infer_env_command(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM python:3.11\n")
        target = infer_target_file("ENV LANG=C.UTF-8", repo_path=tmp_path)
        assert target == "Dockerfile"

    def test_infer_export(self, tmp_path):
        (tmp_path / ".env").write_text("DEBUG=1\n")
        target = infer_target_file("export LANG='en_US.UTF-8'", repo_path=tmp_path)
        assert target == ".env"

    def test_infer_fallback_requirements(self):
        target = infer_target_file("pip install foo==1.0")
        assert target == "requirements.txt"


# =============================================================================
# Applier Tests
# =============================================================================


class TestApplier:
    """Tests for patch application."""

    def test_dry_run_does_not_write(self, tmp_path, env_file_content):
        target = tmp_path / ".env"
        target.write_text(env_file_content)

        patch = create_patch_record(
            fix_code="LANG=en_US.UTF-8",
            target_file=".env",
            content=env_file_content,
        )
        result = apply_patch(patch, repo_path=tmp_path, dry_run=True)
        assert result.success is True
        assert result.dry_run is True
        assert target.read_text() == env_file_content

    def test_apply_writes_file(self, tmp_path, env_file_content):
        target = tmp_path / ".env"
        target.write_text(env_file_content)

        patch = create_patch_record(
            fix_code="LANG=en_US.UTF-8",
            target_file=".env",
            content=env_file_content,
        )
        result = apply_patch(patch, repo_path=tmp_path, dry_run=False)
        assert result.success is True
        assert result.dry_run is False
        new_content = target.read_text()
        assert "LANG=en_US.UTF-8" in new_content
        assert "LANG=C" not in new_content

    def test_apply_creates_backup(self, tmp_path, env_file_content):
        target = tmp_path / ".env"
        target.write_text(env_file_content)

        patch = create_patch_record(
            fix_code="LANG=en_US.UTF-8",
            target_file=".env",
            content=env_file_content,
        )
        apply_patch(patch, repo_path=tmp_path, dry_run=False)

        store_file = tmp_path / ".config-detective" / "patches.json"
        assert store_file.exists()
        data = json.loads(store_file.read_text())
        assert patch.patch_id in data["backups"]

    def test_preview_patch(self, env_file_content):
        patch = create_patch_record(
            fix_code="LANG=en_US.UTF-8",
            target_file=".env",
            content=env_file_content,
            confidence=0.85,
        )
        preview = preview_patch(patch)
        assert "Patch Preview" in preview
        assert ".env" in preview
        assert "85%" in preview


# =============================================================================
# Rollback Tests
# =============================================================================


class TestRollback:
    """Tests for patch rollback / undo."""

    def test_apply_undo_roundtrip(self, tmp_path, env_file_content):
        """Apply a patch, then undo it — the file should be identical."""
        target = tmp_path / ".env"
        target.write_text(env_file_content)

        patch = create_patch_record(
            fix_code="LANG=en_US.UTF-8",
            target_file=".env",
            content=env_file_content,
            case_id="test-case-1",
        )
        apply_patch(patch, repo_path=tmp_path, dry_run=False)
        assert "LANG=en_US.UTF-8" in target.read_text()

        result = undo_patch(patch_id=patch.patch_id, repo_path=tmp_path)
        assert result.success is True
        assert target.read_text() == env_file_content

    def test_undo_by_case_id(self, tmp_path, env_file_content):
        target = tmp_path / ".env"
        target.write_text(env_file_content)

        patch = create_patch_record(
            fix_code="LANG=en_US.UTF-8",
            target_file=".env",
            content=env_file_content,
            case_id="my-case",
        )
        apply_patch(patch, repo_path=tmp_path, dry_run=False)
        result = undo_patch(case_id="my-case", repo_path=tmp_path)
        assert result.success is True
        assert target.read_text() == env_file_content

    def test_undo_latest(self, tmp_path, env_file_content):
        target = tmp_path / ".env"
        target.write_text(env_file_content)

        patch = create_patch_record(
            fix_code="LANG=en_US.UTF-8",
            target_file=".env",
            content=env_file_content,
        )
        apply_patch(patch, repo_path=tmp_path, dry_run=False)
        result = undo_patch(repo_path=tmp_path)
        assert result.success is True
        assert target.read_text() == env_file_content

    def test_undo_no_patches(self, tmp_path):
        result = undo_patch(repo_path=tmp_path)
        assert result.success is False
        assert "No patch found" in result.message

    def test_undo_already_rolled_back(self, tmp_path, env_file_content):
        target = tmp_path / ".env"
        target.write_text(env_file_content)

        patch = create_patch_record(
            fix_code="LANG=en_US.UTF-8",
            target_file=".env",
            content=env_file_content,
        )
        apply_patch(patch, repo_path=tmp_path, dry_run=False)
        undo_patch(patch_id=patch.patch_id, repo_path=tmp_path)
        result = undo_patch(patch_id=patch.patch_id, repo_path=tmp_path)
        assert result.success is False
        assert "already rolled back" in result.message

    def test_backup_store_persistence(self, tmp_path):
        store = BackupStore(tmp_path)
        store.save_backup("p1", "test.txt", "original content")
        store2 = BackupStore(tmp_path)
        backup = store2.get_backup("p1")
        assert backup is not None
        assert backup["content"] == "original content"


# =============================================================================
# Confirm Tests
# =============================================================================


class TestConfirm:
    """Tests for the interactive confirmation prompt."""

    def test_confirm_auto_yes(self, env_file_content):
        patch = create_patch_record(
            fix_code="LANG=en_US.UTF-8",
            target_file=".env",
            content=env_file_content,
        )
        output = io.StringIO()
        result = confirm_apply(patch, auto_yes=True, output=output)
        assert result is True
        assert "Auto-applying" in output.getvalue()

    def test_confirm_user_says_yes(self, env_file_content):
        patch = create_patch_record(
            fix_code="LANG=en_US.UTF-8",
            target_file=".env",
            content=env_file_content,
        )
        output = io.StringIO()
        input_stream = io.StringIO("y\n")
        result = confirm_apply(patch, output=output, input_stream=input_stream)
        assert result is True

    def test_confirm_user_says_no(self, env_file_content):
        patch = create_patch_record(
            fix_code="LANG=en_US.UTF-8",
            target_file=".env",
            content=env_file_content,
        )
        output = io.StringIO()
        input_stream = io.StringIO("n\n")
        result = confirm_apply(patch, output=output, input_stream=input_stream)
        assert result is False

    def test_confirm_undo_auto_yes(self):
        output = io.StringIO()
        result = confirm_undo("p1", "test.txt", auto_yes=True, output=output)
        assert result is True

    def test_confirm_undo_rejected(self):
        output = io.StringIO()
        input_stream = io.StringIO("n\n")
        result = confirm_undo("p1", "test.txt", output=output, input_stream=input_stream)
        assert result is False


# =============================================================================
# Full Round-Trip Test
# =============================================================================


class TestRoundTrip:
    """Full round-trip: apply -> verify changed -> undo -> verify original."""

    def test_requirements_roundtrip(self, tmp_path, requirements_content):
        target = tmp_path / "requirements.txt"
        target.write_text(requirements_content)

        patch = create_patch_record(
            fix_code="pip install cryptography==41.0.0",
            target_file="requirements.txt",
            content=requirements_content,
        )
        assert "cryptography==41.0.0" in patch.patched_content
        assert patch.diff_text != ""

        apply_patch(patch, repo_path=tmp_path, dry_run=False)
        assert "cryptography==41.0.0" in target.read_text()
        assert "cryptography==38.0.0" not in target.read_text()

        result = undo_patch(patch_id=patch.patch_id, repo_path=tmp_path)
        assert result.success is True
        assert target.read_text() == requirements_content

    def test_dockerfile_roundtrip(self, tmp_path, dockerfile_content):
        target = tmp_path / "Dockerfile"
        target.write_text(dockerfile_content)

        patch = create_patch_record(
            fix_code="ENV LANG=C.UTF-8",
            target_file="Dockerfile",
            content=dockerfile_content,
        )
        apply_patch(patch, repo_path=tmp_path, dry_run=False)
        assert "ENV LANG=C.UTF-8" in target.read_text()

        result = undo_patch(patch_id=patch.patch_id, repo_path=tmp_path)
        assert result.success is True
        assert target.read_text() == dockerfile_content
