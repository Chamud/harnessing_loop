"""The verifier: recomputes the numbers and writes verify/verify.json.

It is deliberately independent of whatever the agent wrote. A gate that
trusts the agent's own claims is not a gate.
"""

import csv
import json
from collections import defaultdict
from pathlib import Path

root = Path(__file__).parent
rows = list(csv.DictReader((root / "data" / "orders.csv").open(encoding="utf-8")))
expected_orders = len(rows)
expected_revenue = round(sum(float(r["amount"]) for r in rows), 2)
by_customer = defaultdict(float)
for r in rows:
    by_customer[r["customer"]] += float(r["amount"])
expected_top = max(by_customer, key=by_customer.get)

problems = []
summary_path = root / "out" / "summary.json"
if not summary_path.exists():
    problems.append("out/summary.json is missing")
else:
    got = json.loads(summary_path.read_text(encoding="utf-8"))
    if got.get("orders") != expected_orders:
        problems.append(f"orders: got {got.get('orders')}, expected {expected_orders}")
    if round(float(got.get("revenue", -1)), 2) != expected_revenue:
        problems.append(f"revenue: got {got.get('revenue')}, expected {expected_revenue}")
    if got.get("top_customer") != expected_top:
        problems.append(f"top_customer: got {got.get('top_customer')}, expected {expected_top}")

report_path = root / "out" / "report.md"
if not report_path.exists():
    problems.append("out/report.md is missing")
else:
    text = report_path.read_text(encoding="utf-8")
    for needle in (str(expected_orders), f"{expected_revenue:.2f}", expected_top):
        if needle not in text:
            problems.append(f"report.md does not mention {needle}")

(root / "verify").mkdir(exist_ok=True)
result = {"ok": not problems, "problems": problems, "expected": {"orders": expected_orders, "revenue": expected_revenue, "top_customer": expected_top}}
(root / "verify" / "verify.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
raise SystemExit(0 if result["ok"] else 1)
