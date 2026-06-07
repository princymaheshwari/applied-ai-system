"""Rollback support — backs up pre-patch file contents and provides undo.

The rollback store keeps a local JSON file (`.config-detective/patches.json`)
with every applied patch's original content. `config-detective undo` reads
this store to revert the last (or a specific) applied patch.

Future: patch records are also stored in Supabase for cross-machine undo.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import PatchRecord, PatchResult, PatchStatus

logger = logging.getLogger(__name__)

STORE_DIR = ".config-detective"
STORE_FILE = "patches.json"


class BackupStore:
    """Local JSON-based store for patch backups and history."""

    def __init__(self, repo_path: str | Path = "."):
        self._repo = Path(repo_path).resolve()
        self._store_dir = self._repo / STORE_DIR
        self._store_file = self._store_dir / STORE_FILE
        self._data: dict[str, Any] = {"patches": [], "backups": {}}
        self._load()

    def _load(self) -> None:
        if self._store_file.exists():
            try:
                self._data = json.loads(self._store_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupted patch store, starting fresh")
                self._data = {"patches": [], "backups": {}}

    def _save(self) -> None:
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._store_file.write_text(
            json.dumps(self._data, indent=2, default=str),
            encoding="utf-8",
        )

    def save_backup(self, patch_id: str, filepath: str, content: str) -> None:
        """Save original file content before a patch is applied."""
        self._data["backups"][patch_id] = {
            "filepath": filepath,
            "content": content,
            "backed_up_at": datetime.utcnow().isoformat(),
        }
        self._save()

    def get_backup(self, patch_id: str) -> dict[str, Any] | None:
        """Get the backup for a specific patch."""
        return self._data["backups"].get(patch_id)

    def record_applied_patch(self, patch: PatchRecord) -> None:
        """Record that a patch was applied."""
        self._data["patches"].append(patch.to_dict())
        self._save()

    def get_applied_patches(self) -> list[dict[str, Any]]:
        """Get all applied patches, newest first."""
        patches = [
            p for p in self._data.get("patches", [])
            if p.get("status") == PatchStatus.APPLIED.value
        ]
        return list(reversed(patches))

    def get_last_applied_patch(self) -> dict[str, Any] | None:
        """Get the most recently applied patch."""
        applied = self.get_applied_patches()
        return applied[0] if applied else None

    def get_patch_by_id(self, patch_id: str) -> dict[str, Any] | None:
        """Find a patch by its ID."""
        for p in self._data.get("patches", []):
            if p.get("patch_id") == patch_id:
                return p
        return None

    def get_patch_by_case_id(self, case_id: str) -> dict[str, Any] | None:
        """Find a patch by its case ID."""
        for p in reversed(self._data.get("patches", [])):
            if p.get("case_id") == case_id:
                return p
        return None

    def mark_rolled_back(self, patch_id: str) -> None:
        """Mark a patch as rolled back."""
        for p in self._data.get("patches", []):
            if p.get("patch_id") == patch_id:
                p["status"] = PatchStatus.ROLLED_BACK.value
                break
        self._save()


_store_cache: dict[str, BackupStore] = {}


def get_backup_store(repo_path: str | Path = ".") -> BackupStore:
    """Get or create a BackupStore for the given repo."""
    key = str(Path(repo_path).resolve())
    if key not in _store_cache:
        _store_cache[key] = BackupStore(repo_path)
    return _store_cache[key]


def reset_store_cache() -> None:
    """Clear the store cache (for testing)."""
    _store_cache.clear()


def create_backup(
    patch_id: str,
    filepath: str,
    content: str,
    repo_path: str | Path = ".",
) -> None:
    """Save a file's content before patching it."""
    store = get_backup_store(repo_path)
    store.save_backup(patch_id, filepath, content)


def undo_patch(
    case_id: str | None = None,
    patch_id: str | None = None,
    repo_path: str | Path = ".",
) -> PatchResult:
    """Undo a previously applied patch.

    If neither case_id nor patch_id is given, undoes the most recent patch.

    Args:
        case_id: Case ID to undo
        patch_id: Specific patch ID to undo
        repo_path: Root of the repository

    Returns:
        PatchResult with success status
    """
    repo = Path(repo_path).resolve()
    store = get_backup_store(repo_path)

    patch_data: dict[str, Any] | None = None

    if patch_id:
        patch_data = store.get_patch_by_id(patch_id)
    elif case_id:
        patch_data = store.get_patch_by_case_id(case_id)
    else:
        patch_data = store.get_last_applied_patch()

    if not patch_data:
        return PatchResult(
            success=False,
            action="undo",
            message="No patch found to undo",
        )

    found_patch_id = patch_data.get("patch_id", "")
    target_file = patch_data.get("target_file", "")

    if patch_data.get("status") == PatchStatus.ROLLED_BACK.value:
        return PatchResult(
            success=False,
            patch_id=found_patch_id,
            target_file=target_file,
            action="undo",
            message=f"Patch {found_patch_id} was already rolled back",
        )

    backup = store.get_backup(found_patch_id)
    if not backup:
        return PatchResult(
            success=False,
            patch_id=found_patch_id,
            target_file=target_file,
            action="undo",
            message=f"No backup found for patch {found_patch_id}",
        )

    try:
        target = repo / target_file
        target.write_text(backup["content"], encoding="utf-8")
        store.mark_rolled_back(found_patch_id)

        logger.info(f"Rolled back patch {found_patch_id} on {target_file}")

        return PatchResult(
            success=True,
            patch_id=found_patch_id,
            target_file=target_file,
            action="undo",
            message=f"Successfully rolled back {target_file} to pre-patch state",
        )

    except Exception as e:
        logger.exception(f"Failed to undo patch {found_patch_id}")
        return PatchResult(
            success=False,
            patch_id=found_patch_id,
            target_file=target_file,
            action="undo",
            message=f"Failed to undo patch: {e}",
        )
