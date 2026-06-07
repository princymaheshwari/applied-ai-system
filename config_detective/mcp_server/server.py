"""MCP server — exposes CONFIG DETECTIVE tools for Cursor / Claude Desktop.

Provides 7 tools over stdio transport:
1. compare_envs — diff two environment snapshots
2. bisect_dockerfile_layer — verify a specific Dockerfile layer
3. explain_config_delta — get a natural-language explanation of a delta
4. find_similar_past_case — look up similar cases from memory
5. propose_fix — generate a diff + confidence + evidence for a fix
6. apply_fix — apply a previously proposed fix (with confirmation)
7. undo_fix — roll back the last applied fix

Start with: config-detective mcp-serve
Or directly: python -m config_detective.mcp_server.server
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="config-detective",
    instructions=(
        "CONFIG DETECTIVE - Works-on-my-machine forensics agent. "
        "Diagnoses config-divergence bugs by differential bisection over "
        "a multi-layer environment graph with empirical sandbox verification."
    ),
)


# ---------------------------------------------------------------------------
# Tool 1: compare_envs
# ---------------------------------------------------------------------------

@mcp.tool()
def compare_envs(
    snap_a_path: str,
    snap_b_path: str,
) -> str:
    """Compare two environment snapshots and return a delta summary.

    Args:
        snap_a_path: Path to snapshot A JSON (working environment)
        snap_b_path: Path to snapshot B JSON (failing environment)

    Returns:
        JSON delta summary with ranked suspicious differences
    """
    try:
        from config_detective.snapshot import load_snapshot
        from config_detective.graph.builder import EnvironmentGraphBuilder
        from config_detective.graph.differ import EnvironmentDiffer

        snap_a = load_snapshot(Path(snap_a_path))
        snap_b = load_snapshot(Path(snap_b_path))

        builder_a = EnvironmentGraphBuilder(snap_a)
        builder_b = EnvironmentGraphBuilder(snap_b)
        graph_a = builder_a.build()
        graph_b = builder_b.build()

        differ = EnvironmentDiffer(graph_a, graph_b)
        deltas = differ.compute_delta()
        top = differ.get_top_suspects(n=10)
        summary = differ.summarize_deltas(deltas)

        return json.dumps({
            "total_deltas": len(deltas),
            "top_suspects": [
                {
                    "node_id": d.node_id,
                    "delta_type": d.delta_type,
                    "value_a": d.value_a,
                    "value_b": d.value_b,
                    "suspect_score": d.suspect_score,
                }
                for d in top
            ],
            "summary": summary,
        }, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Tool 2: bisect_dockerfile_layer
# ---------------------------------------------------------------------------

@mcp.tool()
def bisect_dockerfile_layer(
    dockerfile_path: str,
    layer_idx: int,
    failing_command: str = "python -c 'import sys; sys.exit(0)'",
) -> str:
    """Test a specific Dockerfile layer to isolate which layer introduces the failure.

    Args:
        dockerfile_path: Path to the Dockerfile
        layer_idx: Index of the layer to test (0-based)
        failing_command: Command that should fail in the broken environment

    Returns:
        JSON with verification result for the layer
    """
    try:
        from config_detective.snapshot.dockerfile import parse_dockerfile

        dockerfile_data = parse_dockerfile(Path(dockerfile_path))
        layers = dockerfile_data.instructions

        if layer_idx < 0 or layer_idx >= len(layers):
            return json.dumps({
                "error": f"Layer index {layer_idx} out of range (0-{len(layers)-1})"
            })

        layer = layers[layer_idx]
        return json.dumps({
            "layer_idx": layer_idx,
            "instruction": layer.instruction,
            "value": layer.value,
            "total_layers": len(layers),
            "note": "Full sandbox bisection requires Docker. Layer info returned.",
        }, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Tool 3: explain_config_delta
# ---------------------------------------------------------------------------

@mcp.tool()
def explain_config_delta(
    node_id: str,
    delta_type: str,
    value_a: str = "",
    value_b: str = "",
) -> str:
    """Generate a natural-language explanation of a configuration delta.

    Args:
        node_id: The delta node ID (e.g., "env:LANG", "python_package:cryptography")
        delta_type: Type of delta (value_changed, only_in_a, only_in_b)
        value_a: Value in environment A (working)
        value_b: Value in environment B (failing)

    Returns:
        Human-readable explanation of the delta and its likely impact
    """
    parts = node_id.split(":", 1)
    layer = parts[0] if len(parts) > 1 else "unknown"
    name = parts[1] if len(parts) > 1 else node_id

    explanations: dict[str, str] = {}

    if delta_type == "value_changed":
        explanations["what"] = f"'{name}' changed from '{value_a}' to '{value_b}'"
    elif delta_type == "only_in_a":
        explanations["what"] = f"'{name}' exists in the working env (value: '{value_a}') but is missing from the failing env"
    elif delta_type == "only_in_b":
        explanations["what"] = f"'{name}' exists in the failing env (value: '{value_b}') but not in the working env"
    else:
        explanations["what"] = f"'{name}' has delta type '{delta_type}'"

    if layer == "env":
        explanations["layer"] = "Environment variable"
        if name.upper() in ("LANG", "LC_ALL", "LC_CTYPE"):
            explanations["impact"] = "Locale settings affect string encoding, sorting, and character handling. A change here commonly causes UnicodeDecodeError or UnicodeEncodeError."
        elif name.upper() == "TZ":
            explanations["impact"] = "Timezone affects datetime parsing and formatting. Can cause test failures or incorrect timestamps."
        elif "SSL" in name.upper() or "CERT" in name.upper():
            explanations["impact"] = "SSL/certificate settings affect HTTPS connections. Can cause SSLError or certificate verification failures."
        else:
            explanations["impact"] = "This environment variable difference may affect application behavior depending on how it's used."
    elif layer == "python_package":
        explanations["layer"] = "Python package"
        explanations["impact"] = f"Version difference in '{name}' may introduce breaking API changes, missing features, or dependency conflicts."
    elif layer == "os_package":
        explanations["layer"] = "OS-level package"
        explanations["impact"] = f"OS package '{name}' differences can affect shared libraries, system tools, and native extensions."
    else:
        explanations["layer"] = layer
        explanations["impact"] = "This configuration difference may affect system behavior."

    return json.dumps(explanations, indent=2)


# ---------------------------------------------------------------------------
# Tool 4: find_similar_past_case
# ---------------------------------------------------------------------------

@mcp.tool()
def find_similar_past_case(
    failure_signature: str,
    top_k: int = 3,
) -> str:
    """Search episodic memory for similar past investigation cases.

    Args:
        failure_signature: The failure trace or error message to match against
        top_k: Number of similar cases to return

    Returns:
        JSON with top-k similar past cases and their solutions
    """
    try:
        from config_detective.memory import MemoryRAG

        rag = MemoryRAG()
        cases = rag.retrieve_similar_cases(failure_signature, top_k=top_k)

        if not cases:
            return json.dumps({
                "found": 0,
                "message": "No similar past cases found in memory.",
                "cases": [],
            })

        return json.dumps({
            "found": len(cases),
            "cases": [
                {
                    "case_id": c.case_id,
                    "similarity": round(c.similarity, 3),
                    "root_cause": c.root_cause,
                    "fix_applied": c.fix_applied,
                    "error_type": c.error_type,
                }
                for c in cases
            ],
        }, indent=2)

    except Exception as e:
        return json.dumps({
            "found": 0,
            "message": f"Memory search unavailable: {e}",
            "cases": [],
        })


# ---------------------------------------------------------------------------
# Tool 5: propose_fix
# ---------------------------------------------------------------------------

@mcp.tool()
def propose_fix(
    snap_a_path: str,
    snap_b_path: str,
    failure_trace: str,
    target_file: str = "",
    repo_path: str = ".",
) -> str:
    """Run a full investigation and propose a fix as a unified diff.

    Args:
        snap_a_path: Path to snapshot A JSON (working)
        snap_b_path: Path to snapshot B JSON (failing)
        failure_trace: The error message or stack trace
        target_file: Optional specific file to patch
        repo_path: Path to the repository root

    Returns:
        JSON with diff, confidence, evidence, and patch_id
    """
    try:
        from config_detective.snapshot import load_snapshot
        from config_detective.agents.state import create_initial_state
        from config_detective.agents.orchestrator import run_investigation_sync
        from config_detective.patcher import propose_fix as _propose

        snap_a = load_snapshot(Path(snap_a_path))
        snap_b = load_snapshot(Path(snap_b_path))

        state = create_initial_state(
            snapshot_a_dict=snap_a.to_json(),
            snapshot_b_dict=snap_b.to_json(),
            failure_trace=failure_trace,
        )

        final_state = run_investigation_sync(state)

        hypothesis = final_state.get("selected_hypothesis")
        if not hypothesis:
            return json.dumps({
                "success": False,
                "message": "Investigation completed but no fix was identified.",
                "confidence": final_state.get("confidence", 0.0),
            })

        fix_code = hypothesis.get("fix_code", "")
        confidence = hypothesis.get("confidence", 0.0)

        if target_file and fix_code:
            target = Path(repo_path) / target_file
            content = target.read_text(encoding="utf-8") if target.exists() else ""
            patch = _propose(
                fix_code=fix_code,
                target_file=target_file,
                content=content,
                trace_id=final_state.get("trace_id", ""),
                confidence=confidence,
                description=hypothesis.get("explanation", ""),
            )
            return json.dumps({
                "success": True,
                "patch_id": patch.patch_id,
                "target_file": patch.target_file,
                "diff": patch.diff_text,
                "confidence": confidence,
                "explanation": hypothesis.get("explanation", ""),
                "fix_code": fix_code,
            }, indent=2)

        return json.dumps({
            "success": True,
            "fix_code": fix_code,
            "confidence": confidence,
            "explanation": hypothesis.get("explanation", ""),
            "fix_suggestion": hypothesis.get("fix_suggestion", ""),
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Tool 6: apply_fix
# ---------------------------------------------------------------------------

@mcp.tool()
def apply_fix(
    patch_id: str = "",
    case_id: str = "",
    target_file: str = "",
    fix_code: str = "",
    repo_path: str = ".",
    confirm: bool = True,
) -> str:
    """Apply a previously proposed fix or a new fix directly.

    Args:
        patch_id: ID of a previously proposed patch to apply
        case_id: Case ID to look up the patch
        target_file: Target file for a direct fix application
        fix_code: Fix code for direct application (if no patch_id)
        repo_path: Path to the repository root
        confirm: If True, this is a confirmed apply (from user OK)

    Returns:
        JSON with applied patch metadata
    """
    if not confirm:
        return json.dumps({
            "success": False,
            "message": "Apply was not confirmed. Set confirm=true to apply.",
        })

    try:
        from config_detective.patcher import apply_fix as _apply, propose_fix as _propose
        from config_detective.patcher.rollback import get_backup_store

        if patch_id or case_id:
            store = get_backup_store(repo_path)
            patch_data = None
            if patch_id:
                patch_data = store.get_patch_by_id(patch_id)
            elif case_id:
                patch_data = store.get_patch_by_case_id(case_id)

            if patch_data:
                from config_detective.patcher.models import PatchRecord
                patch = PatchRecord.from_dict(patch_data)
                result = _apply(patch, repo_path, dry_run=False)
                return json.dumps(result.to_dict(), indent=2)
            else:
                return json.dumps({
                    "success": False,
                    "message": f"No patch found for patch_id={patch_id} case_id={case_id}",
                })

        if target_file and fix_code:
            target = Path(repo_path) / target_file
            content = target.read_text(encoding="utf-8") if target.exists() else ""
            patch = _propose(
                fix_code=fix_code,
                target_file=target_file,
                content=content,
            )
            result = _apply(patch, repo_path, dry_run=False)
            return json.dumps(result.to_dict(), indent=2)

        return json.dumps({
            "success": False,
            "message": "Provide patch_id/case_id or target_file+fix_code",
        })

    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Tool 7: undo_fix
# ---------------------------------------------------------------------------

@mcp.tool()
def undo_fix(
    case_id: str = "",
    patch_id: str = "",
    repo_path: str = ".",
) -> str:
    """Undo a previously applied fix, restoring the file to its pre-patch state.

    Args:
        case_id: Case ID of the patch to undo (optional)
        patch_id: Specific patch ID to undo (optional)
        repo_path: Path to the repository root

    Returns:
        JSON with rollback result
    """
    try:
        from config_detective.patcher import undo_fix as _undo

        result = _undo(
            case_id=case_id or None,
            patch_id=patch_id or None,
            repo_path=repo_path,
        )
        return json.dumps(result.to_dict(), indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_server(transport: str = "stdio") -> None:
    """Start the MCP server.

    Args:
        transport: Transport type ("stdio" or "sse")
    """
    if transport == "sse":
        import asyncio
        asyncio.run(mcp.run_sse_async())
    else:
        import asyncio
        asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    run_server()
