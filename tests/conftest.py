"""Shared pytest fixtures for the CONFIG DETECTIVE test suite.

This file is loaded automatically by pytest. Fixtures defined here are
available to every test module without explicit import.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def project_root() -> Path:
    """Absolute path to the repository root."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block real network credentials from leaking into tests.

    Tests that need credentials must set them explicitly via monkeypatch.
    """
    for key in (
        "GROQ_API_KEY",
        "GOOGLE_API_KEY",
        "HF_TOKEN",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
