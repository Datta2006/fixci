"""Tests for LLM response parsing and prompt construction."""

from __future__ import annotations

import pytest

from app.llm import (
    LLMClient,
    build_user_prompt,
    parse_diagnosis,
    strip_code_fences,
)
from app.models import (
    Attempt,
    Confidence,
    FailureContext,
    FailureType,
    VerificationResult,
    WorkflowRunRef,
)

VALID_JSON = """{
  "root_cause": "flask 0.0.1 does not exist",
  "explanation": "Pin to an existing release.",
  "confidence": "high",
  "files_changed": ["requirements.txt"],
  "patch": "--- a/requirements.txt\\n+++ b/requirements.txt\\n@@ -1 +1 @@\\n-flask==0.0.1\\n+flask==3.0.3\\n"
}"""


def _context() -> FailureContext:
    return FailureContext(
        run=WorkflowRunRef(
            run_id=1,
            repo_full_name="o/r",
            installation_id=1,
            head_branch="main",
            head_sha="abc",
        ),
        trimmed_log="ERROR: no matching distribution",
        failure_type=FailureType.dependency,
        failing_command="pip install -r requirements.txt",
    )


def test_strip_code_fences() -> None:
    assert strip_code_fences("```json\n{}\n```") == "{}"
    assert strip_code_fences("```\n{}\n```") == "{}"
    assert strip_code_fences("{}") == "{}"


def test_parse_diagnosis_valid() -> None:
    diag = parse_diagnosis(VALID_JSON)
    assert diag.confidence is Confidence.high
    assert diag.files_changed == ["requirements.txt"]
    assert "flask==3.0.3" in diag.patch
    assert diag.is_patch_free() is False


def test_parse_diagnosis_fenced() -> None:
    diag = parse_diagnosis(f"```json\n{VALID_JSON}\n```")
    assert diag.root_cause.startswith("flask")


def test_parse_diagnosis_with_surrounding_prose() -> None:
    diag = parse_diagnosis(f"Sure, here you go:\n{VALID_JSON}\nHope that helps!")
    assert diag.confidence is Confidence.high


def test_parse_diagnosis_normalises_confidence_and_files() -> None:
    diag = parse_diagnosis(
        '{"root_cause":"x","explanation":"y","confidence":"HIGH",'
        '"files_changed":"a.py"}'
    )
    assert diag.confidence is Confidence.high
    assert diag.files_changed == ["a.py"]
    assert diag.patch == ""
    assert diag.is_patch_free() is True


def test_parse_diagnosis_invalid_raises() -> None:
    with pytest.raises(ValueError):
        parse_diagnosis("not json at all")
    with pytest.raises(ValueError):
        parse_diagnosis('{"root_cause": "missing required fields"}')


def test_build_user_prompt_contains_context() -> None:
    prompt = build_user_prompt(_context())
    assert "pip install -r requirements.txt" in prompt
    assert "dependency" in prompt
    assert "ERROR: no matching distribution" in prompt


def test_build_user_prompt_retry_mentions_previous_failure() -> None:
    context = _context()
    attempt = Attempt(
        index=1,
        diagnosis=parse_diagnosis(VALID_JSON),
        verification=VerificationResult(
            passed=False, command="pytest", output_tail="AssertionError: still broken"
        ),
    )
    prompt = build_user_prompt(context, attempt)
    assert "PREVIOUS ATTEMPT FAILED" in prompt
    assert "still broken" in prompt


class _Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]


class _Messages:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts
        self.calls = 0

    def create(self, **_: object) -> _Response:
        text = self._texts[min(self.calls, len(self._texts) - 1)]
        self.calls += 1
        return _Response(text)


class _FakeAnthropic:
    def __init__(self, texts: list[str]) -> None:
        self.messages = _Messages(texts)


def test_llm_client_parses_first_response() -> None:
    fake = _FakeAnthropic([VALID_JSON])
    client = LLMClient(api_key="k", model="m", client=fake)
    diag = client.diagnose(_context())
    assert diag.confidence is Confidence.high
    assert fake.messages.calls == 1


def test_llm_client_retries_once_on_invalid_json() -> None:
    fake = _FakeAnthropic(["I cannot do that", VALID_JSON])
    client = LLMClient(api_key="k", model="m", client=fake)
    diag = client.diagnose(_context())
    assert diag.root_cause.startswith("flask")
    assert fake.messages.calls == 2


def test_llm_client_raises_after_retries_exhausted() -> None:
    fake = _FakeAnthropic(["nope", "still nope"])
    client = LLMClient(api_key="k", model="m", client=fake)
    with pytest.raises(ValueError):
        client.diagnose(_context())
