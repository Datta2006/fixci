"""Log trimming, failing-command extraction, classification, and redaction."""

from __future__ import annotations

import re

from app.models import FailureType

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# GitHub Actions prefixes each log line with "2024-01-01T00:00:00.1234567Z ".
GH_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?")
LOG_DIRECTIVE_RE = re.compile(r"^##\[[a-zA-Z]+\]")

# Order matters: the first matching pattern wins.
COMMAND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ruff", re.compile(r"\bruff (?:check|format)\b[^\n]*")),
    ("black", re.compile(r"\bblack (?:--check|--diff)?[^\n]*")),
    ("flake8", re.compile(r"\bflake8\b[^\n]*")),
    ("pytest", re.compile(r"\b(?:python -m )?pytest\b[^\n]*")),
    ("pip", re.compile(r"\bpip install\b[^\n]*")),
]

ERROR_MARKERS = (
    "Traceback (most recent call last)",
    "##[error]",
    "error:",
    "Error:",
    "ERROR:",
    "FAILED",
    "E   ",
    "AssertionError",
    "ModuleNotFoundError",
)

DEPENDENCY_MARKERS = (
    "modulenotfounderror",
    "no matching distribution found",
    "could not find a version that satisfies",
    "resolutionimpossible",
    "error: cannot install",
    "conflicting dependencies",
    "because these package versions have conflicting dependencies",
    "incompatible requirements",
    "packaging.version",
    "invalid requirement",
    "no version of",
)

LINT_MARKERS = (
    "would reformat",
    "files would be reformatted",
    "would be reformatted",
    "error: would reformat",
    "found 1 error",
    "found 2 error",
    "found 3 error",
    "f401",
    "e501",
    "f841",
    "imported but unused",
    "undefined name",
    "line too long",
)

TEST_MARKERS = (
    "short test summary info",
    "test session starts",
    "assertionerror",
    "assert ",
    " = failed",
    "failed tests/",
)

MAX_CONTEXT_LINES_BEFORE_ERROR = 200
MAX_LOG_CHARS = 24_000  # ~6000 tokens at ~4 chars/token
CHARS_PER_TOKEN = 4

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"), "[REDACTED_ANTHROPIC_KEY]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (re.compile(r"(?i)authorization:\s*(?:bearer|token)\s+\S+"), "Authorization: [REDACTED]"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"), r"\1=[REDACTED]"),
]


def redact_secrets(text: str) -> str:
    """Mask anything that looks like a credential before it leaves the process."""
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences."""
    return ANSI_RE.sub("", text)


def strip_log_noise(text: str) -> str:
    """Remove ANSI codes, leading timestamps, and Actions group directives."""
    lines: list[str] = []
    for raw in strip_ansi(text).splitlines():
        line = GH_TS_RE.sub("", raw)
        line = LOG_DIRECTIVE_RE.sub("", line)
        lines.append(line.rstrip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command extraction
# ---------------------------------------------------------------------------


def extract_failing_command(text: str) -> str | None:
    """Return the first recognised build/test command seen in the log.

    Matches against workflow-visible invocations such as ``pytest tests/x.py``
    or ``ruff check .``, preferring the earliest recognised command.
    """
    for line in text.splitlines():
        for _tool, pattern in COMMAND_PATTERNS:
            match = pattern.search(line)
            if match:
                command = match.group(0).strip()
                # Drop shell prompts / "- name:" style step headers.
                command = re.sub(r"^(?:\$ |> )", "", command).strip()
                return command
    return None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _has_any(text_lower: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text_lower for marker in markers)


def classify_failure(text: str, command: str | None = None) -> FailureType:
    """Rule-based failure classification.

    The extracted command biases the result; generic markers are the fallback.
    Anything that does not match is ``unsupported``.
    """
    blob = text.lower()

    if command:
        cmd = command.lower()
        if "pip" in cmd and _has_any(blob, DEPENDENCY_MARKERS):
            return FailureType.dependency
        if any(tool in cmd for tool in ("ruff", "black", "flake8")):
            return FailureType.lint
        if "pytest" in cmd and _has_any(blob, TEST_MARKERS):
            return FailureType.test_failure

    if _has_any(blob, DEPENDENCY_MARKERS):
        return FailureType.dependency
    if _has_any(blob, LINT_MARKERS):
        return FailureType.lint
    if _has_any(blob, TEST_MARKERS):
        return FailureType.test_failure
    return FailureType.unsupported


# ---------------------------------------------------------------------------
# Trimming
# ---------------------------------------------------------------------------


def _first_error_index(lines: list[str]) -> int | None:
    for idx, line in enumerate(lines):
        if any(marker in line for marker in ERROR_MARKERS):
            return idx
    return None


def trim_log(text: str, max_chars: int = MAX_LOG_CHARS) -> str:
    """Clean, trim, redact, and size-cap a CI log.

    Keeps the last ``MAX_CONTEXT_LINES_BEFORE_ERROR`` lines before the first
    error marker plus everything after it (where tracebacks live). Falls back
    to the tail of the log when no error marker is present.
    """
    cleaned = strip_log_noise(text)
    lines = cleaned.splitlines()

    error_idx = _first_error_index(lines)
    if error_idx is not None:
        start = max(0, error_idx - MAX_CONTEXT_LINES_BEFORE_ERROR)
        kept = lines[start:]
    else:
        kept = lines[-MAX_CONTEXT_LINES_BEFORE_ERROR * 2 :]

    trimmed = "\n".join(kept)
    if len(trimmed) > max_chars:
        # Keep the tail: the interesting error output is at the end.
        trimmed = "…[log truncated]…\n" + trimmed[-max_chars:]
    return redact_secrets(trimmed)


def approx_tokens(text: str) -> int:
    """Rough token estimate used for budgeting prompts."""
    return len(text) // CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# Referenced files
# ---------------------------------------------------------------------------

# pytest tracebacks:  tests/test_x.py:12: AssertionError
# python tracebacks:  File "/app/tests/test_x.py", line 12, in test_foo
TRACEBACK_FILE_RE = re.compile(
    r'(?:File "(?P<qpath>[^"]+)", line \d+|(?P<path>[\w./-]+\.py):\d+)'
)


def extract_referenced_files(text: str, limit: int = 5) -> list[str]:
    """Return repo-relative Python file paths mentioned in a traceback.

    Paths are normalised (absolute sandbox prefixes are stripped) and
    de-duplicated while preserving order.
    """
    found: list[str] = []
    for match in TRACEBACK_FILE_RE.finditer(text):
        raw = match.group("qpath") or match.group("path") or ""
        if not raw:
            continue
        for prefix in ("/app/", "/github/workspace/", "/repo/"):
            idx = raw.find(prefix)
            if idx != -1:
                raw = raw[idx + len(prefix) :]
                break
        if raw.startswith("./"):
            raw = raw[2:]
        # Skip anything still absolute (stdlib / site-packages paths).
        if raw and not raw.startswith("/") and raw not in found:
            found.append(raw)
        if len(found) >= limit:
            break
    return found
