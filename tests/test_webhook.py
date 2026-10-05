"""Tests for webhook signature verification and event filtering."""

from __future__ import annotations

import hashlib
import hmac
import json

from app.webhook import (
    extract_workflow_path,
    extract_pr_number,
    is_failed_workflow_run,
    is_fixci_branch,
    parse_workflow_run,
    verify_signature,
)

SECRET = "s3cr3t"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_verify_signature_accepts_valid() -> None:
    body = b'{"hello":"world"}'
    assert verify_signature(SECRET, body, _sign(body)) is True


def test_verify_signature_rejects_tampered_body() -> None:
    body = b'{"hello":"world"}'
    signature = _sign(body)
    assert verify_signature(SECRET, b'{"hello":"tampered"}', signature) is False


def test_verify_signature_rejects_wrong_secret() -> None:
    body = b"{}"
    good = _sign(body)
    assert verify_signature("other-secret", body, good) is False


def test_verify_signature_rejects_missing_or_malformed_header() -> None:
    body = b"{}"
    assert verify_signature(SECRET, body, None) is False
    assert verify_signature(SECRET, body, "") is False
    assert verify_signature(SECRET, body, "md5=deadbeef") is False
    assert verify_signature(SECRET, body, "sha256=nothex") is False


def test_verify_signature_empty_secret_is_false() -> None:
    assert verify_signature("", b"{}", "sha256=abc") is False


def _payload(**overrides: object) -> dict:
    payload = {
        "action": "completed",
        "workflow_run": {
            "id": 123,
            "conclusion": "failure",
            "head_branch": "feature/x",
            "head_sha": "abc123",
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "html_url": "https://github.com/o/r/actions/runs/123",
            "pull_requests": [{"number": 7}],
        },
        "repository": {"full_name": "o/r"},
        "installation": {"id": 99},
    }
    payload.update(overrides)
    return payload


def test_is_failed_workflow_run() -> None:
    assert is_failed_workflow_run(_payload()) is True
    assert is_failed_workflow_run(_payload(action="requested")) is False
    no_conclusion = _payload()
    no_conclusion["workflow_run"].pop("conclusion")
    assert is_failed_workflow_run(no_conclusion) is False
    success = _payload()
    success["workflow_run"]["conclusion"] = "success"
    assert is_failed_workflow_run(success) is False


def test_is_fixci_branch() -> None:
    assert is_fixci_branch("fixci/fix-123") is True
    assert is_fixci_branch("main") is False
    assert is_fixci_branch(None) is False


def test_extract_pr_number() -> None:
    assert extract_pr_number({"pull_requests": [{"number": 7}]}) == 7
    assert extract_pr_number({"pull_requests": []}) is None
    assert extract_pr_number({}) is None


def test_parse_workflow_run() -> None:
    run = parse_workflow_run(_payload())
    assert run is not None
    assert run.run_id == 123
    assert run.repo_full_name == "o/r"
    assert run.installation_id == 99
    assert run.pr_number == 7
    assert run.head_branch == "feature/x"


def test_parse_workflow_run_missing_ids_returns_none() -> None:
    payload = _payload()
    payload["workflow_run"]["id"] = None
    assert parse_workflow_run(payload) is None
    payload2 = _payload()
    payload2["repository"] = {}
    assert parse_workflow_run(payload2) is None


def test_extract_workflow_path() -> None:
    assert extract_workflow_path(_payload()) == ".github/workflows/ci.yml"
    assert extract_workflow_path({"workflow_run": {}}) is None


def test_signature_matches_json_encoding() -> None:
    """The exact bytes posted must be signed (FastAPI reads raw body)."""
    body = json.dumps(_payload()).encode()
    assert verify_signature(SECRET, body, _sign(body)) is True
