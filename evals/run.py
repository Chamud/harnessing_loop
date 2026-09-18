"""Run a profile over a set of tasks and score the results.

    python evals/run.py --model fake                 # tasks that carry a script run offline
    python evals/run.py --model <model-id> --task greet

A task is a YAML file in evals/tasks/:

    name: greet
    profile: chat
    prompt: "Say hello to the user by name; the name is in name.txt"
    files: {name.txt: "Ada"}
    checks:
      - final_contains: "Ada"
      - file_exists: out.txt
      - file_contains: {path: out.txt, text: "hello"}
      - json_field: {path: out/summary.json, key: ok, expected: true}
    max_cost_usd: 0.5
    script:                       # optional: turns for the fake model
      - {tool: read_file, input: {path: name.txt}}
      - "Hello, Ada."

Results go to evals/results/<timestamp>.json and a table is printed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harnessing_loop import Loop, load_profile  # noqa: E402
from harnessing_loop.llm.fake import FakeModel, tool  # noqa: E402

TASKS_DIR = Path(__file__).parent / "tasks"
RESULTS_DIR = Path(__file__).parent / "results"


def script_to_turns(script: list) -> list:
    turns = []
    for step in script:
        if isinstance(step, str):
            turns.append(step)
        elif isinstance(step, dict) and "tool" in step:
            turns.append([tool(step["tool"], **(step.get("input") or {}))])
        elif isinstance(step, list):
            turns.append([tool(s["tool"], **(s.get("input") or {})) if isinstance(s, dict) else s for s in step])
    return turns


def run_check(check, ws: Path, result) -> tuple[str, bool, str]:
    if isinstance(check, dict):
        kind, arg = next(iter(check.items()))
    else:
        kind, arg = str(check), None
    try:
        if kind == "final_contains":
            return kind, str(arg).lower() in result.final_text.lower(), str(arg)
        if kind == "file_exists":
            return kind, (ws / str(arg)).exists(), str(arg)
        if kind == "file_contains":
            p = ws / arg["path"]
            return kind, p.exists() and arg["text"] in p.read_text(encoding="utf-8", errors="replace"), f"{arg['path']} ~ {arg['text']}"
        if kind == "json_field":
            p = ws / arg["path"]
            data = json.loads(p.read_text(encoding="utf-8"))
            cur = data
            for part in str(arg["key"]).split("."):
                cur = cur[part]
            return kind, cur == arg.get("expected", True), f"{arg['path']}:{arg['key']}"
        if kind == "completed":
            return kind, result.reason == "completed", result.reason
        return kind, False, f"unknown check {kind}"
    except Exception as exc:  # noqa: BLE001
        return kind, False, f"{exc}"


def run_task(task: dict, model: str) -> dict:
    ws = Path(tempfile.mkdtemp(prefix=f"eval_{task['name']}_"))
    for rel, content in (task.get("files") or {}).items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(content), encoding="utf-8")
    profile = load_profile(task.get("profile", "chat"))
    if task.get("max_cost_usd") is not None:
        profile = profile.with_overrides(limits={**profile.limits, "max_cost_usd": task["max_cost_usd"]})
    if model == "fake":
        if not task.get("script"):
            return {"name": task["name"], "skipped": "no script for the fake model"}
        client = FakeModel(script_to_turns(task["script"]))
    else:
        client = model
    runtime = profile.build(ws, model=client)
    t0 = time.time()
    result = Loop(runtime).run(task["prompt"])
    checks = [run_check(c, ws, result) for c in task.get("checks") or []]
    passed = all(ok for _, ok, _ in checks)
    out = {
        "name": task["name"],
        "profile": profile.name,
        "reason": result.reason,
        "turns": result.turns,
        "seconds": round(time.time() - t0, 1),
        "passed": passed,
        "checks": [{"kind": k, "ok": ok, "detail": d} for k, ok, d in checks],
        "final_text": result.final_text[:500],
    }
    shutil.rmtree(ws, ignore_errors=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="fake")
    ap.add_argument("--task", default=None, help="run one task by name")
    args = ap.parse_args()
    tasks = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(TASKS_DIR.glob("*.yaml"))]
    if args.task:
        tasks = [t for t in tasks if t["name"] == args.task]
    results = [run_task(t, args.model) for t in tasks]
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps({"model": args.model, "results": results}, indent=2), encoding="utf-8")
    print(f"{'task':<24}{'result':<12}{'turns':<7}{'status'}")
    for r in results:
        if "skipped" in r:
            print(f"{r['name']:<24}{'skipped':<12}{'':<7}{r['skipped']}")
            continue
        status = "PASS" if r["passed"] else "FAIL " + "; ".join(f"{c['kind']}({c['detail']})" for c in r["checks"] if not c["ok"])
        print(f"{r['name']:<24}{r['reason']:<12}{r['turns']:<7}{status}")
    print(f"\nwritten: {out}")
    return 0 if all(r.get("passed", True) for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
