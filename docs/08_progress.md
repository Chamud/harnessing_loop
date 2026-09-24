# Progress: phases, gates, evidence, stuck detection

[日本語](ja/08_progress.md)

This doc covers how the harness decides whether a run is moving forward: evidence recorded by tools, forward-only phases with entry gates, the `finish` gate, phase inference, and stuck detection.

## The principle

The model says what it did. The harness decides from evidence. Assistant text counts for nothing; a key in `RunState.evidence` counts, and only tools write those keys.

## Evidence

`RunState` in `harnessing_loop/core/state.py` carries `evidence: dict[str, Any]`. `add_evidence(key, value=True)` sets the key and resets `turns_without_evidence`.

Tools report evidence as keyword arguments to `ToolResult.ok` in `harnessing_loop/tools/base.py`:

```python
from harnessing_loop.tools.base import ToolResult

return ToolResult.ok(f"Wrote {n:,} chars to {rel}", file_written=rel)
```

Step 8 of `run_tool_call` in `harnessing_loop/tools/pipeline.py` merges `result.evidence` into the state, for non-error results only. The built-in packs record:

| Pack | Tool | Evidence key |
|---|---|---|
| files | `write_file`, `edit_file` | `file_written` = relative path |
| exec | `shell` | `shell_ran` = True |
| exec | `run_python` | `python_ran` = True |
| planning | `set_phase` | `phase` = phase name |
| planning | `finish` | `finished` = status, plus `final_summary` |

`finished` ends the run: after each tool turn the loop checks it and returns `Terminal("completed", ...)`.

## Phases

A profile lists `phases:` in order. `set_phase` in `harnessing_loop/tools/packs/planning.py`:

1. `validate` rejects a name not in `gates.phases` and lists the order.
2. `call` compares indexes with `Gates.index`. A lower index is refused: "Cannot move back from X to Y. Phases move forward only."
3. `gates.check_entry(phase, state, ctx)` runs the entry requirements. Problems are refused as "Cannot enter Y yet:" with one line each.
4. On success it sets `state.phase`, appends to `phases_seen`, emits `PHASE` and writes a note.

## Gates

`Gates` in `harnessing_loop/progress/gates.py` holds `phases`, `entry` (phase to requirements) and `finish`. A requirement is any callable `(state, ctx) -> str | None`: a problem description, or `None` when satisfied. A requirement that raises is reported as a problem; a broken gate blocks.

| Requirement | Passes when |
|---|---|
| `evidence(key)` | `state.evidence[key]` is truthy |
| `file_exists(rel)` | the path exists and is a directory or non-empty |
| `fresh_file(rel, newer_than)` | `rel` exists and is newer than every file matching the glob |
| `json_field(rel, key, expected)` | the JSON file has dotted `key` equal to `expected` |
| `todos_done` | no todo item is in a state other than `completed` |

Each factory accepts an optional `message`. A custom requirement is a plain function, attached through the profile's `extra_gates` field, which `Profile.build` in `harnessing_loop/profiles/base.py` merges into `Gates`:

```python
from harnessing_loop.profiles.base import load_profile

def has_three_outputs(state, ctx):
    n = len(list((state.workspace / "out").glob("*.json")))
    return None if n >= 3 else f"expected 3 outputs under out/, found {n}"

profile = load_profile("template_app").with_overrides(extra_gates={"finish": [has_three_outputs]})
runtime = profile.build("./work")
```

`check_finish` also adds a problem when `state.phase` is not the last phase.

## The finish tool

`finish` runs `gates.check_finish` and refuses with an error result:

```
Cannot finish yet:
- verify/verify.json does not exist; run the verifier
- open todo items: write the report
- Phase is 'build'; finish requires the last phase 'done'.
```

`finish` with `status: blocked` skips the gates and records `finished = "blocked"`; the loop still ends `completed` with the summary as final text.

## require_finish

With `require_finish: true`, `Loop._on_model_stop` in `harnessing_loop/core/loop.py` does not accept a plain end of turn. If `finished` is absent and `finish` is in the registry, it appends `FINISH_NUDGE_TEXT` as a meta user message and continues, at most twice. The third plain stop ends with `Terminal("incomplete", ...)`. Without `require_finish`, a plain stop is `Terminal("completed", ..., "end of turn")`.

## Phase inference

`infer_phase(state, phases, phase_evidence)` in `harnessing_loop/progress/evidence.py` runs after every tool turn. The profile's `phase_evidence:` maps phase names to evidence keys. The highest phase whose key is present becomes current, forward only. Inference only covers work done without a `set_phase`; such moves emit `PHASE` with `inferred=True`.

## Gate specs in YAML

`_build_req` in `harnessing_loop/profiles/base.py` accepts:

| Form | Example |
|---|---|
| string | `todos_done`, `evidence:verified`, `file_exists:out` |
| one-key dict | `{evidence: verified}`, `{file_exists: out}`, `{todos_done: true}` |
| dict with args | `{fresh_file: {path: verify/verify.json, newer_than: "out/**/*", message: ...}}` |
| dict with args | `{json_field: {path: verify/verify.json, key: ok, expected: true, message: ...}}` |

Anything else raises `ConfigError`. From `harnessing_loop/profiles/template_app.yaml`:

```yaml
phases: [plan, build, verify, done]
phase_evidence:
  build: file_written
  verify: verified

gates:
  entry:
    verify:
      - file_exists: out
  finish:
    - fresh_file: {path: verify/verify.json, newer_than: "out/**/*"}
    - json_field: {path: verify/verify.json, key: ok, expected: true}
    - todos_done

require_finish: true
```

No built-in tool produces `verified`. An application's verifier tool adds it with `ToolResult.ok(..., verified=True)`.

## Stuck detection

`StuckDetector` in `harnessing_loop/progress/stuck.py` is called once per tool turn as `observe(calls, state, cost_usd)` and returns `(nudge, stop_reason)`:

| Signal | Key under `limits:` | Behaviour |
|---|---|---|
| same tool call with identical input `repeat_limit` times in a row | `stuck_repeat_limit` (3) | nudge first, stop on recurrence |
| `no_evidence_turns` turns without new evidence | `stuck_no_evidence_turns` (12) | nudge and reset, stop on recurrence |
| cost above budget | `max_cost_usd` | stop at once |

A nudge goes into the tool-result message inside `<system_reminder>` with a `STUCK` event. A stop becomes `Terminal("budget_exceeded", ...)` when the reason mentions the budget, otherwise `Terminal("stuck", ...)`.

## Run this

```
pytest tests/test_progress.py -v
```

`test_gate_builtins` exercises each requirement; `test_set_phase_forward_only_and_entry_gate` checks the refusals; `test_finish_requires_last_phase_and_fresh_verifier` runs a phased job and asserts both refusal texts before the accepted `finish`.

Next: `docs/09_scaling_tools.md`
