"""Tests for log trimming, command extraction, classification, and redaction."""

from __future__ import annotations

from app.log_analyzer import (
    approx_tokens,
    classify_failure,
    extract_failing_command,
    extract_referenced_files,
    redact_secrets,
    strip_ansi,
    strip_log_noise,
    trim_log,
)
from app.models import FailureType

DEPENDENCY_LOG = """\
2024-01-01T00:00:00.1234567Z ##[group]Run pip install -r requirements.txt
2024-01-01T00:00:00.1234567Z pip install -r requirements.txt
2024-01-01T00:00:01.0000000Z Collecting flask==0.0.1
2024-01-01T00:00:02.0000000Z ERROR: Could not find a version that satisfies the requirement flask==0.0.1
2024-01-01T00:00:02.0000000Z ERROR: No matching distribution found for flask==0.0.1
2024-01-01T00:00:02.0000000Z ##[error]Process completed with exit code 1.
"""

LINT_LOG = """\
2024-01-01T00:00:00.1000000Z ##[group]Run ruff check .
2024-01-01T00:00:00.2000000Z ruff check .
2024-01-01T00:00:00.3000000Z app/x.py:1:8: F401 [*] `os` imported but unused
2024-01-01T00:00:00.4000000Z Found 1 error.
2024-01-01T00:00:00.5000000Z ##[error]Process completed with exit code 1.
"""

TEST_LOG = """\
2024-01-01T00:00:00.1000000Z pytest tests/test_math.py -q
2024-01-01T00:00:01.0000000Z =========================== short test summary info ============================
2024-01-01T00:00:01.1000000Z FAILED tests/test_math.py::test_add - AssertionError: assert 3 == 4
2024-01-01T00:00:01.2000000Z 1 failed, 2 passed in 0.05s
"""


def test_strip_ansi_removes_escape_sequences() -> None:
    assert strip_ansi("\x1b[31mred\x1b[0m") == "red"


def test_strip_log_noise_removes_timestamps_and_directives() -> None:
    out = strip_log_noise("2024-01-01T00:00:00.1234567Z ##[group]Run tests\nplain line")
    assert out == "Run tests\nplain line"


def test_extract_failing_command_each_tool() -> None:
    assert extract_failing_command(DEPENDENCY_LOG) == "pip install -r requirements.txt"
    assert extract_failing_command(LINT_LOG) == "ruff check ."
    assert extract_failing_command(TEST_LOG) == "pytest tests/test_math.py -q"


def test_extract_failing_command_none_when_absent() -> None:
    assert extract_failing_command("nothing to see here") is None


def test_classify_dependency() -> None:
    cmd = extract_failing_command(DEPENDENCY_LOG)
    assert classify_failure(DEPENDENCY_LOG, cmd) is FailureType.dependency


def test_classify_lint() -> None:
    cmd = extract_failing_command(LINT_LOG)
    assert classify_failure(LINT_LOG, cmd) is FailureType.lint


def test_classify_test_failure() -> None:
    cmd = extract_failing_command(TEST_LOG)
    assert classify_failure(TEST_LOG, cmd) is FailureType.test_failure


def test_classify_unsupported() -> None:
    assert classify_failure("Something odd happened during deploy") is FailureType.unsupported


def test_classify_without_command_uses_markers() -> None:
    assert classify_failure("ModuleNotFoundError: No module named 'foo'") is FailureType.dependency


def test_trim_log_keeps_context_before_error_and_traceback() -> None:
    lines = [f"line {i}" for i in range(1000)]
    lines[50] = "EARLY_MARKER"
    lines[900] = "Traceback (most recent call last)"
    lines[901] = "AssertionError: boom"
    log = "\n".join(lines)

    trimmed = trim_log(log)
    assert "Traceback (most recent call last)" in trimmed
    assert "AssertionError: boom" in trimmed
    # Error is at index 900; the window starts at 700, so line 50 is dropped.
    assert "EARLY_MARKER" not in trimmed
    # A line 100 before the error is retained.
    assert "line 850" in trimmed


def test_trim_log_hard_cap() -> None:
    log = "\n".join(["x" * 200 for _ in range(2000)]) + "\nERROR: boom"
    trimmed = trim_log(log, max_chars=5_000)
    assert len(trimmed) <= 5_000 + len("…[log truncated]…\n")
    assert "ERROR: boom" in trimmed
    assert approx_tokens(trimmed) < 2000


def test_redact_secrets_masks_tokens() -> None:
    text = "token=ghp_" + "a" * 30 + " and api_key: sk-ant-" + "b" * 30
    redacted = redact_secrets(text)
    assert "ghp_" not in redacted
    assert "sk-ant-" not in redacted
    assert "[REDACTED" in redacted


def test_redact_secrets_masks_private_key() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    assert "MIIabc" not in redact_secrets(pem)


def test_trim_log_redacts_before_returning() -> None:
    log = "ERROR: bad ghp_" + "c" * 30 + "\nmore"
    assert "ghp_" not in trim_log(log)


def test_extract_referenced_files() -> None:
    log = (
        'File "/app/tests/test_x.py", line 12, in test_foo\n'
        "tests/test_y.py:3: AssertionError\n"
        'File "/app/tests/test_x.py", line 20, in test_bar\n'
    )
    files = extract_referenced_files(log)
    assert files == ["tests/test_x.py", "tests/test_y.py"]


def test_extract_referenced_files_ignores_absolute_paths() -> None:
    assert extract_referenced_files('File "/usr/lib/python3.12/os.py", line 1, in x') == []
