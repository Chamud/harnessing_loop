"""The scheduler: keeps up to `max_active` worker processes running.

- polls the job table, claims queued jobs in FIFO order
- starts `python -m server.worker <id> <db>` per job, logging to the workspace
- marks a job failed if its worker exits while the job still says running
- marks stale jobs (no heartbeat) failed, so a killed worker does not block a slot
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

from .jobs import JobDB

ROOT = Path(__file__).resolve().parents[1]


class Scheduler:
    def __init__(self, db: JobDB, *, max_active: int = 3, poll_s: float = 1.0, stale_s: float = 120.0):
        self.db = db
        self.max_active = max_active
        self.poll_s = poll_s
        self.stale_s = stale_s
        self._procs: dict[str, subprocess.Popen] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._reap()
                self._launch()
            except Exception as exc:  # noqa: BLE001 - keep scheduling
                print(f"[scheduler] {exc}", file=sys.stderr)
            self._stop.wait(self.poll_s)

    def _reap(self) -> None:
        for jid, proc in list(self._procs.items()):
            code = proc.poll()
            if code is None:
                continue
            del self._procs[jid]
            self.db.fail_if_active(jid, f"worker exited with code {code} before recording a result")
        for job in self.db.stale(self.stale_s):
            if job["id"] not in self._procs:
                self.db.fail_if_active(job["id"], "no heartbeat; worker presumed dead")

    def _launch(self) -> None:
        while len(self._procs) < self.max_active:
            job = self.db.claim_next()
            if job is None:
                return
            ws = Path(job["workspace"])
            (ws / ".harness").mkdir(parents=True, exist_ok=True)
            log = (ws / ".harness" / "worker.log").open("a", encoding="utf-8")
            cmd = [sys.executable, "-m", "server.worker", job["id"], str(self.db.path)]
            proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
            self._procs[job["id"]] = proc

    def active_count(self) -> int:
        return len(self._procs)
