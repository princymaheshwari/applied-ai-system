"""Model Context Protocol server for CONFIG DETECTIVE.

Exposes the agent's investigation tools so Cursor and Claude Desktop can
drive investigations directly from the IDE.

Tools provided:
- compare_envs — diff two environment snapshots
- bisect_dockerfile_layer — isolate a failing Dockerfile layer
- explain_config_delta — natural-language explanation of a delta
- find_similar_past_case — search episodic memory
- propose_fix — run investigation and propose a diff
- apply_fix — apply a patch (with explicit user confirmation)
- undo_fix — roll back the last applied patch

Start via CLI: config-detective mcp-serve
"""

from .server import mcp, run_server

__all__ = ["mcp", "run_server"]
