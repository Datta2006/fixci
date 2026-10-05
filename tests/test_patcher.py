"""Tests for unified-diff validation and ``git apply`` integration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.patcher import (
    count_changed_lines,
    parse_patch_paths,
    validate_patch,
    PatchValidationError,
    apply_patch,
    check_patch,
)

SIMPLE_PATCH = """\
diff --git a/requirements.txt b/requirements.txt
--- a/requirements.txt
+++ b/requirements.txt
@@ -1 +1 @@
-flask==0.0.1
+flask==3.0.3
"""


def test_parse_patch_paths() -> None:
    assert parse_patch_paths(SIMPLE_PATCH) == ["requirements.txt"]


def test_count_changed_lines() -> None:
    assert count_changed_lines(SIMPLE_PATCH) == 2


def test_validate_patch_ok() -> None:
    info = validate_patch(SIMPLE_PATCH)
    assert info.files == ["requirements.txt"]
    assert info.changed_lines == 2


def test_validate_rejects_empty() -> None:
    with pytest.raises(PatchValidationError):
        validate_patch("")
    with pytest.raises(PatchValidationError):
        validate_patch("   \n  ")


def test_validate_rejects_no_file_changes() -> None:
    with pytest.raises(PatchValidationError):
        validate_patch("not a diff at all")


def test_validate_rejects_github_dir() -> None:
    patch = SIMPLE_PATCH.replace("requirements.txt", ".github/workflows/ci.yml")
    with pytest.raises(PatchValidationError, match="protected"):
        validate_patch(patch)


def test_validate_rejects_secret_files() -> None:
    for name in (".env", "server.key", "secrets.yaml", "id_rsa"):
        patch = SIMPLE_PATCH.replace("requirements.txt", name)
        with pytest.raises(PatchValidationError):
            validate_patch(patch)


def test_validate_rejects_path_outside_repo() -> None:
    patch = SIMPLE_PATCH.replace("requirements.txt", "../outside.py")
    with pytest.raises(PatchValidationError):
        validate_patch(patch)

    absolute = SIMPLE_PATCH.replace("requirements.txt", "/etc/passwd")
    with pytest.raises(PatchValidationError):
        validate_patch(absolute)


def test_validate_rejects_oversized_patch() -> None:
    added = "\n".join(f"+line {i}" for i in range(250))
    patch = (
        "--- a/big.txt\n"
        "+++ b/big.txt\n"
        "@@ -0,0 +1,250 @@\n"
        f"{added}\n"
    )
    with pytest.raises(PatchValidationError, match="exceed"):
        validate_patch(patch)


def test_validate_rejects_no_content_lines() -> None:
    patch = "--- a/x.py\n+++ b/x.py\n@@ -1,0 +1,0 @@\n"
    with pytest.raises(PatchValidationError):
        validate_patch(patch)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "requirements.txt").write_text("flask==0.0.1\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def test_check_and_apply_patch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    assert check_patch(repo, SIMPLE_PATCH) is True
    apply_patch(repo, SIMPLE_PATCH)
    assert (repo / "requirements.txt").read_text().strip() == "flask==3.0.3"


def test_check_patch_false_when_context_mismatch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    bad = SIMPLE_PATCH.replace("-flask==0.0.1", "-django==0.0.1")
    assert check_patch(repo, bad) is False
    with pytest.raises(PatchValidationError, match="git apply --check failed"):
        apply_patch(repo, bad)
