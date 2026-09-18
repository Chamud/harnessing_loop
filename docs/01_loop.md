# The loop

This doc covers one iteration of `harnessing_loop/core/loop.py`, the reason vocabulary it exits with, the `Runtime` it reads from, and the event and cost objects it writes to.

## One iteration, in order

`Loop._iteration` runs these steps and returns either a `Continue` or a `Terminal`.

| Step | What happens | Where |
|---|---|---|
| 1 | Bump `state.turn`, take a checkpoint snapshot | `_iteration` |
| 2 | Read `.harness/control.json`: raise `Aborted` on cancel, spin on pause, collect the inbox | `_control` |
| 3 | Estimate tokens, microcompact old tool results, full compaction if near the limit | `_manage_context` |
| 4 | Build a `ModelRequest`, stream the model, collect the `ModelResponse` | `_call_model` |
| 5 | Add usage to the cost tracker, append the assistant message, emit `assistant_message` | `_iteration` |
| 6 | `stop_reason == "max_tokens"` with no tool calls: output-limit recovery | `_iteration` |
| 7 | No tool calls: stop hooks, finish requirement, then `Terminal` | `_on_model_stop` |
| 8 | Tool calls: dispatch, per-message size budget, stuck check, attachments, append results | `_iteration` |
| 9 | Infer phase from evidence, save `.harness/state.json` | `_iteration` |
| 10 | Exits: finish accepted, hook stop, stuck or budget, `max_turns`, else `Continue("tool_use")` | `_iteration` |

`PromptTooLong` from step 4 forces one compaction and returns `Continue("compact_retry")`. A second one is `Terminal("prompt_too_long")`. Any other `ModelError` is `Terminal("model_error")`.

## The loop-back signal

The loop continues when the assistant message contains a `tool_use` block. It reads `msg.tool_uses()`, not `response.stop_reason`. The stop reason is only consulted for the `max_tokens` case. This keeps the loop correct across providers that report stop reasons differently, and across the fake model, which derives its stop reason from the blocks it emits.

## Reason vocabulary

`core/state.py` defines the two transition types. Tests assert on the `reason` strings.

`Terminal(reason, turns, message, final_text)`. `bool(terminal)` is true only for `completed`.

| Reason | Meaning |
|---|---|
| `completed` | end of turn, or `finish` accepted |
| `max_turns` | `config.max_turns` reached |
| `aborted` | cancel flag in the control file |
| `blocking_limit` | context full and compaction disabled or failing |
| `prompt_too_long` | request too large after a forced compaction |
| `hook_prevented` | a hook returned `stop` |
| `model_error` | transport failure, or output limit hit repeatedly |
| `budget_exceeded` | `max_cost_usd` crossed |
| `stuck` | repeated identical tool calls or no evidence for too long |
| `incomplete` | `require_finish` set and the model ended twice without calling `finish` |

`Continue(reason)` reasons: `tool_use`, `output_limit_recovery`, `stop_hook_blocking`, `compact_retry`, `budget_continuation`.

## Output-limit recovery

When the reply was cut at `max_tokens` and holds no tool calls, the loop appends a meta user message (`OUTPUT_LIMIT_TEXT`) asking the model to resume without recap, and returns `Continue("output_limit_recovery")`. It does this up to `config.max_output_recovery_attempts` times (default 3), then returns `Terminal("model_error", ..., "output limit hit repeatedly")`.

## Runtime and RunConfig

`core/runtime.py` defines `Runtime`, a dataclass holding everything a run needs. Required: `workspace`, `config`, `model`, `registry`, `system_prompt`, `permissions`, `hooks`. Optional: `sandbox`, `file_state`, `redactor`, `events`, `gates`, `checkpoints`, `transcript`, `memory`, `skills`, `deps`, `dynamic_prompt`, `require_finish`, `profile_name`, `cache_ttl`, `extra`, `depth`. `__post_init__` creates an `EventBus` logging to `.harness/events.jsonl` and a `FileStateCache` when none were given. `child()` builds a subagent runtime with a smaller registry, no gates, no transcript, and a cloned file state cache.

`RunConfig` is a frozen dataclass built once in `Profile.build`. Nothing reads settings mid-run, so behaviour cannot change between turns.

## Events and cost

`core/events.py`: `EventBus.emit(type, **data)` writes a JSONL line when a log path is set, then fans out to subscribers. A broken subscriber is ignored. Event type constants include `RUN_START`, `TURN_START`, `TEXT_DELTA`, `ASSISTANT_MESSAGE`, `TOOL_START`, `TOOL_END`, `COMPACT`, `COST`, `STUCK`, `RUN_END`. `print_subscriber` is a minimal terminal renderer.

`core/cost.py`: `CostTracker.add(model, usage)` accumulates `Usage` per model and prices it from a table keyed by substrings of the model name. Unknown models cost zero. `snapshot()` is emitted as the `cost` event after every model call.

## Minimal offline example

```python
from harnessing_loop.core.events import print_subscriber
from harnessing_loop.core.loop import Loop
from harnessing_loop.llm.fake import scripted, tool
from harnessing_loop.profiles.base import Profile

profile = Profile.from_dict({
    "name": "demo",
    "system_prompt": "You are a test agent.",
    "tool_packs": ["files", "planning"],
    "permissions": {"mode": "accept_edits"},
    "limits": {"max_turns": 10},
})
model = scripted(
    [tool("write_file", path="hello.txt", content="hi\n")],
    [tool("read_file", path="hello.txt")],
    "The file says hi.",
)
rt = profile.build("./work", model=model)
rt.events.subscribe(print_subscriber)
loop = Loop(rt)
result = loop.run("Write hello.txt, then read it back.")
print(result.reason, result.turns)          # completed 3
print([t.reason for t in loop.transitions])  # ['tool_use', 'tool_use', 'completed']
```

`FakeModel` replays each scripted turn, then answers `"done"` forever. Usage is derived from text length, so cost and token estimates have numbers to work with.

## Run this

```
python -m pytest tests/test_loop.py -q
```

Next: `docs/02_tools.md`
