"""Patcher — unified-diff builder, applier, rollback, and interactive confirm.

This module takes a verified fix from the investigation pipeline and:
1. Generates a unified diff showing the proposed change
2. Previews the diff for human review (propose mode, default)
3. Applies the diff to the actual repo files (apply mode, opt-in)
4. Stores pre-patch content for rollback (config-detective undo)

Usage:
    from config_detective.patcher import propose_fix, apply_fix, undo_fix

    # Generate a patch from a fix
    patch = propose_fix(fix_code, target_file, content)

    # Apply it (with dry-run option)
    result = apply_fix(patch, repo_path, dry_run=False)

    # Undo it later
    result = undo_fix(case_id="abc123", repo_path=".")
"""

from .applier import apply_patch, apply_patches, preview_patch
from .confirm import confirm_apply, confirm_undo
from .models import PatchRecord, PatchResult, PatchStatus, TargetFileType
from .rollback import (
    BackupStore,
    create_backup,
    get_backup_store,
    reset_store_cache,
    undo_patch,
)
from .unified_diff import (
    apply_fix_to_content,
    create_patch_record,
    detect_target_type,
    generate_diff,
    infer_target_file,
)


def propose_fix(
    fix_code: str,
    target_file: str,
    content: str,
    trace_id: str = "",
    case_id: str = "",
    confidence: float = 0.0,
    description: str = "",
) -> PatchRecord:
    """Convenience: create a PatchRecord from a fix and target file."""
    return create_patch_record(
        fix_code=fix_code,
        target_file=target_file,
        content=content,
        trace_id=trace_id,
        case_id=case_id,
        confidence=confidence,
        description=description,
    )


def apply_fix(
    patch: PatchRecord,
    repo_path: str = ".",
    dry_run: bool = True,
) -> PatchResult:
    """Convenience: apply a patch with dry-run default."""
    return apply_patch(patch, repo_path, dry_run)


def undo_fix(
    case_id: str | None = None,
    patch_id: str | None = None,
    repo_path: str = ".",
) -> PatchResult:
    """Convenience: undo a patch."""
    return undo_patch(case_id=case_id, patch_id=patch_id, repo_path=repo_path)


__all__ = [
    "BackupStore",
    "PatchRecord",
    "PatchResult",
    "PatchStatus",
    "TargetFileType",
    "apply_fix",
    "apply_fix_to_content",
    "apply_patch",
    "apply_patches",
    "confirm_apply",
    "confirm_undo",
    "create_backup",
    "create_patch_record",
    "detect_target_type",
    "generate_diff",
    "get_backup_store",
    "infer_target_file",
    "preview_patch",
    "propose_fix",
    "reset_store_cache",
    "undo_fix",
    "undo_patch",
]
