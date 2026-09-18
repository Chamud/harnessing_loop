"""HTTP API and a minimal page. Standard library only.

    python -m server.app --root ./jobs --port 8765 --max-active 3

Endpoints:
    GET  /                      the page
    GET  /jobs                  list jobs
    POST /jobs                  {"profile": "coder", "prompt": "...", "model": "...", "mode": "build|resume"}
    GET  /jobs/{id}             one job
    POST /jobs/{id}/cancel      set the cancel flag in the workspace control file
    POST /jobs/{id}/pause
    POST /jobs/{id}/resume
    POST /jobs/{id}/message     {"text": "..."} -> control inbox
    GET  /jobs/{id}/events      server-sent events tailed from the workspace event log
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harnessing_loop.persistence.control import set_flag  # noqa: E402
from server.jobs import JobDB  # noqa: E402
from server.scheduler import Scheduler  # noqa: E402

PAGE = (Path(__file__).parent / "page.html").read_text(encoding="utf-8")


class State:
    db: JobDB
    root: Path


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter
            pass

        # ---- helpers -----------------------------------------------------------
        def _json(self, code: int, obj) -> None:
            body = json.dumps(obj, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n == 0:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except json.JSONDecodeError:
                return {}

        # ---- routes ----------------------------------------------------------------
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            parts = [p for p in path.split("/") if p]
            if not parts:
                body = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parts == ["jobs"]:
                return self._json(200, state.db.list())
            if len(parts) == 2 and parts[0] == "jobs":
                job = state.db.get(parts[1])
                return self._json(200 if job else 404, job or {"error": "not found"})
            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "events":
                return self._events(parts[1])
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            parts = [p for p in path.split("/") if p]
            if parts == ["jobs"]:
                data = self._body()
                prompt = str(data.get("prompt", "")).strip()
                profile = str(data.get("profile", "chat"))
                if not prompt:
                    return self._json(400, {"error": "prompt is required"})
                ws = Path(data["workspace"]).resolve() if data.get("workspace") else None
                job = state.db.create(profile=profile, prompt=prompt, workspace=ws or state.root / "pending", model=data.get("model"), mode=str(data.get("mode", "build")))
                if ws is None:
                    ws = state.root / job["id"]
                    state.db.set(job["id"], workspace=str(ws))
                    job["workspace"] = str(ws)
                ws.mkdir(parents=True, exist_ok=True)
                return self._json(201, job)
            if len(parts) == 3 and parts[0] == "jobs":
                job = state.db.get(parts[1])
                if job is None:
                    return self._json(404, {"error": "not found"})
                ws = Path(job["workspace"])
                action = parts[2]
                if action == "cancel":
                    set_flag(ws, cancel=True)
                    if job["status"] == "queued":
                        state.db.set(job["id"], status="cancelled", finished_at=time.time(), reason="cancelled before start")
                elif action == "pause":
                    set_flag(ws, pause=True)
                elif action == "resume":
                    set_flag(ws, pause=False)
                elif action == "message":
                    text = str(self._body().get("text", "")).strip()
                    if not text:
                        return self._json(400, {"error": "text is required"})
                    set_flag(ws, inbox=[text])
                else:
                    return self._json(404, {"error": "unknown action"})
                return self._json(200, state.db.get(job["id"]))
            self._json(404, {"error": "not found"})

        def _events(self, jid: str) -> None:
            job = state.db.get(jid)
            if job is None:
                return self._json(404, {"error": "not found"})
            log = Path(job["workspace"]) / ".harness" / "events.jsonl"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            offset = 0
            idle = 0.0
            try:
                while True:
                    if log.exists():
                        with log.open("r", encoding="utf-8") as fh:
                            fh.seek(offset)
                            chunk = fh.read()
                            offset = fh.tell()
                        for line in chunk.splitlines():
                            if line.strip():
                                self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                                idle = 0.0
                        self.wfile.flush()
                    job = state.db.get(jid)
                    if job and job["status"] in ("done", "failed", "cancelled") and offset >= (log.stat().st_size if log.exists() else 0):
                        self.wfile.write(f"event: end\ndata: {json.dumps(job, default=str)}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        return
                    time.sleep(0.5)
                    idle += 0.5
                    if idle >= 15:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        idle = 0.0
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="./jobs")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--max-active", type=int, default=3)
    args = ap.parse_args(argv)
    state = State()
    state.root = Path(args.root).resolve()
    state.root.mkdir(parents=True, exist_ok=True)
    state.db = JobDB(state.root / "jobs.sqlite")
    sched = Scheduler(state.db, max_active=args.max_active)
    sched.start()
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    print(f"serving on http://{args.host}:{args.port}  root={state.root}  max_active={args.max_active}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        sched.stop()
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
