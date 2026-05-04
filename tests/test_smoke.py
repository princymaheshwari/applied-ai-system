"""Sanity tests that verify the project scaffolding is intact.

Real test modules (test_differ, test_graph, test_memory, test_guardrails,
test_orchestrator_smoke) land in their respective phases.
"""

from __future__ import annotations

import importlib

import pytest


SUBPACKAGES = [
    "config_detective",
    "config_detective.agents",
    "config_detective.eval",
    "config_detective.graph",
    "config_detective.guardrails",
    "config_detective.llm",
    "config_detective.mcp_server",
    "config_detective.memory",
    "config_detective.patcher",
    "config_detective.retrieval",
    "config_detective.sandbox",
    "config_detective.snapshot",
]


@pytest.mark.parametrize("module_name", SUBPACKAGES)
def test_subpackage_importable(module_name: str) -> None:
    """Every declared subpackage should import cleanly."""
    module = importlib.import_module(module_name)
    assert module is not None


def test_version_exposed() -> None:
    from config_detective import __version__

    assert isinstance(__version__, str)
    assert __version__.count(".") >= 2


def test_cli_app_constructible() -> None:
    """Importing the CLI module should not error during scaffolding phase."""
    from config_detective.cli import app

    assert app is not None
    assert app.info.name == "config-detective"
