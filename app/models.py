"""Pydantic v2 models shared across the pipeline."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class FailureType(str, Enum):
    """Rule-based classification of a CI failure."""

    dependency = "dependency"
    lint = "lint"
    test_failure = "test_failure"
    unsupported = "unsupported"


class Confidence(str, Enum):
    """LLM self-reported confidence."""

    low = "low"
    medium = "medium"
    high = "high"


class WorkflowRunRef(BaseModel):
    """Identifies the failed workflow run that triggered FixCI."""

    run_id: int
    repo_full_name: str
    installation_id: int
    head_branch: str
    head_sha: str
    pr_number: int | None = None
    workflow_name: str | None = None
    run_url: str | None = None


class FailureContext(BaseModel):
    """Everything the LLM needs to diagnose a failure."""

    run: WorkflowRunRef
    trimmed_log: str
    failure_type: FailureType
    failing_command: str | None = None
    diff: str = ""
    workflow_yaml: str = ""
    # Contents of files referenced in the traceback (path -> text), size-capped.
    files: dict[str, str] = Field(default_factory=dict)


class Diagnosis(BaseModel):
    """Structured LLM response describing a fix."""

    root_cause: str
    explanation: str
    confidence: Confidence
    files_changed: list[str] = Field(default_factory=list)
    patch: str = ""

    def is_patch_free(self) -> bool:
        """True when the LLM produced no patch (diagnosis-only)."""
        return not self.patch.strip()


class VerificationResult(BaseModel):
    """Outcome of re-running the failing command in the Docker sandbox."""

    passed: bool
    command: str
    output_tail: str = ""
    duration_s: float = 0.0
    error: str | None = None
    # Final working-tree contents of the files the patch touched. Used to
    # commit the verified change through the GitHub Contents API without ever
    # needing host git credentials.
    patched_files: dict[str, str] = Field(default_factory=dict)


class Attempt(BaseModel):
    """A single diagnosis + verification attempt in the retry loop."""

    index: int
    diagnosis: Diagnosis | None = None
    verification: VerificationResult | None = None
    error: str | None = None


class RunStatus(str, Enum):
    """Terminal state of a FixCI run, persisted to SQLite."""

    received = "received"
    diagnosing = "diagnosing"
    fixing = "fixing"
    pr_opened = "pr_opened"
    diagnosis_only = "diagnosis_only"
    failed = "failed"
    skipped = "skipped"


class RunRecord(BaseModel):
    """Persisted history row for one workflow run."""

    run_id: int
    repo_full_name: str
    status: RunStatus
    attempts: int = 0
    failure_type: FailureType | None = None
    root_cause: str | None = None
    pr_url: str | None = None
    detail: str | None = None
