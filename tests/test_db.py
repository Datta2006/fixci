"""Tests for the SQLite run-history store."""

from __future__ import annotations

from pathlib import Path

from app.db import RunStore
from app.models import FailureType, RunRecord, RunStatus


def _store(tmp_path: Path) -> RunStore:
    store = RunStore(tmp_path / "fixci.db")
    store.init()
    return store


def test_claim_is_atomic_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.claim(1, "o/r") is True
    assert store.claim(1, "o/r") is False


def test_is_terminal_false_until_finished(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.claim(1, "o/r")
    assert store.is_terminal(1) is False
    store.upsert(RunRecord(run_id=1, repo_full_name="o/r", status=RunStatus.pr_opened))
    assert store.is_terminal(1) is True


def test_upsert_and_get_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = RunRecord(
        run_id=42,
        repo_full_name="o/r",
        status=RunStatus.diagnosis_only,
        attempts=3,
        failure_type=FailureType.test_failure,
        root_cause="off by one",
        detail="unverified",
    )
    store.upsert(record)
    loaded = store.get(42)
    assert loaded is not None
    assert loaded.status is RunStatus.diagnosis_only
    assert loaded.attempts == 3
    assert loaded.failure_type is FailureType.test_failure
    assert loaded.root_cause == "off by one"


def test_upsert_updates_existing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.claim(5, "o/r")
    store.upsert(RunRecord(run_id=5, repo_full_name="o/r", status=RunStatus.fixing, attempts=1))
    store.upsert(
        RunRecord(
            run_id=5,
            repo_full_name="o/r",
            status=RunStatus.pr_opened,
            attempts=2,
            pr_url="https://example.com/pr/1",
        )
    )
    loaded = store.get(5)
    assert loaded is not None
    assert loaded.status is RunStatus.pr_opened
    assert loaded.pr_url == "https://example.com/pr/1"


def test_recent_orders_by_update(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(RunRecord(run_id=1, repo_full_name="o/r", status=RunStatus.failed))
    store.upsert(RunRecord(run_id=2, repo_full_name="o/r", status=RunStatus.pr_opened))
    recent = store.recent()
    assert {r.run_id for r in recent} == {1, 2}
