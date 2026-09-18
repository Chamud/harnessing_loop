# Context management

This doc covers how the loop keeps a long run inside the context window: microcompaction, full compaction, the rebuild afterwards, the prompt cache layout, and what goes into the system prompt versus per-turn reminders.

## Three thresholds

Before every model call, `Loop._manage_context` in `harnessing_loop/core/loop.py` estimates the next request with `Loop._tokens()` (system blocks + tool schemas + `conversation_tokens`, which starts from the last exact usage report and pads later messages by a third) and compares it against three lines:

| Threshold | Default | Action |
|---|---|---|
| `microcompact_min_tokens` | 40,000 | clear old tool results |
| `context_window_tokens - compact_buffer_tokens` | 200,000 - 13,000 | full compaction |
| `context_window_tokens - blocking_buffer_tokens` | 200,000 - 3,000 | `blocking_limit` if compaction is off or keeps failing |

The values are fields of `RunConfig` in `harnessing_loop/core/state.py`; `should_compact` and `is_blocking` in `harnessing_loop/context/compact.py` are the comparisons. A profile sets them under `context:`; the window is `limits.context_window_tokens`.

```yaml
context:
  compact: true
  compact_buffer_tokens: 13000
  blocking_buffer_tokens: 3000
  compact_max_output_tokens: 20000
  compact_max_failures: 3
  microcompact_keep_recent: 5
  microcompact_min_tokens: 40000
```

## Microcompaction

`microcompact(messages, keep_recent, compactable)` in `harnessing_loop/context/microcompact.py` replaces old tool result content with `CLEARED_MARKER` (`"[old tool result cleared]"`, from `harnessing_loop/tools/results.py`).

1. Only results of tools in `COMPACTABLE` qualify: `read_file`, `shell`, `run_python`, `grep_files`, `glob_files`, `list_dir`, `web_fetch`, `web_search`, `edit_file`, `write_file`, `task_output`. Planning tool results stay.
2. Results already cleared or already persisted (`<persisted_output>`) are skipped.
3. The last `keep_recent` candidates are kept; everything older is cleared.
4. `ToolUseBlock` and `ToolResultBlock` pairs stay in place, so the structure the API requires is intact.
5. The function returns a new list. The input messages are not mutated.

## Full compaction

`compact(model, messages, config, keep_tail=True)` in `harnessing_loop/context/compact.py`:

1. `split_tail(messages, keep_tokens=TAIL_KEEP_TOKENS)` walks back up to 8,000 estimated tokens and cuts at a user message that is neither a tool result nor a harness message. The tail is kept verbatim; the head is summarized.
2. `build_summary_prompt(head)` renders the head with `render_for_summary` (results cut to `MAX_RESULT_CHARS_IN_PROMPT` = 1,500) and asks for the numbered sections in `SUMMARY_SECTIONS`: task and intent, key facts and decisions, files, errors and fixes, verification, pending work, current step, next action.
3. `summarize` sends a `ModelRequest` with `tools=[]`, `thinking="off"` and `max_output_tokens=compact_max_output_tokens`. The preamble says "Reply with TEXT ONLY. Do not call tools."
4. `parse_summary` takes the text between `<summary>` tags, or the whole reply without them. An empty summary raises `RuntimeError("empty summary")`.

## The rebuild

`build_post_compact` in `harnessing_loop/context/rebuild.py` returns `[boundary] + tail`. The boundary is one user message with `meta=True, kind="compact_boundary"` containing, in order:

- the continuation instruction and the `<summary>` block
- the transcript path, if any
- `.harness/notes.md` inside `<notes>` and `.harness/todo.json` inside `<todo>`
- the current phase and the last 20 files written
- up to `MAX_FILES` = 5 most recently read files, re-read from disk inside `<file path="...">`, capped at 20,000 chars each and 120,000 total

Files shown are re-recorded in the file state cache; every other entry is forgotten, so the model must read a file again before editing it.

## Failures and the circuit breaker

- Each failed `Loop._compact` increments `state.compact_failures`; success resets it. At `compact_max_failures` the loop stops trying, and once past the blocking line ends with `Terminal("blocking_limit", ...)`.
- With `compact: false` the run ends with `blocking_limit` at the blocking line.
- If the provider still rejects the request, the client raises `PromptTooLong` (`harnessing_loop/core/errors.py`). The loop compacts once with `force=True` (whole conversation, no tail) and returns `Continue("compact_retry")`. A second `PromptTooLong` ends with `Terminal("prompt_too_long", ...)`.

## Prompt cache layout

`harnessing_loop/llm/cache_layout.py` lays the request out as `tools -> system static -> system dynamic -> messages` with three markers:

| Function | Marker |
|---|---|
| `tool_schemas_with_cache` | on the last schema, after sorting by name |
| `system_blocks` | on the static system block only |
| `messages_with_cache` | on the last non-thinking block of the last message |

Rules that keep the prefix stable: schemas are sorted and never reordered mid-run; the static system text is built once and never changes; anything per-turn (date, phase, reminders) goes in the dynamic block or a user message.

## System prompt assembly

`assemble_static_prompt(profile_prompt, workspace=..., registry=..., skills=...)` in `harnessing_loop/context/system_prompt.py` joins the profile prompt, instruction files, the skill listing and the deferred tool stubs.

Instruction files are named `AGENT.md`. `load_instruction_files` reads `~/.harnessing_loop/AGENT.md`, then one per directory from the filesystem root down to the workspace, so closer files come later. A line `@path` includes another `.md` or `.txt` file relative to the including file, up to `MAX_INCLUDE_DEPTH` = 5. Files over `WARN_CHARS` = 40,000 produce a warning.

`dynamic_prompt(runtime, state)` adds the date, workspace name, sandbox backend and phase; `Runtime.dynamic_prompt` replaces it.

## Per-turn attachments

`build_attachments` in `harnessing_loop/context/attachments.py` appends one `<system_reminder>` block to the tool-result message, only for deltas: tools newly loaded or defined, a finished background task, an open todo list every `TODO_REMINDER_EVERY` = 10 turns, a date change, new operator messages. `AttachmentState` remembers what was announced.

## Run this

```
pytest tests/test_compact.py -v
```

`test_full_compaction_in_loop_reinjects_notes` asserts the boundary carries the notes; `test_compaction_circuit_breaker` ends in `blocking_limit`.

Next: `docs/08_progress.md`
