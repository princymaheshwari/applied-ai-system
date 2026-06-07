"""Tests for the MCP server (Phase 8b).

Covers:
- Server instantiation and tool registration
- Each tool function directly (unit-testable without MCP transport)
- explain_config_delta with various delta types
- find_similar_past_case (mocked memory)
- undo_fix integration with patcher
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from config_detective.mcp_server.server import (
    mcp,
    compare_envs,
    bisect_dockerfile_layer,
    explain_config_delta,
    find_similar_past_case,
    apply_fix,
    undo_fix,
)


# =============================================================================
# Server Registration Tests
# =============================================================================


class TestServerRegistration:
    """Tests for MCP server tool registration."""

    def test_server_has_name(self):
        assert mcp.name == "config-detective"

    def test_server_has_instructions(self):
        assert "CONFIG DETECTIVE" in (mcp.instructions or "")


# =============================================================================
# explain_config_delta Tests
# =============================================================================


class TestExplainDelta:
    """Tests for the explain_config_delta tool."""

    def test_env_var_locale_change(self):
        result = json.loads(explain_config_delta(
            node_id="env:LANG",
            delta_type="value_changed",
            value_a="en_US.UTF-8",
            value_b="C",
        ))
        assert "LANG" in result["what"]
        assert "Locale" in result["impact"] or "encoding" in result["impact"].lower()

    def test_env_var_only_in_a(self):
        result = json.loads(explain_config_delta(
            node_id="env:TZ",
            delta_type="only_in_a",
            value_a="UTC",
        ))
        assert "missing" in result["what"]

    def test_python_package_version(self):
        result = json.loads(explain_config_delta(
            node_id="python_package:cryptography",
            delta_type="value_changed",
            value_a="41.0.0",
            value_b="38.0.0",
        ))
        assert "Python package" in result["layer"]
        assert "cryptography" in result["what"]

    def test_os_package(self):
        result = json.loads(explain_config_delta(
            node_id="os_package:libssl3",
            delta_type="only_in_a",
            value_a="3.0.2",
        ))
        assert "OS" in result["layer"]

    def test_ssl_env_var(self):
        result = json.loads(explain_config_delta(
            node_id="env:SSL_CERT_FILE",
            delta_type="value_changed",
            value_a="/etc/ssl/certs/ca-certificates.crt",
            value_b="",
        ))
        assert "SSL" in result["impact"] or "certificate" in result["impact"].lower()


# =============================================================================
# find_similar_past_case Tests
# =============================================================================


class TestFindSimilarCase:
    """Tests for the find_similar_past_case tool."""

    def test_memory_unavailable_returns_empty(self):
        result = json.loads(find_similar_past_case(
            failure_signature="UnicodeDecodeError: 'ascii' codec",
        ))
        assert result["found"] == 0
        assert "cases" in result

    def test_returns_json_structure(self):
        result = json.loads(find_similar_past_case(
            failure_signature="ImportError: No module named ssl",
            top_k=2,
        ))
        assert "found" in result
        assert "cases" in result


# =============================================================================
# undo_fix Tests
# =============================================================================


class TestUndoFix:
    """Tests for the undo_fix MCP tool."""

    def test_undo_no_patches(self, tmp_path):
        result = json.loads(undo_fix(repo_path=str(tmp_path)))
        assert result["success"] is False

    def test_undo_after_apply(self, tmp_path):
        from config_detective.patcher import propose_fix, apply_fix as _apply
        from config_detective.patcher.rollback import reset_store_cache

        reset_store_cache()

        target = tmp_path / ".env"
        target.write_text("LANG=C\n")

        p = propose_fix(
            fix_code="LANG=en_US.UTF-8",
            target_file=".env",
            content="LANG=C\n",
            case_id="mcp-test-1",
        )
        _apply(p, repo_path=str(tmp_path), dry_run=False)
        assert "en_US.UTF-8" in target.read_text()

        result = json.loads(undo_fix(
            patch_id=p.patch_id,
            repo_path=str(tmp_path),
        ))
        assert result["success"] is True
        assert target.read_text() == "LANG=C\n"


# =============================================================================
# apply_fix Tests
# =============================================================================


class TestApplyFix:
    """Tests for the apply_fix MCP tool."""

    def test_apply_without_confirm_rejected(self):
        result = json.loads(apply_fix(confirm=False))
        assert result["success"] is False
        assert "not confirmed" in result["message"].lower()

    def test_apply_with_target_and_fix(self, tmp_path):
        from config_detective.patcher.rollback import reset_store_cache
        reset_store_cache()

        target = tmp_path / ".env"
        target.write_text("LANG=C\n")

        result = json.loads(apply_fix(
            target_file=".env",
            fix_code="LANG=en_US.UTF-8",
            repo_path=str(tmp_path),
            confirm=True,
        ))
        assert result["success"] is True
        assert "en_US.UTF-8" in target.read_text()

    def test_apply_missing_args(self):
        result = json.loads(apply_fix(confirm=True))
        assert result["success"] is False


# =============================================================================
# bisect_dockerfile_layer Tests
# =============================================================================


class TestBisectLayer:
    """Tests for the bisect_dockerfile_layer tool."""

    def test_layer_out_of_range(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python:3.11\nRUN echo hello\n")
        result = json.loads(bisect_dockerfile_layer(
            dockerfile_path=str(df),
            layer_idx=99,
        ))
        assert "error" in result

    def test_valid_layer(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python:3.11\nRUN echo hello\nCMD python\n")
        result = json.loads(bisect_dockerfile_layer(
            dockerfile_path=str(df),
            layer_idx=0,
        ))
        assert result["layer_idx"] == 0
        assert result["total_layers"] == 3
