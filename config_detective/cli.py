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
    skip_os_packages: bool = typer.Option(
        False, "--skip-os-packages", help="Skip OS package detection (faster)."
    ),
    skip_env_vars: bool = typer.Option(
        False, "--skip-env-vars", help="Skip environment variable capture."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show detailed progress."
    ),
) -> None:
    """Capture a deterministic snapshot of the current environment.

    This captures:
    - Lockfiles (requirements.txt, uv.lock, package-lock.json, etc.)
    - Dockerfiles
    - Environment variables (with secrets redacted)
    - OS packages (dpkg, apt, brew, etc.)
    - Runtime versions (Python, Node, Ruby, etc.)
    - Locale and timezone settings
    - System info (OS, kernel, glibc/musl, architecture)
    """
    import logging

    from config_detective.snapshot import capture_snapshot, save_snapshot

    # Configure logging based on verbosity
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    repo_path = repo.resolve()

    console.print(f"[blue]Capturing snapshot from:[/blue] {repo_path}")

    with console.status("[bold green]Capturing environment snapshot..."):
        snap = capture_snapshot(
            repo_path=repo_path,
            include_os_packages=not skip_os_packages,
            include_env_vars=not skip_env_vars,
        )

    # Print summary
    console.print()
    console.print("[bold green]Snapshot captured![/bold green]")
    console.print(f"  Hash: [cyan]{snap.snapshot_hash}[/cyan]")
    console.print(f"  Lockfiles: {len(snap.lockfiles)}")
    console.print(f"  Dockerfiles: {len(snap.dockerfiles)}")
    console.print(f"  Env vars: {len(snap.env_vars)} ({sum(1 for e in snap.env_vars if e.redacted)} redacted)")
    console.print(f"  OS packages: {len(snap.os_packages)}")
    console.print(f"  Python: {snap.runtime_versions.python or 'not detected'}")
    console.print(f"  System: {snap.system.os_release or snap.system.os_type.value}")

    if snap.capture_errors:
        console.print()
        console.print(f"[yellow]Warnings ({len(snap.capture_errors)}):[/yellow]")
        for error in snap.capture_errors:
            console.print(f"  - {error}")

    # Save to file
    save_snapshot(snap, output)
    console.print()
    console.print(f"[green]Saved to:[/green] {output}")


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
    from config_detective.snapshot import load_snapshot
    from config_detective.agents.state import create_initial_state
    from config_detective.agents.orchestrator import run_investigation_sync

    snap_a_obj = load_snapshot(snap_a)
    snap_b_obj = load_snapshot(snap_b)
    failure_trace = trace.read_text(encoding="utf-8")

    state = create_initial_state(
        snapshot_a_dict=snap_a_obj.to_json(),
        snapshot_b_dict=snap_b_obj.to_json(),
        failure_trace=failure_trace,
    )

    console.print("[blue]Running investigation...[/blue]")
    final = run_investigation_sync(state)

    report = final.get("report", {})
    hypothesis = final.get("selected_hypothesis")

    console.print()
    console.print(f"[bold]Status:[/bold] {report.get('status', 'unknown')}")
    console.print(f"[bold]Confidence:[/bold] {report.get('confidence', 0):.0%}")

    if hypothesis:
        console.print(f"[bold]Root cause:[/bold] {hypothesis.get('delta_id', 'N/A')}")
        console.print(f"[bold]Explanation:[/bold] {hypothesis.get('explanation', 'N/A')}")
        fix_code = hypothesis.get("fix_code", "")
        if fix_code:
            console.print(f"[bold]Fix:[/bold] {fix_code}")

            if mode == "apply":
                from config_detective.patcher import propose_fix, apply_fix
                from config_detective.patcher.unified_diff import infer_target_file
                from config_detective.patcher.confirm import confirm_apply

                target = infer_target_file(fix_code, repo_path=".")
                if target:
                    target_path = Path(target)
                    content = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
                    patch = propose_fix(
                        fix_code=fix_code,
                        target_file=target,
                        content=content,
                        trace_id=final.get("trace_id", ""),
                        confidence=hypothesis.get("confidence", 0.0),
                        description=hypothesis.get("explanation", ""),
                    )
                    if confirm_apply(patch, auto_yes=yes):
                        result = apply_fix(patch, repo_path=".", dry_run=False)
                        if result.success:
                            console.print(f"[green]Patch applied:[/green] {result.message}")
                        else:
                            console.print(f"[red]Patch failed:[/red] {result.message}")
                    else:
                        console.print("[yellow]Patch application cancelled.[/yellow]")
    else:
        console.print("[yellow]No fix identified. See full report for details.[/yellow]")


@app.command(name="apply")
def apply_cmd(
    case_id: str = typer.Argument(..., help="Case id from a previous investigation."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt."),
) -> None:
    """Apply a previously-proposed fix to the repo."""
    from config_detective.patcher.rollback import get_backup_store
    from config_detective.patcher.models import PatchRecord
    from config_detective.patcher import apply_fix
    from config_detective.patcher.confirm import confirm_apply

    store = get_backup_store(".")
    patch_data = store.get_patch_by_case_id(case_id)
    if not patch_data:
        console.print(f"[red]No patch found for case {case_id}[/red]")
        raise typer.Exit(code=1)

    patch = PatchRecord.from_dict(patch_data)
    if confirm_apply(patch, auto_yes=yes):
        result = apply_fix(patch, repo_path=".", dry_run=False)
        if result.success:
            console.print(f"[green]{result.message}[/green]")
        else:
            console.print(f"[red]{result.message}[/red]")
            raise typer.Exit(code=1)
    else:
        console.print("[yellow]Cancelled.[/yellow]")


@app.command()
def undo(
    case_id: Optional[str] = typer.Argument(
        None,
        help="Case id to undo. Defaults to the most recently applied patch.",
    ),
) -> None:
    """Undo a previously-applied patch."""
    from config_detective.patcher import undo_fix

    result = undo_fix(case_id=case_id, repo_path=".")
    if result.success:
        console.print(f"[green]{result.message}[/green]")
    else:
        console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(code=1)


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
    from config_detective.mcp_server import run_server

    console.print(f"[blue]Starting MCP server (transport={transport})...[/blue]")
    run_server(transport=transport)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
