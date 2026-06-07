"""Interactive confirmation prompt for patch application.

Shows a diff preview and asks the user to confirm before writing.
Bypassed by ``--yes`` for CI usage.
"""

from __future__ import annotations

import sys
from typing import IO

from .applier import preview_patch
from .models import PatchRecord


def confirm_apply(
    patch: PatchRecord,
    auto_yes: bool = False,
    output: IO[str] | None = None,
    input_stream: IO[str] | None = None,
) -> bool:
    """Show a diff preview and ask the user to confirm.

    Args:
        patch: The patch to preview and confirm
        auto_yes: If True, skip the prompt and return True (CI mode)
        output: Output stream (defaults to sys.stdout)
        input_stream: Input stream (defaults to sys.stdin)

    Returns:
        True if the user confirmed (or auto_yes is True), False otherwise
    """
    out = output or sys.stdout
    inp = input_stream or sys.stdin

    preview = preview_patch(patch)
    out.write(preview + "\n\n")

    if auto_yes:
        out.write("Auto-applying (--yes flag set)\n")
        return True

    out.write("Apply this patch? [y/N] ")
    out.flush()

    try:
        response = inp.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        out.write("\nAborted.\n")
        return False

    return response in ("y", "yes")


def confirm_undo(
    patch_id: str,
    target_file: str,
    auto_yes: bool = False,
    output: IO[str] | None = None,
    input_stream: IO[str] | None = None,
) -> bool:
    """Ask the user to confirm an undo operation.

    Args:
        patch_id: ID of the patch to undo
        target_file: File that will be reverted
        auto_yes: Skip prompt if True
        output: Output stream
        input_stream: Input stream

    Returns:
        True if confirmed
    """
    out = output or sys.stdout
    inp = input_stream or sys.stdin

    out.write(f"Undo patch {patch_id} on {target_file}?\n")
    out.write("This will restore the file to its pre-patch state.\n")

    if auto_yes:
        out.write("Auto-confirming (--yes flag set)\n")
        return True

    out.write("Proceed? [y/N] ")
    out.flush()

    try:
        response = inp.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        out.write("\nAborted.\n")
        return False

    return response in ("y", "yes")
