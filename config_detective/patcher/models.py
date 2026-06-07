"""Data models for the patcher module.

Defines the structures that flow through the patch pipeline:
fix_code → diff generation → preview → apply/rollback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4


class PatchStatus(str, Enum):
    """Status of a patch."""

    PROPOSED = "proposed"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class TargetFileType(str, Enum):
    """Types of files that can be patched."""

    DOCKERFILE = "Dockerfile"
    REQUIREMENTS = "requirements.txt"
    ENV_FILE = ".env"
    DOCKER_COMPOSE = "docker-compose.yml"
    PYPROJECT = "pyproject.toml"
    GENERIC = "generic"


@dataclass
class PatchHunk:
    """A single hunk in a unified diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        header = f"@@ -{self.old_start},{self.old_count} +{self.new_start},{self.new_count} @@"
        return header + "\n" + "\n".join(self.lines)


@dataclass
class PatchRecord:
    """A complete patch operation record.

    Stores everything needed to apply, preview, and roll back a patch.
    """

    patch_id: str = field(default_factory=lambda: str(uuid4())[:12])
    case_id: str = ""
    trace_id: str = ""
    target_file: str = ""
    target_type: TargetFileType = TargetFileType.GENERIC
    diff_text: str = ""
    original_content: str = ""
    patched_content: str = ""
    description: str = ""
    fix_code: str = ""
    confidence: float = 0.0
    status: PatchStatus = PatchStatus.PROPOSED
    created_at: datetime = field(default_factory=datetime.utcnow)
    applied_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "case_id": self.case_id,
            "trace_id": self.trace_id,
            "target_file": self.target_file,
            "target_type": self.target_type.value,
            "diff_text": self.diff_text,
            "original_content": self.original_content,
            "patched_content": self.patched_content,
            "description": self.description,
            "fix_code": self.fix_code,
            "confidence": self.confidence,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PatchRecord":
        return cls(
            patch_id=data.get("patch_id", str(uuid4())[:12]),
            case_id=data.get("case_id", ""),
            trace_id=data.get("trace_id", ""),
            target_file=data.get("target_file", ""),
            target_type=TargetFileType(data.get("target_type", "generic")),
            diff_text=data.get("diff_text", ""),
            original_content=data.get("original_content", ""),
            patched_content=data.get("patched_content", ""),
            description=data.get("description", ""),
            fix_code=data.get("fix_code", ""),
            confidence=data.get("confidence", 0.0),
            status=PatchStatus(data.get("status", "proposed")),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if isinstance(data.get("created_at"), str)
                else datetime.utcnow()
            ),
            applied_at=(
                datetime.fromisoformat(data["applied_at"])
                if isinstance(data.get("applied_at"), str) and data["applied_at"]
                else None
            ),
        )


@dataclass
class PatchResult:
    """Result of a patch apply or undo operation."""

    success: bool = False
    patch_id: str = ""
    target_file: str = ""
    action: str = ""
    message: str = ""
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "patch_id": self.patch_id,
            "target_file": self.target_file,
            "action": self.action,
            "message": self.message,
            "dry_run": self.dry_run,
        }
