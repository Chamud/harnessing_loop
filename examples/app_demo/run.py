"""Run the CSV report application end to end.

    python examples/app_demo/run.py                 # scripted fake model, offline
    python examples/app_demo/run.py --model <id>    # a real model

The scripted run shows the full shape of an application job: phases,
a refused finish, a verifier that recomputes the numbers, and a hook.
The workspace is copied to a temp dir so the example stays clean.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from harnessing_loop import Loop, load_profile  # noqa: E402
from harnessing_loop.core.events import print_subscriber  # noqa: E402
from harnessing_loop.llm.fake import FakeModel, tool  # noqa: E402

SUMMARY_SCRIPT = """import csv, json
from collections import defaultdict
rows = list(csv.DictReader(open('data/orders.csv', encoding='utf-8')))
by = defaultdict(float)
for r in rows: by[r['customer']] += float(r['amount'])
s = {'orders': len(rows), 'revenue': round(sum(float(r['amount']) for r in rows), 2), 'top_customer': max(by, key=by.get)}
import os; os.makedirs('out', exist_ok=True)
json.dump(s, open('out/summary.json', 'w'), indent=2)
open('out/report.md', 'w').write(f"# Orders report\\n\\nThere were {s['orders']} orders with total revenue {s['revenue']:.2f}. The top customer was {s['top_customer']}.\\n")
print(s)
"""


def demo_model() -> FakeModel:
    return FakeModel([
        [tool("set_phase", phase="plan"), tool("read_file", path="data/orders.csv", limit=3)],
        [tool("todo_write", todos=[{"content": "compute summary", "status": "in_progress"}, {"content": "verify", "status": "pending"}])],
        [tool("set_phase", phase="build"), tool("shell", command="rm -rf out")],          # blocked by the hook
        [tool("run_python", code=SUMMARY_SCRIPT)],
        [tool("finish", summary="done")],                                                   # refused: phase, verifier, todos
        [tool("set_phase", phase="verify"), tool("shell", command="python verify.py")],
        [tool("todo_write", todos=[{"content": "compute summary", "status": "completed"}, {"content": "verify", "status": "completed"}]),
         tool("set_phase", phase="done"),
         tool("finish", summary="Produced out/summary.json and out/report.md from data/orders.csv and verified them with verify.py.")],
    ])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--keep", action="store_true", help="keep the temp workspace")
    args = ap.parse_args()

    ws = Path(tempfile.mkdtemp(prefix="csv_report_"))
    shutil.copytree(HERE / "workspace", ws, dirs_exist_ok=True)
    profile = load_profile(HERE / "profile.yaml")
    runtime = profile.build(ws, model=args.model or demo_model())
    runtime.events.subscribe(print_subscriber)
    result = Loop(runtime).run("Produce the report from data/orders.csv.")
    print(f"\nresult: {result.reason} after {result.turns} turns")
    print(result.final_text)
    print(f"verify: {(ws / 'verify' / 'verify.json').read_text() if (ws / 'verify' / 'verify.json').exists() else 'missing'}")
    print(f"workspace: {ws}")
    if not args.keep:
        shutil.rmtree(ws, ignore_errors=True)
    return 0 if result.reason == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
