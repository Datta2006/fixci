"""SQLite storage for FixCI run history."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.models import FailureType, RunRecord, RunStatus

TERMINAL_STATUSES = (
    RunStatus.pr_opened.value,
    RunStatus.diagnosis_only.value,
    RunStatus.failed.value,
    RunStatus.skipped.value,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id         INTEGER PRIMARY KEY,
    repo_full_name TEXT NOT NULL,
    status         TEXT NOT NULL,
    attempts       INTEGER NOT NULL DEFAULT 0,
    failure_type   TEXT,
    root_cause     TEXT,
    pr_url         TEXT,
    detail         TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    """Small synchronous store; one connection per operation for thread safety."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        """Create tables if they do not exist."""
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def already_processed(self, run_id: int) -> bool:
        """True when this workflow run has any record (dedupe at the edge)."""
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return row is not None

    def claim(self, run_id: int, repo_full_name: str) -> bool:
        """Atomically claim a run for processing.

        Returns True if this caller created the row, False if it already existed
        (so another webhook delivery got here first).
        """
        now = _utcnow()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO runs
                    (run_id, repo_full_name, status, attempts, created_at, updated_at)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (run_id, repo_full_name, RunStatus.received.value, now, now),
            )
            return cursor.rowcount == 1

    def is_terminal(self, run_id: int) -> bool:
        """True when a run has reached a terminal status."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return False
        return row["status"] in TERMINAL_STATUSES

    def upsert(self, record: RunRecord) -> None:
        """Insert or update a run record."""
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (run_id, repo_full_name, status, attempts, failure_type,
                                  root_cause, pr_url, detail, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    attempts = excluded.attempts,
                    failure_type = excluded.failure_type,
                    root_cause = excluded.root_cause,
                    pr_url = excluded.pr_url,
                    detail = excluded.detail,
                    updated_at = excluded.updated_at
                """,
                (
                    record.run_id,
                    record.repo_full_name,
                    record.status.value,
                    record.attempts,
                    record.failure_type.value if record.failure_type else None,
                    record.root_cause,
                    record.pr_url,
                    record.detail,
                    now,
                    now,
                ),
            )

    def get(self, run_id: int) -> RunRecord | None:
        """Fetch a single run record."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return RunRecord(
            run_id=row["run_id"],
            repo_full_name=row["repo_full_name"],
            status=RunStatus(row["status"]),
            attempts=row["attempts"],
            failure_type=FailureType(row["failure_type"]) if row["failure_type"] else None,
            root_cause=row["root_cause"],
            pr_url=row["pr_url"],
            detail=row["detail"],
        )

    def recent(self, limit: int = 20) -> list[RunRecord]:
        """Return the most recently updated runs."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            RunRecord(
                run_id=r["run_id"],
                repo_full_name=r["repo_full_name"],
                status=RunStatus(r["status"]),
                attempts=r["attempts"],
                failure_type=FailureType(r["failure_type"]) if r["failure_type"] else None,
                root_cause=r["root_cause"],
                pr_url=r["pr_url"],
                detail=r["detail"],
            )
            for r in rows
        ]
