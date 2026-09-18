You produce a report from `data/orders.csv`.

Phases, declared with set_phase, forward only:

1. plan: read the CSV header and a few rows. Write a todo list.
2. build: write `out/summary.json` with fields `orders`, `revenue`, `top_customer`
   computed from the CSV by a script you run, then write `out/report.md` that
   states the same numbers in prose.
3. verify: run `python verify.py`. It recomputes the numbers from the CSV and
   writes `verify/verify.json` with `ok: true` when they match.
4. done: call finish with a one-paragraph summary.

Rules:
- Numbers come from running code over the file, never from reading and estimating.
- If verify.py reports a mismatch, fix the output and run it again.
- finish is refused until verify/verify.json is newer than everything in out/ and reports ok.
