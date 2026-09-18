# Building an application

How a runtime becomes a service: jobs, worker processes, control, event streaming, and the pieces you change for your own domain.

## The shape

```
client  ──POST /jobs──▶  server/app.py  ──▶  jobs.sqlite (queued)
                              │
                     server/scheduler.py     claims FIFO, ≤ max_active
                              │
                     server/worker.py        one process per job
                              │
                   Profile.build → Loop.run  writes .harness/ in the job workspace
                              │
client  ◀─GET /jobs/{id}/events─  server tails .harness/events.jsonl
client  ──POST /jobs/{id}/pause─▶ writes .harness/control.json
```

The library never imports `server/`. Everything the service needs from a run is already on disk: the event log, the transcript, the control file, the state file. The server reads and writes those files. It does not hold a reference to a running loop.

## Run it

```
python -m server.app --root ./jobs --port 8765 --max-active 3
```

Open `http://127.0.0.1:8765/`. Submit a prompt with a profile. Watch the event stream. Pause, resume, cancel, or send a message to the running agent.

Or from a script:

```python
import json, urllib.request
req = urllib.request.Request(
    "http://127.0.0.1:8765/jobs",
    data=json.dumps({"profile": "coder", "prompt": "add tests for cli.py", "model": "fake"}).encode(),
    headers={"Content-Type": "application/json"},
)
print(json.load(urllib.request.urlopen(req)))
```

## The parts

| File | Role | What to know |
|---|---|---|
| `server/jobs.py` | `JobDB`, a SQLite table | `claim_next()` is atomic (`BEGIN IMMEDIATE`), so several schedulers can share one database. Statuses: `queued`, `running`, `paused`, `done`, `failed`, `cancelled`. |
| `server/scheduler.py` | `Scheduler` thread | Claims jobs in FIFO order, starts `python -m server.worker <id> <db>`, marks a job failed when its process exits without recording a result or when the heartbeat goes stale. |
| `server/worker.py` | `run_job()` | Builds the runtime from the job's profile, subscribes to events for heartbeats and pause/resume mirroring, records the terminal reason, final text, turns and cost. A crash is recorded as `failed` with the traceback. |
| `server/app.py` | HTTP API and SSE | Standard library only. `GET /jobs/{id}/events` tails the workspace event log and ends with an `end` event once the job is terminal. Control actions write the control file; the loop picks them up on its next turn. |
| `server/page.html` | One page | Lists jobs, streams one job's events, exposes the control buttons. |

## Job workspace

Each job gets `<root>/<job_id>/` unless the request names a workspace. Inside it the run creates `.harness/` with:

```
transcript.jsonl   the conversation, resumable
events.jsonl       what the UI streams
state.json         phase, evidence, files, todos
control.json       cancel / pause / inbox
notes.md, todo.json
tool-results/      spilled large outputs
checkpoints/       pre-edit copies of files
worker.log         stdout and stderr of the worker process
```

To continue a finished or failed job, submit a job with `"mode": "resume"` and the same `workspace`. The worker calls `Loop.resume`, which loads the transcript from the last compaction boundary and the saved state.

## Making it yours

An application is a profile plus a verifier. The demo in `examples/app_demo/` shows the whole pattern in five files:

1. **`profile.yaml`**: phases, entry gates, finish gates, permissions, sandbox, hooks. Start from `harnessing_loop/profiles/template_app.yaml`, which lists every key.
2. **`prompt.md`**: the method, in the model's terms. Name the phases, the output paths, and the rule that numbers come from code, not memory.
3. **A verifier** (`workspace/verify.py`): an independent program that recomputes or checks the outputs and writes `verify/verify.json` with `ok: true`. The finish gate uses `fresh_file` and `json_field` on that file. A verifier that trusts the agent's own claims is not a gate.
4. **Hooks** where a rule cannot live in a permission pattern. The demo blocks destructive shell commands with a ten-line script.
5. **Evidence** for phase inference: `phase_evidence` maps evidence keys such as `file_written` to phases, so progress is inferred from what happened even if the model forgets to declare it.

Custom gates that cannot be expressed in YAML are plain callables `(state, ctx) -> str | None`, attached through `Profile.extra_gates` or by building `Gates` yourself. Custom tools are `Tool` subclasses attached through `Profile.extra_tools`, or a new pack registered in `harnessing_loop/tools/packs/__init__.py`.

## Operating it

- **Isolation**: switch `sandbox.backend` to `docker` before running jobs from anyone but yourself. The startup check refuses exec tools on the local backend unless the profile says `allow_unsafe_local: true`.
- **Cost**: `limits.max_cost_usd` stops a runaway job. The job row carries the final cost.
- **Concurrency**: `--max-active` caps worker processes. Each worker is a full process, so memory scales with it.
- **Recovery**: a worker killed mid-run leaves a transcript ending in a `tool_use` without a result. Resume repairs that with a synthetic error result and continues.
- **Observability**: the event log is the single source. Anything that needs to know what a job is doing tails it, including the page, tests, and your own dashboards.

## Run this

```
python -m pytest -q
python examples/app_demo/run.py
python -m server.app --root ./jobs
```

Next: `evals/run.py` to measure a profile against a task set before changing prompts or gates.
