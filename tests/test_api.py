"""Integration tests for the FastAPI webhook endpoint."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.db import RunStore

SECRET = "hook-secret"


def _payload(branch: str = "feature/x", action: str = "completed", conclusion: str = "failure") -> dict:
    return {
        "action": action,
        "workflow_run": {
            "id": 555,
            "conclusion": conclusion,
            "head_branch": branch,
            "head_sha": "abc123",
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "pull_requests": [{"number": 7}],
        },
        "repository": {"full_name": "o/r"},
        "installation": {"id": 99},
    }


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = Settings(github_webhook_secret=SECRET, database_url=f"sqlite:///{tmp_path}/t.db")
    store = RunStore(settings.sqlite_path)
    store.init()
    processed: list[tuple] = []
    monkeypatch.setattr(main_module, "_settings", settings)
    monkeypatch.setattr(main_module, "_store", store)
    monkeypatch.setattr(main_module, "process_run", lambda run, path: processed.append((run, path)))
    return TestClient(main_module.app), store, processed


def _post(client: TestClient, payload: dict, secret: str = SECRET, event: str = "workflow_run"):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "x-hub-signature-256": signature,
            "x-github-event": event,
            "content-type": "application/json",
        },
    )


def test_health(api) -> None:
    client, _store, _processed = api
    assert client.get("/health").json() == {"status": "ok"}


def test_valid_failure_is_accepted_and_processed(api) -> None:
    client, store, processed = api
    resp = _post(client, _payload())
    assert resp.status_code == 202
    assert resp.json() == {"status": "accepted", "run_id": 555}
    assert len(processed) == 1
    assert processed[0][0].run_id == 555
    assert processed[0][1] == ".github/workflows/ci.yml"
    # The run was claimed before the background task ran.
    assert store.already_processed(555) is True


def test_invalid_signature_is_rejected(api) -> None:
    client, _store, processed = api
    resp = _post(client, _payload(), secret="wrong-secret")
    assert resp.status_code == 401
    assert processed == []


def test_missing_signature_is_rejected(api) -> None:
    client, _store, _processed = api
    resp = client.post("/webhook", content=b"{}", headers={"x-github-event": "workflow_run"})
    assert resp.status_code == 401


def test_non_workflow_run_event_is_ignored(api) -> None:
    client, _store, processed = api
    resp = _post(client, _payload(), event="push")
    assert resp.status_code == 202
    assert resp.json()["status"] == "ignored"
    assert processed == []


def test_successful_run_is_ignored(api) -> None:
    client, _store, processed = api
    resp = _post(client, _payload(conclusion="success"))
    assert resp.json()["status"] == "ignored"
    assert processed == []


def test_fixci_branch_is_ignored(api) -> None:
    client, _store, processed = api
    resp = _post(client, _payload(branch="fixci/fix-555"))
    assert resp.json()["status"] == "ignored"
    assert "loop prevention" in resp.json()["reason"]
    assert processed == []


def test_duplicate_delivery_is_deduped(api) -> None:
    client, _store, processed = api
    first = _post(client, _payload())
    second = _post(client, _payload())
    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"
    assert len(processed) == 1
