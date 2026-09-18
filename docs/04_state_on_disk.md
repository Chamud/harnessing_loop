# State on disk

This doc covers every file the harness writes under `.harness/`, how a run is resumed from them, and how an operator steers a live run through them.

The principle: the conversation is disposable. Context can be compacted, truncated, or lost to a crash. Real state lives in files, and the loop is rebuilt from them.

## The `.harness/` directory

Created inside the workspace. No tool may write under `.harness/`; it is a protected path (`safety/guards.py`, `PROTECTED_RELATIVE`).

| Path | Written by | Contents |
|---|---|---|
| `transcript.jsonl` | `persistence/transcript.py` | Every message, one JSON record per line, append-only |
| `events.jsonl` | `core/events.py` `EventBus` | Every loop event (`tool_start`, `compact`, `run_end`, ...) |
| `state.json` | `core/loop.py` `_save_state` | The `RunState` keys in `STATE_KEYS`, rewritten atomically after each tool turn and at run end |
| `notes.md` | `tools/packs/planning.py` `_append_note` | Timestamped lines from `notes_append`, `set_phase`, `finish` |
| `todo.json` | `tools/packs/planning.py` `TodoWrite` | The todo list, replaced on every `todo_write` |
| `tool-results/<call_id>.txt` | `tools/results.py` `persist` | Full text of tool results that exceeded their size cap |
| `checkpoints/` | `persistence/checkpoints.py` | `index.json` plus a copy of every file before it was changed |
| `tasks/<task_id>.out` | `tools/packs/tasks.py` | Output of `run_background` commands |
| `control.json` | `persistence/control.py` | Operator flags: `cancel`, `pause`, `inbox` |

`STATE_KEYS` is `("turn", "phase", "phases_seen", "evidence", "files_written", "files_read", "todos", "notes", "compactions", "dynamic_tools")`.

## The transcript

`Transcript(path, session_id)` appends records. A message record:

```json
{"type": "message", "id": "m_...", "parent_id": "m_...", "role": "assistant",
 "content": [{"type": "text", "text": "..."}], "usage": null, "stop_reason": null,
 "meta": false, "kind": "normal", "ts": 1726650000.0, "session": "abc123"}
```

1. `append()` fills `parent_id` with the last id written, so the file is a chain.
2. `meta(kind, **data)` writes `run_start` and `run_end` records. They are skipped when rebuilding messages.
3. `boundary(message)` writes a message record with `"kind": "compact_boundary"`, parented to the last message before compaction.
4. `load_all()` walks back from the last record through `parent_id`, so an abandoned fork is dropped.
5. `load_live()` takes the chain from the last `compact_boundary` onward and runs `normalize_for_api` on it. Everything earlier is represented by the summary inside the boundary message.
6. A torn last line after a crash is ignored. A file over `MAX_READ_BYTES` (200 MB) is refused.

## Resume and orphan repair

`Loop.resume(runtime)` needs a runtime with a transcript. It calls `load_live()`, builds a fresh `RunState`, and fills it from `state.json` with `_load_state`. The evidence keys `finished` and `stop_requested` are dropped on load so the run can keep working.

`run(None)` then decides what to send:

- no messages: `Terminal("completed", 0, "nothing to resume")`
- last message is an assistant reply without tool calls: append `"Continue from where you left off."` as a meta user message
- otherwise: continue as is

Orphans are repaired by `normalize_for_api` in `core/messages.py`. A crash right after the model requested a tool leaves a `tool_use` with no `tool_result`. `repair_orphans` adds a synthetic error result, `"Tool call was interrupted before it produced a result."`, right after that assistant message. `drop_empty_assistant` then removes assistant messages holding only thinking or whitespace, `merge_adjacent_user` collapses neighbouring user turns, and leading non-user messages are dropped. The same function runs before every model call.

```python
from harnessing_loop.core.loop import Loop

loop = Loop.resume(runtime)   # runtime built by a profile with transcript: true
result = loop.run(None)
```

CLI: `hloop resume --profile coder --workspace ./work`.

