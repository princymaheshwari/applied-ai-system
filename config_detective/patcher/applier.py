"""Patch applier — writes the patched content to the target file.

Supports two modes:
- **dry-run** (default): shows what would change without touching the file
- **apply**: writes the patched content, recording the original for rollback

The applier never silently overwrites — it always creates a backup via
the rollback module before writing.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import PatchRecord, PatchResult, PatchStatus
from .rollback import create_backup, get_backup_store

logger = logging.getLogger(__name__)


def apply_patch(
    patch: PatchRecord,
    repo_path: str | Path = ".",
    dry_run: bool = True,
) -> PatchResult:
    """Apply a patch to the target file.

    Args:
        patch: The PatchRecord to apply
        repo_path: Root of the repository
        dry_run: If True, don't actually write the file

    Returns:
        PatchResult with success status
    """
    repo = Path(repo_path).resolve()
    target = repo / patch.target_file

    if dry_run:
        return PatchResult(
            success=True,
            patch_id=patch.patch_id,
            target_file=patch.target_file,
            action="dry_run",
            message=f"Would apply patch to {patch.target_file}",
            dry_run=True,
        )

    try:
        if target.exists():
            current_content = target.read_text(encoding="utf-8")
        else:
            current_content = ""

        create_backup(patch.patch_id, patch.target_file, current_content, repo_path)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(patch.patched_content, encoding="utf-8")

        patch.status = PatchStatus.APPLIED
        patch.applied_at = datetime.utcnow()

        store = get_backup_store(repo_path)
        store.record_applied_patch(patch)

        logger.info(f"Applied patch {patch.patch_id} to {patch.target_file}")

        return PatchResult(
            success=True,
            patch_id=patch.patch_id,
            target_file=patch.target_file,
            action="applied",
            message=f"Successfully applied patch to {patch.target_file}",
            dry_run=False,
        )

    except Exception as e:
        logger.exception(f"Failed to apply patch {patch.patch_id}")
        patch.status = PatchStatus.FAILED
        return PatchResult(
            success=False,
            patch_id=patch.patch_id,
            target_file=patch.target_file,
            action="failed",
            message=f"Failed to apply patch: {e}",
            dry_run=False,
        )


def apply_patches(
    patches: list[PatchRecord],
    repo_path: str | Path = ".",
    dry_run: bool = True,
) -> list[PatchResult]:
    """Apply multiple patches.

    Args:
        patches: List of PatchRecords
        repo_path: Root of the repository
        dry_run: If True, don't write files

    Returns:
        List of PatchResults
    """
    results: list[PatchResult] = []
    for patch in patches:
        result = apply_patch(patch, repo_path, dry_run)
        results.append(result)
        if not result.success and not dry_run:
            break
    return results


def preview_patch(patch: PatchRecord) -> str:
    """Generate a human-readable preview of a patch.

    Args:
        patch: The PatchRecord to preview

    Returns:
        Formatted preview string with diff
    """
    lines = []
    lines.append(f"--- Patch Preview: {patch.patch_id} ---")
    lines.append(f"Target: {patch.target_file} ({patch.target_type.value})")
    lines.append(f"Confidence: {patch.confidence:.0%}")
    if patch.description:
        lines.append(f"Description: {patch.description}")
    lines.append("")
    if patch.diff_text:
        lines.append(patch.diff_text)
    else:
        lines.append("(no changes)")
    lines.append("")
    lines.append(f"--- End of Patch {patch.patch_id} ---")
    return "\n".join(lines)
