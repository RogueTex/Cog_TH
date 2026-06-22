"""SQLite-backed persistence for automation runs.

A "run" links a GitHub issue to the Devin session (and PR) that remediates it.
The store is intentionally small and synchronous; SQLite is plenty for a
single-instance demo automation.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                TEXT PRIMARY KEY,
    mode              TEXT NOT NULL,            -- 'real' | 'adopt'
    repo              TEXT NOT NULL,
    issue_number      INTEGER NOT NULL,
    issue_url         TEXT,
    issue_title       TEXT,
    devin_session_id  TEXT,
    devin_session_url TEXT,
    pull_request_url  TEXT,
    pr_state          TEXT,
    status            TEXT NOT NULL,            -- new|running|exit|error|adopted|...
    structured_output TEXT,                     -- JSON blob from Devin, if any
    detail            TEXT,                     -- free-form notes / last poll detail
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
"""

# Columns that callers may update via update_run.
_UPDATABLE = {
    "issue_url",
    "issue_title",
    "devin_session_id",
    "devin_session_url",
    "pull_request_url",
    "pr_state",
    "status",
    "structured_output",
    "detail",
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Store:
    """Thin wrapper around a SQLite database of automation runs."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        if db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(db_path))
            os.makedirs(parent, exist_ok=True)
        # check_same_thread=False so FastAPI's threadpool workers can share it.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- queries -------------------------------------------------------

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    def list_runs(self) -> list[dict[str, Any]]:
        cur = self._conn.execute("SELECT * FROM runs ORDER BY created_at DESC, id DESC")
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def find_active_real_run(self, repo: str, issue_number: int) -> dict[str, Any] | None:
        """Return an existing real-mode run for this issue, if one exists.

        Used for idempotency: we do not spin up a second Devin session for an
        issue that already has one unless the caller forces it.
        """
        cur = self._conn.execute(
            "SELECT * FROM runs WHERE repo = ? AND issue_number = ? AND mode = 'real' "
            "ORDER BY created_at DESC LIMIT 1",
            (repo, issue_number),
        )
        row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    # --- mutations -----------------------------------------------------

    def create_run(
        self,
        *,
        mode: str,
        repo: str,
        issue_number: int,
        status: str,
        issue_url: str | None = None,
        issue_title: str | None = None,
        devin_session_id: str | None = None,
        devin_session_url: str | None = None,
        pull_request_url: str | None = None,
        pr_state: str | None = None,
        structured_output: dict[str, Any] | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        run_id = uuid.uuid4().hex
        now = _now()
        self._conn.execute(
            """
            INSERT INTO runs (
                id, mode, repo, issue_number, issue_url, issue_title,
                devin_session_id, devin_session_url, pull_request_url, pr_state,
                status, structured_output, detail, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                mode,
                repo,
                issue_number,
                issue_url,
                issue_title,
                devin_session_id,
                devin_session_url,
                pull_request_url,
                pr_state,
                status,
                json.dumps(structured_output) if structured_output is not None else None,
                detail,
                now,
                now,
            ),
        )
        self._conn.commit()
        run = self.get_run(run_id)
        assert run is not None
        return run

    def update_run(self, run_id: str, **fields: Any) -> dict[str, Any] | None:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"Cannot update unknown columns: {sorted(unknown)}")
        if not fields:
            return self.get_run(run_id)

        if "structured_output" in fields and fields["structured_output"] is not None:
            value = fields["structured_output"]
            if not isinstance(value, str):
                fields["structured_output"] = json.dumps(value)

        assignments = ", ".join(f"{col} = ?" for col in fields)
        values = list(fields.values())
        values.append(_now())  # updated_at
        values.append(run_id)
        self._conn.execute(
            f"UPDATE runs SET {assignments}, updated_at = ? WHERE id = ?", values
        )
        self._conn.commit()
        return self.get_run(run_id)

    # --- helpers -------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        raw = data.get("structured_output")
        if raw:
            try:
                data["structured_output"] = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                pass
        return data
