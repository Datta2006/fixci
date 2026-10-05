"""Tests for sandbox helpers that do not require a Docker daemon."""

from __future__ import annotations

import io
import tarfile

from app.models import Diagnosis, FailureContext, FailureType, WorkflowRunRef
from app.sandbox import Sandbox, _is_safe_relpath, _q, _tar_file


def _context(failing_command: str | None = "pytest -q") -> FailureContext:
    return FailureContext(
        run=WorkflowRunRef(
            run_id=1,
            repo_full_name="o/r",
            installation_id=1,
            head_branch="main",
            head_sha="abc",
        ),
        trimmed_log="FAILED tests/test_x.py",
        failure_type=FailureType.test_failure,
        failing_command=failing_command,
    )


def _diagnosis() -> Diagnosis:
    return Diagnosis(
        root_cause="off by one",
        explanation="fix the math",
        confidence="high",
        files_changed=["calc.py"],
        patch="--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-x\n+y\n",
    )


def test_sandbox_disabled_returns_not_passed() -> None:
    sandbox = Sandbox(enabled=False)
    result = sandbox.run(_context(), _diagnosis())
    assert result.passed is False
    assert result.error == "sandbox disabled by configuration"


def test_sandbox_without_failing_command() -> None:
    result = Sandbox(enabled=True).run(_context(failing_command=None), _diagnosis())
    assert result.passed is False
    assert "no failing command" in (result.error or "")


def test_q_shell_quotes_single_quotes() -> None:
    assert _q("pytest -q") == "'pytest -q'"
    assert _q("echo 'hi'") == "'echo '\\''hi'\\'''"


def test_tar_file_contains_named_payload() -> None:
    blob = _tar_file("patch.diff", "hello world")
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        member = tar.getmembers()[0]
        assert member.name == "patch.diff"
        assert tar.extractfile(member).read() == b"hello world"  # type: ignore[union-attr]


def test_is_safe_relpath() -> None:
    assert _is_safe_relpath("calc.py") is True
    assert _is_safe_relpath("app/main.py") is True
    assert _is_safe_relpath("") is False
    assert _is_safe_relpath("/etc/passwd") is False
    assert _is_safe_relpath("../secret") is False
    assert _is_safe_relpath("a/../../b") is False
