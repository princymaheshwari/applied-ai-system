"""Dockerfile parsing using the dockerfile-parse library.

Extracts structured instruction data from Dockerfiles including:
- Base images (FROM)
- Environment variables (ENV, ARG)
- Commands (RUN, CMD, ENTRYPOINT)
- File operations (COPY, ADD)
- Build configuration (WORKDIR, USER, EXPOSE)
"""

from __future__ import annotations

from pathlib import Path

from config_detective.snapshot.models import DockerfileData, DockerInstruction

# Try to import dockerfile-parse, fall back to manual parsing if unavailable
try:
    from dockerfile_parse import DockerfileParser

    HAS_DOCKERFILE_PARSE = True
except ImportError:
    HAS_DOCKERFILE_PARSE = False


def _parse_with_library(path: Path) -> DockerfileData:
    """Parse Dockerfile using dockerfile-parse library."""
    parser = DockerfileParser(path=str(path))

    instructions: list[DockerInstruction] = []
    base_image: str | None = None

    for entry in parser.structure:
        instruction = entry.get("instruction", "").upper()
        value = entry.get("value", "")
        start_line = entry.get("startline")
        end_line = entry.get("endline")

        # Capture the base image from FROM instruction
        if instruction == "FROM" and not base_image:
            # Handle multi-stage builds: FROM python:3.11 AS builder
            base_image = value.split(" AS ")[0].split(" as ")[0].strip()

        instructions.append(
            DockerInstruction(
                instruction=instruction,
                value=value,
                start_line=start_line,
                end_line=end_line,
            )
        )

    return DockerfileData(
        path=str(path),
        base_image=base_image,
        instructions=instructions,
    )


def _parse_manually(path: Path) -> DockerfileData:
    """Fallback parser when dockerfile-parse is not available.

    This is a simple line-by-line parser that handles basic cases.
    """
    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    instructions: list[DockerInstruction] = []
    base_image: str | None = None
    current_instruction: str | None = None
    current_value_lines: list[str] = []
    current_start_line: int | None = None

    def flush_instruction() -> None:
        nonlocal current_instruction, current_value_lines, current_start_line
        if current_instruction and current_value_lines:
            value = " ".join(current_value_lines).strip()
            # Remove line continuation backslashes
            value = value.replace(" \\ ", " ").replace("\\", "")
            instructions.append(
                DockerInstruction(
                    instruction=current_instruction,
                    value=value,
                    start_line=current_start_line,
                    end_line=len(lines) - 1,
                )
            )
        current_instruction = None
        current_value_lines = []
        current_start_line = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Skip empty lines and comments
        if not stripped or stripped.startswith("#"):
            continue

        # Check if this is a new instruction
        if stripped.split()[0].upper() in {
            "FROM",
            "RUN",
            "CMD",
            "LABEL",
            "MAINTAINER",
            "EXPOSE",
            "ENV",
            "ADD",
            "COPY",
            "ENTRYPOINT",
            "VOLUME",
            "USER",
            "WORKDIR",
            "ARG",
            "ONBUILD",
            "STOPSIGNAL",
            "HEALTHCHECK",
            "SHELL",
        }:
            # Flush any previous instruction
            flush_instruction()

            parts = stripped.split(None, 1)
            current_instruction = parts[0].upper()
            current_value_lines = [parts[1] if len(parts) > 1 else ""]
            current_start_line = i

            # Capture base image
            if current_instruction == "FROM" and not base_image:
                value = current_value_lines[0]
                base_image = value.split(" AS ")[0].split(" as ")[0].strip()
        else:
            # Continuation line
            if current_instruction:
                current_value_lines.append(stripped)

    # Flush the last instruction
    flush_instruction()

    return DockerfileData(
        path=str(path),
        base_image=base_image,
        instructions=instructions,
    )


def parse_dockerfile(path: Path) -> DockerfileData:
    """Parse a Dockerfile and return structured data.

    Uses dockerfile-parse library if available, falls back to manual parsing.
    """
    if HAS_DOCKERFILE_PARSE:
        try:
            return _parse_with_library(path)
        except Exception:
            # Fall back to manual parsing on any error
            return _parse_manually(path)
    else:
        return _parse_manually(path)


def find_dockerfiles(repo_path: Path) -> list[Path]:
    """Find all Dockerfiles in a repository.

    Looks for:
    - Dockerfile
    - Dockerfile.*  (e.g., Dockerfile.dev, Dockerfile.prod)
    - *.dockerfile
    - docker/Dockerfile*
    """
    skip_dirs = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        "legacy",
    }

    dockerfiles: list[Path] = []

    for item in repo_path.rglob("*"):
        # Skip directories we don't care about
        if any(skip in item.parts for skip in skip_dirs):
            continue

        if not item.is_file():
            continue

        name = item.name.lower()

        # Match Dockerfile, Dockerfile.*, or *.dockerfile
        if name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(".dockerfile"):
            dockerfiles.append(item)

    return sorted(dockerfiles)


def capture_dockerfiles(repo_path: Path) -> list[DockerfileData]:
    """Find and parse all Dockerfiles in a repository."""
    dockerfile_paths = find_dockerfiles(repo_path)
    return [parse_dockerfile(p) for p in dockerfile_paths]
