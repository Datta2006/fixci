"""Webhook signature verification and event filtering."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from app.models import WorkflowRunRef

FIXCI_BRANCH_PREFIX = "fixci/"
TERMINAL_STATUSES = ("pr_opened", "diagnosis_only", "failed", "skipped")


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Verify the X-Hub-Signature-256 HMAC of a webhook payload.

    Uses a constant-time comparison and returns False for any malformed input.
    """
    if not secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


def is_failed_workflow_run(payload: dict[str, Any]) -> bool:
    """True for `workflow_run` events that completed with a failure."""
    if payload.get("action") != "completed":
        return False
    run = payload.get("workflow_run") or {}
    return run.get("conclusion") == "failure"


def is_fixci_branch(branch: str | None) -> bool:
    """True when the branch was created by FixCI (loop prevention)."""
    return bool(branch) and branch.startswith(FIXCI_BRANCH_PREFIX)


def extract_pr_number(workflow_run: dict[str, Any]) -> int | None:
    """Return the PR number associated with a workflow run, if any."""
    prs = workflow_run.get("pull_requests") or []
    for pr in prs:
        number = pr.get("number")
        if isinstance(number, int):
            return number
    return None


def parse_workflow_run(payload: dict[str, Any]) -> WorkflowRunRef | None:
    """Build a ``WorkflowRunRef`` from a webhook payload.

    Returns None when required identifiers are missing.
    """
    run = payload.get("workflow_run") or {}
    repo = payload.get("repository") or {}
    installation = payload.get("installation") or {}
    run_id = run.get("id")
    if not run_id or not repo.get("full_name"):
        return None
    return WorkflowRunRef(
        run_id=int(run_id),
        repo_full_name=repo.get("full_name", ""),
        installation_id=int(installation.get("id") or 0),
        head_branch=run.get("head_branch") or "",
        head_sha=run.get("head_sha") or "",
        pr_number=extract_pr_number(run),
        workflow_name=run.get("name"),
        run_url=run.get("html_url"),
    )


def extract_workflow_path(payload: dict[str, Any]) -> str | None:
    """Return the workflow file path from a webhook payload."""
    path = (payload.get("workflow_run") or {}).get("path")
    return path or None
