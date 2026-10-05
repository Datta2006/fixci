"""Validation and application of unified diffs produced by the LLM."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

MAX_CHANGED_LINES = 200

# Directories/files FixCI will never touch.
FORBIDDEN_PREFIXES = (".github/", ".git/")
SECRET_PATH_RE = re.compile(
    r"(^|/)(\.env(\..*)?|.*\.pem|.*\.key|.*\.p12|id_rsa|id_ed25519"
    r"|credentials(\.json)?|secrets?\.ya?ml)$",
    re.IGNORECASE,
)


class PatchValidationError(ValueError):
    """Raised when a generated patch is unsafe or malformed."""


class PatchInfo(BaseModel):
    """Static facts about a validated patch."""

    files: list[str] = Field(default_factory=list)
    changed_lines: int = 0


def _strip_ab_prefix(path: str) -> str:
    """Turn ``b/src/x.py`` into ``src/x.py`` and drop trailing tab metadata."""
    path = path.split("\t", 1)[0].strip()
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path


def parse_patch_paths(patch: str) -> list[str]:
    """Extract the file paths a unified diff writes to (``+++`` headers)."""
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith("+++ "):
            raw = line[4:].strip()
            if raw == "/dev/null":
                continue
            path = _strip_ab_prefix(raw)
            if path and path not in paths:
                paths.append(path)
    return paths


def count_changed_lines(patch: str) -> int:
    """Count added/removed content lines, ignoring diff headers."""
    count = 0
    for line in patch.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            count += 1
    return count


def _check_path_safety(path: str) -> None:
    if path.startswith("/") or path.startswith("..") or "/../" in path:
        raise PatchValidationError(f"patch touches a path outside the repo: {path}")
    if path.startswith(FORBIDDEN_PREFIXES):
        raise PatchValidationError(f"patch touches a protected path: {path}")
    if SECRET_PATH_RE.search(path):
        raise PatchValidationError(f"patch touches a secret-like file: {path}")


def validate_patch(patch: str, max_changed_lines: int = MAX_CHANGED_LINES) -> PatchInfo:
    """Statically validate a patch before it is ever applied.

    Rejects empty patches, oversized patches, paths outside the repo, paths
    under ``.github/``, and secret-like filenames.
    """
    if not patch or not patch.strip():
        raise PatchValidationError("empty patch")

    files = parse_patch_paths(patch)
    if not files:
        raise PatchValidationError("patch contains no file changes")

    for path in files:
        _check_path_safety(path)

    changed = count_changed_lines(patch)
    if changed == 0:
        raise PatchValidationError("patch contains no added or removed lines")
    if changed > max_changed_lines:
        raise PatchValidationError(
            f"patch changes {changed} lines, exceeding the {max_changed_lines} line limit"
        )
    return PatchInfo(files=files, changed_lines=changed)


def apply_patch(repo_dir: str | Path, patch: str) -> None:
    """Apply a unified diff inside ``repo_dir`` using ``git apply``.

    Raises ``PatchValidationError`` if ``git apply --check`` fails.
    """
    repo = str(repo_dir)
    if not check_patch(repo, patch):
        raise PatchValidationError("git apply --check failed")
    subprocess.run(
        ["git", "apply", "--whitespace=fix", "-"],
        input=patch,
        cwd=repo,
        text=True,
        check=True,
        capture_output=True,
    )


def check_patch(repo_dir: str | Path, patch: str) -> bool:
    """Run ``git apply --check`` for a patch against a working tree."""
    result = subprocess.run(
        ["git", "apply", "--check", "-"],
        input=patch,
        cwd=str(repo_dir),
        text=True,
        capture_output=True,
    )
    return result.returncode == 0
