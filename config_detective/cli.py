"""Typer-based command line interface for CONFIG DETECTIVE.

Subcommands are stubbed during scaffolding (Phase 0) and filled in across
later phases. Each stub prints which phase will land its implementation so
running ``config-detective --help`` is immediately useful.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from config_detective import __version__

app = typer.Typer(
    name="config-detective",
    help=(
        "Works-on-my-machine forensics agent. Diagnoses config-divergence "
        "bugs by differential bisection over a multi-layer environment "
        "graph with empirical sandbox verification."
    ),
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _stub(phase: str, message: str) -> None:
    console.print(f"[yellow][stub][/yellow] [bold]Phase {phase}[/bold] - {message}")
    raise typer.Exit(code=2)


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", "-V", help="Show the version and exit."
    ),
) -> None:
    if version:
        console.print(f"config-detective {__version__}")
        raise typer.Exit()


@app.command()
def snapshot(
    output: Path = typer.Option(
        Path("snap.json"), "--output", "-o", help="Path to write snapshot JSON."
    ),
    repo: Path = typer.Option(
        Path("."), "--repo", "-r", help="Path to the repo to snapshot."
    ),
) -> None:
    """Capture a deterministic snapshot of the current environment."""
    _stub("1", f"snapshot module lands in Phase 1. Would write to {output} from {repo}.")


@app.command()
def investigate(
    snap_a: Path = typer.Option(..., "--snap-a", help="Snapshot A (works)."),
    snap_b: Path = typer.Option(..., "--snap-b", help="Snapshot B (fails)."),
    trace: Path = typer.Option(..., "--trace", help="Path to failure trace."),
    mode: str = typer.Option(
        "propose",
        "--mode",
        help="Operating mode: 'propose' (default, human-in-the-loop) or 'apply'.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Skip the apply confirmation prompt (only meaningful with --mode apply).",
    ),
) -> None:
    """Run a full investigation: diff, prioritize, hypothesize, sandbox-verify, report."""
    _stub("5", f"orchestrator lands in Phase 5. mode={mode!r} yes={yes}")


@app.command(name="apply")
def apply_cmd(
    case_id: str = typer.Argument(..., help="Case id from a previous investigation."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt."),
) -> None:
    """Apply a previously-proposed fix to the repo."""
    _stub("8a", f"patcher applier lands in Phase 8a. case_id={case_id} yes={yes}")


@app.command()
def undo(
    case_id: Optional[str] = typer.Argument(
        None,
        help="Case id to undo. Defaults to the most recently applied patch.",
    ),
) -> None:
    """Undo a previously-applied patch."""
    _stub("8a", f"patcher rollback lands in Phase 8a. case_id={case_id}")


@app.command(name="eval")
def eval_cmd(
    seeds_dir: Path = typer.Option(
        Path("config_detective/eval/seeds"),
        "--seeds-dir",
        help="Directory of seeded benchmark cases.",
    ),
    output: Path = typer.Option(
        Path("eval_reports/latest.md"),
        "--output",
        help="Where to write the eval report.",
    ),
) -> None:
    """Run the 15-case benchmark and print a metrics summary."""
    _stub("10", f"benchmark runner lands in Phase 10. seeds_dir={seeds_dir}")


@app.command(name="mcp-serve")
def mcp_serve(
    transport: str = typer.Option(
        "stdio",
        "--transport",
        help="MCP transport: stdio (for Cursor) or sse.",
    ),
) -> None:
    """Start the MCP server so Cursor / Claude Desktop can drive investigations."""
    _stub("8b", f"MCP server lands in Phase 8b. transport={transport!r}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
