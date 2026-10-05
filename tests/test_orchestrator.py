"""Tests for the orchestrator retry loop and safety guarantees."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.db import RunStore
from app.models import (
    Diagnosis,
    FailureContext,
    FailureType,
    RunRecord,
    RunStatus,
    VerificationResult,
    WorkflowRunRef,
)
from app.orchestrator import (
    Orchestrator,
    build_diagnosis_comment,
    build_pr_body,
    build_pr_title,
)

PATCH = (
    "--- a/requirements.txt\n"
    "+++ b/requirements.txt\n"
    "@@ -1 +1 @@\n"
    "-flask==0.0.1\n"
    "+flask==3.0.3\n"
)
BAD_PATCH = (
    "--- a/.github/workflows/ci.yml\n"
    "+++ b/.github/workflows/ci.yml\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)


def _run() -> WorkflowRunRef:
    return WorkflowRunRef(
        run_id=123,
        repo_full_name="o/r",
        installation_id=1,
        head_branch="feature/x",
        head_sha="abc",
        pr_number=7,
    )


def _context(failure_type: FailureType = FailureType.dependency) -> FailureContext:
    return FailureContext(
        run=_run(),
        trimmed_log="ERROR: no matching distribution",
        failure_type=failure_type,
        failing_command="pip install -r requirements.txt",
    )


def _diagnosis(patch: str = PATCH) -> Diagnosis:
    return Diagnosis(
        root_cause="flask 0.0.1 does not exist",
        explanation="Pin an existing release.",
        confidence="high",
        files_changed=["requirements.txt"],
        patch=patch,
    )


class FakeClient:
    def __init__(self, context: FailureContext) -> None:
        self.context = context
        self.comments: list[tuple[int, str]] = []
        self.prs: list[dict] = []

    def build_failure_context(self, run: WorkflowRunRef, workflow_path: str | None) -> FailureContext:
        return self.context

    def installation_token(self) -> str:
        return "tok"

    def find_pr_for_branch(self, branch: str) -> int | None:
        return None

    def post_comment(self, number: int, body: str) -> None:
        self.comments.append((number, body))

    def create_fix_pr(self, **kwargs) -> str:
        self.prs.append(kwargs)
        return "https://example.com/o/r/pull/9"


class FakeLLM:
    def __init__(self, diagnoses: list[Diagnosis]) -> None:
        self.diagnoses = diagnoses
        self.calls = 0

    def diagnose(self, context: FailureContext, previous=None) -> Diagnosis:
        diag = self.diagnoses[min(self.calls, len(self.diagnoses) - 1)]
        self.calls += 1
        return diag


class FakeSandbox:
    def __init__(self, results: list[VerificationResult]) -> None:
        self.results = results
        self.calls = 0

    def run(self, context, diagnosis, installation_token: str = "") -> VerificationResult:
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return result


def _passed() -> VerificationResult:
    return VerificationResult(
        passed=True,
        command="pip install -r requirements.txt",
        output_tail="Successfully installed flask-3.0.3",
        patched_files={"requirements.txt": "flask==3.0.3\n"},
    )


def _failed(msg: str = "still failing") -> VerificationResult:
    return VerificationResult(
        passed=False, command="pip install -r requirements.txt", output_tail=msg
    )


def _make(tmp_path: Path, context, llm, sandbox, max_attempts: int = 3):
    store = RunStore(tmp_path / "fixci.db")
    store.init()
    settings = Settings(max_attempts=max_attempts, sandbox_enabled=True)
    client = FakeClient(context)
    orch = Orchestrator(settings, store, client, llm, sandbox)
    return orch, store, client


def test_verified_fix_opens_pr(tmp_path: Path) -> None:
    orch, store, client = _make(tmp_path, _context(), FakeLLM([_diagnosis()]), FakeSandbox([_passed()]))
    record = orch.process(_run())

    assert record.status is RunStatus.pr_opened
    assert record.pr_url == "https://example.com/o/r/pull/9"
    assert len(client.prs) == 1
    assert client.prs[0]["branch"] == "fixci/fix-123"
    assert client.prs[0]["base_branch"] == "feature/x"
    assert client.comments and client.comments[0][0] == 7
    assert "pull/9" in client.comments[0][1]
    assert store.is_terminal(123) is True


def test_no_unverified_patch_is_pushed(tmp_path: Path) -> None:
    llm = FakeLLM([_diagnosis()])
    sandbox = FakeSandbox([_failed(), _failed(), _failed()])
    orch, store, client = _make(tmp_path, _context(), llm, sandbox, max_attempts=3)

    record = orch.process(_run())

    assert record.status is RunStatus.diagnosis_only
    assert client.prs == []
    assert sandbox.calls == 3
    assert "unverified" in client.comments[0][1].lower()
    assert "no patch pushed" in client.comments[0][1].lower()


def test_unsupported_failure_is_diagnosis_only(tmp_path: Path) -> None:
    context = _context(FailureType.unsupported)
    llm = FakeLLM([_diagnosis(patch="")])
    sandbox = FakeSandbox([_passed()])
    orch, _store, client = _make(tmp_path, context, llm, sandbox)

    record = orch.process(_run())

    assert record.status is RunStatus.diagnosis_only
    assert sandbox.calls == 0
    assert client.prs == []
    assert client.comments


def test_patch_free_diagnosis_does_not_run_sandbox(tmp_path: Path) -> None:
    llm = FakeLLM([_diagnosis(patch="")])
    sandbox = FakeSandbox([_passed()])
    orch, _store, client = _make(tmp_path, _context(), llm, sandbox)

    record = orch.process(_run())

    assert record.status is RunStatus.diagnosis_only
    assert sandbox.calls == 0
    assert client.prs == []


def test_rejected_patch_never_reaches_sandbox(tmp_path: Path) -> None:
    llm = FakeLLM([_diagnosis(patch=BAD_PATCH)])
    sandbox = FakeSandbox([_passed()])
    orch, _store, client = _make(tmp_path, _context(), llm, sandbox, max_attempts=1)

    record = orch.process(_run())

    assert record.status is RunStatus.diagnosis_only
    assert sandbox.calls == 0
    assert client.prs == []


def test_retry_succeeds_on_second_attempt(tmp_path: Path) -> None:
    llm = FakeLLM([_diagnosis(), _diagnosis()])
    sandbox = FakeSandbox([_failed(), _passed()])
    orch, _store, client = _make(tmp_path, _context(), llm, sandbox, max_attempts=3)

    record = orch.process(_run())

    assert record.status is RunStatus.pr_opened
    assert record.attempts == 2
    assert sandbox.calls == 2


def test_already_terminal_run_is_skipped(tmp_path: Path) -> None:
    orch, store, client = _make(tmp_path, _context(), FakeLLM([_diagnosis()]), FakeSandbox([_passed()]))
    store.upsert(
        RunRecord(run_id=123, repo_full_name="o/r", status=RunStatus.pr_opened)
    )
    record = orch.process(_run())
    assert record.status is RunStatus.skipped
    assert client.prs == []


def test_pr_title_and_body_include_evidence() -> None:
    context = _context()
    diagnosis = _diagnosis()
    title = build_pr_title(diagnosis)
    body = build_pr_body(context, diagnosis, _passed(), attempts=2)

    assert title.startswith("FixCI: ")
    assert "Root cause" in body
    assert "requirements.txt" in body
    assert "pip install -r requirements.txt" in body
    assert "Successfully installed flask-3.0.3" in body
    assert "Attempts used:** 2" in body
    assert "review" in body.lower()


def test_diagnosis_comment_marked_unverified() -> None:
    comment = build_diagnosis_comment(_context(), _diagnosis(), attempts=3, note="could not verify")
    assert "unverified" in comment.lower()
    assert "could not verify" in comment
