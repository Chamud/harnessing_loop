"""One job, one process.

    python -m server.worker <job_id> <db_path>

The worker builds the runtime from the job's profile, runs the loop, keeps
a heartbeat in the job table, mirrors pause/resume into the job status,
and records the terminal result. Events go to the workspace event log,
which the API streams to clients.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harnessing_loop.core import events as ev  # noqa: E402
from harnessing_loop.core.loop import Loop  # noqa: E402
from harnessing_loop.profiles.base import load_profile  # noqa: E402
from server.jobs import JobDB  # noqa: E402

HEARTBEAT_EVERY_S = 5.0


def run_job(job_id: str, db_path: Path) -> int:
    db = JobDB(db_path)
    job = db.get(job_id)
    if job is None:
        print(f"no job {job_id}", file=sys.stderr)
        return 2
    db.set(job_id, pid=os.getpid())
    last_beat = {"t": 0.0}

    def on_event(e) -> None:
        now = time.time()
        if now - last_beat["t"] > HEARTBEAT_EVERY_S:
            db.heartbeat(job_id)
            last_beat["t"] = now
        if e.type == ev.CONTROL:
            state = e.data.get("state")
            if state == "paused":
                db.set(job_id, status="paused")
            elif state == "resumed":
                db.set(job_id, status="running")

    try:
        profile = load_profile(job["profile"])
        runtime = profile.build(Path(job["workspace"]), model=job.get("model") or None, ask_handler=None)
        runtime.events.subscribe(on_event)
        if job["mode"] == "resume":
            result = Loop.resume(runtime).run(None)
        else:
            result = Loop(runtime).run(job["prompt"])
        status = "done" if result.reason == "completed" else ("cancelled" if result.reason == "aborted" else "failed")
        cost = _last_cost(Path(job["workspace"]))
        db.finish(job_id, status=status, reason=result.reason, final_text=result.final_text, cost_usd=cost, turns=result.turns, error=None if status == "done" else result.message)
        return 0 if status == "done" else 1
    except Exception as exc:  # noqa: BLE001
        db.finish(job_id, status="failed", reason="crash", final_text="", cost_usd=0.0, turns=0, error=f"{exc}\n{traceback.format_exc(limit=5)}")
        return 1


def _last_cost(workspace: Path) -> float:
    """Read the last cost event from the workspace event log."""
    import json

    p = workspace / ".harness" / "events.jsonl"
    cost = 0.0
    if not p.exists():
        return cost
    try:
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "run_end":
                    cost = float(rec.get("data", {}).get("cost", cost))
    except OSError:
        pass
    return cost


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python -m server.worker <job_id> <db_path>", file=sys.stderr)
        sys.exit(2)
    sys.exit(run_job(sys.argv[1], Path(sys.argv[2])))
