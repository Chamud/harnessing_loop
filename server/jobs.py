"""The job table.

Status machine:

    queued -> running -> done | failed | cancelled
                 |  ^
                 v  |
               paused

`claim_next` is atomic, so several schedulers can share one database.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

STATUSES = ("queued", "running", "paused", "done", "failed", "cancelled")
ACTIVE = ("running", "paused")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    prompt TEXT NOT NULL,
    workspace TEXT NOT NULL,
    model TEXT,
    mode TEXT NOT NULL DEFAULT 'build',
    status TEXT NOT NULL DEFAULT 'queued',
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    heartbeat REAL,
    pid INTEGER,
    reason TEXT,
    final_text TEXT,
    cost_usd REAL DEFAULT 0,
    turns INTEGER DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created_at);
"""


class JobDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
        finally:
            conn.close()

    # ---- writes -------------------------------------------------------------------
    def create(self, *, profile: str, prompt: str, workspace: Path, model: str | None = None, mode: str = "build") -> dict[str, Any]:
        jid = uuid.uuid4().hex[:12]
        with self._conn() as c:
            c.execute(
                "INSERT INTO jobs (id, profile, prompt, workspace, model, mode, status, created_at) VALUES (?,?,?,?,?,?,'queued',?)",
                (jid, profile, prompt, str(workspace), model, mode, time.time()),
            )
        return self.get(jid)

    def claim_next(self) -> dict[str, Any] | None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                c.execute("COMMIT")
                return None
            c.execute("UPDATE jobs SET status='running', started_at=?, heartbeat=? WHERE id=?", (time.time(), time.time(), row["id"]))
            c.execute("COMMIT")
            return self.get(row["id"])

    def set(self, jid: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), jid))

    def heartbeat(self, jid: str) -> None:
        self.set(jid, heartbeat=time.time())

    def finish(self, jid: str, *, status: str, reason: str, final_text: str, cost_usd: float, turns: int, error: str | None = None) -> None:
        self.set(jid, status=status, reason=reason, final_text=final_text, cost_usd=cost_usd, turns=turns, error=error, finished_at=time.time())

    def fail_if_active(self, jid: str, error: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=? AND status IN ('running','paused')", (error, time.time(), jid))

    # ---- reads --------------------------------------------------------------------------
    def get(self, jid: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        return dict(row) if row else None

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def active(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM jobs WHERE status IN ('running','paused')").fetchall()
        return [dict(r) for r in rows]

    def stale(self, older_than_s: float) -> list[dict[str, Any]]:
        cutoff = time.time() - older_than_s
        with self._conn() as c:
            rows = c.execute("SELECT * FROM jobs WHERE status IN ('running','paused') AND (heartbeat IS NULL OR heartbeat < ?)", (cutoff,)).fetchall()
        return [dict(r) for r in rows]


def to_json(job: dict[str, Any] | None) -> str:
    return json.dumps(job, default=str)
