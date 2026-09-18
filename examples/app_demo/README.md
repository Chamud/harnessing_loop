# CSV report: a small application on the core

Turns `data/orders.csv` into `out/summary.json` and `out/report.md`, then
proves the numbers with an independent verifier.

```
python examples/app_demo/run.py            # offline, scripted model
python examples/app_demo/run.py --model <model-id>
```

What to watch in the output:

1. `set_phase plan` and a todo list. Phases move forward only.
2. A `shell rm -rf out` call blocked by `hooks/no_destructive.py` (exit code 2).
3. `run_python` computing the summary from the file. Evidence `file_written` moves the phase to `build`.
4. An early `finish` refused with three reasons: wrong phase, missing verifier output, open todos.
5. `python verify.py` recomputing everything and writing `verify/verify.json`.
6. `finish` accepted once the gate sees a fresh `verify.json` with `ok: true`.

Files:

| Path | Role |
|---|---|
| `profile.yaml` | phases, gates, permissions, sandbox, hook |
| `prompt.md` | the system prompt, referenced from the profile |
| `workspace/verify.py` | the verifier, independent of the agent's code |
| `workspace/hooks/no_destructive.py` | a command hook |
| `workspace/data/orders.csv` | input |

To make your own application, copy this folder, change the phases and gates
in `profile.yaml`, replace `verify.py` with a check for your domain, and
point the prompt at your inputs and outputs.