## File checkpoints and rewind

`Checkpoints(workspace, max_entries=200)` keeps `.harness/checkpoints/index.json` and copies named `<seq>_<filename>`.

- `snapshot(turn)`: called by the loop at the start of each iteration; records the turn number.
- `backup(path)`: called by `write_file` and `edit_file` before they write. A file that does not exist yet is recorded with `"backup": null`.
- `rewind(turn)`: restores every file touched at or after `turn` to its earliest backup in that range, deletes files that did not exist, and drops those index entries.

```python
from harnessing_loop.persistence.checkpoints import Checkpoints

restored = Checkpoints(runtime.workspace).rewind(turn=12)   # list of restored paths
```

## The control file

`.harness/control.json` is read at the start of every iteration and again during tool execution:

```json
{"cancel": false, "pause": false, "inbox": ["message for the agent"]}
```

- `cancel`: raises `Aborted`; the run ends with reason `aborted`. During tool execution it stops further tool calls.
- `pause`: the loop sleeps in one-second steps until the flag clears or `cancel` is set.
- `inbox`: each entry is delivered once as `Message from the operator: ...` in the next system reminder.

`set_flag(workspace, **flags)` reads, merges, and writes atomically (temp file plus `os.replace`). `inbox` values are appended; other keys are replaced.

```python
from harnessing_loop.persistence.control import set_flag

set_flag(workspace, pause=True)
set_flag(workspace, inbox="Stop after the tests pass.")
set_flag(workspace, pause=False)
```

The same from `cli.py`:

```
hloop control --workspace ./work --cancel
hloop control --workspace ./work --pause
hloop control --workspace ./work --resume
hloop control --workspace ./work --send "Stop after the tests pass."
```

## Notes and todo

The model's own bookkeeping, mirrored to disk by `tools/packs/planning.py`.

- `notes_append` writes `- <timestamp> <text>` to `notes.md` and `state.notes`. `set_phase` and `finish` append a line too.
- `todo_write` replaces `todo.json` with a list of `{"content": ..., "status": "pending" | "in_progress" | "completed"}`.

After a compaction, `context/rebuild.py` reads both files into the boundary message, so decisions survive when the messages that produced them are gone. Readers live in `persistence/notes.py`: `read_notes(workspace, max_chars=20_000)` and `read_todos(workspace)`.

## Memory

`MemoryDir` (`persistence/memory.py`) is long-term state outside any one workspace. A profile sets `memory: {dir: ~/.harnessing_loop/memory}`; a relative dir resolves against the workspace.

One fact per file plus `MEMORY.md`, the index:

```
---
name: prefers-tabs
description: User prefers tabs over spaces in Python
type: user
---

Use tabs.
```

`type` is one of `user`, `feedback`, `project`, `reference`. `write(name, description, type, body)` slugs the name, writes the file, and updates the index: one line per entry, a markdown link to the file followed by its description, capped at `MAX_INDEX_LINES` (200) and `MAX_INDEX_BYTES` (25,000).

`relevant(query, k=5)` scores entries by keyword overlap between the query and `name` plus `description`, newest first on ties. A profile can plug in `selector(query, entries, k) -> list[str]` instead. `recall_text(query)` wraps each picked body in `<memory name="..." type="...">`. `Loop.run` calls it on the first prompt and appends the result inside a `<system_reminder>`.

```python
from pathlib import Path
from harnessing_loop.persistence.memory import MemoryDir

mem = MemoryDir(Path("~/.harnessing_loop/memory").expanduser())
mem.write("deploy-url", "Staging deploy dashboard link", "reference", "https://example.invalid/deploy")
print(mem.recall_text("deploy dashboard"))
```

## Run this

```
python -m pytest tests/test_resume.py tests/test_memory_skills_meta.py -q
```

`test_resume.py` covers the parent chain, boundary loading, torn lines, orphan repair, and state restore. `test_memory_skills_meta.py` covers memory write, index, and recall into the prompt.

Next: `docs/05_permissions_and_hooks.md`
