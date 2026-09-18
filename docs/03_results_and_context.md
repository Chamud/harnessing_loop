# Tool results and token accounting

This doc covers how a tool result is bounded, redacted, and counted before the model sees it, and how the harness tracks what the model has read.

## Size control

`tools/results.py`. Two levels, one per call and one per message.

### Per call: `size_control`

`size_control(text, tool=..., call_id=..., workspace=..., config=...)` runs inside the pipeline's `finish()` after redaction.

1. Empty or whitespace-only text becomes `EMPTY_MARKER`, the string `(no output)`. Some models react badly to an empty block.
2. The cap is `cap_for(tool, config)`: the tool's `max_result_chars` clamped to `config.tool_result_max_chars` (default 50,000). `None` means use the config value. `0` means never spill; the tool bounds itself, as `read_file` does.
3. If the text is longer than the cap, `persist()` writes it to `.harness/tool-results/<call_id>.txt` and the model receives `large_result_text(...)` instead.

The replacement text looks like this:

```
<persisted_output>
Output too large (120,000 chars). Full output saved to: .harness/tool-results/tu_abc.txt

Preview (first 2,000 chars):
...
</persisted_output>
```

The preview length is `config.tool_result_preview_chars` (default 2,000). `persist` never overwrites an existing file, so a replayed transcript keeps the original output.

### Per message: `apply_message_budget`

The loop calls `apply_message_budget(results, workspace=..., config=...)` on the whole batch of results from one assistant turn. If their total length exceeds `config.tool_results_budget_per_message` (default 200,000), the largest results are persisted one at a time, largest first, until the sum fits. Results that already start with `<persisted_output>` are skipped.

### Markers

| Constant | Value | Used by |
|---|---|---|
| `EMPTY_MARKER` | `(no output)` | `size_control` |
| `CLEARED_MARKER` | `[old tool result cleared]` | `context/microcompact.py` when clearing old results |

## The file state cache

`tools/file_state.py`, `FileStateCache`. It records what the model has read, with content and mtime, keyed by normalized absolute path. The cache holds `max_entries` (default 100); the oldest entry falls out first. `Runtime.__post_init__` creates one when none is given, and `Runtime.child()` hands a subagent a `clone()`.

Rules, enforced by `check_before_edit(path)` from `write_file.validate` and `edit_file.validate`:

| Situation | Result |
|---|---|
| File does not exist | allowed; creating a file needs no prior read |
| Never read, or read with `partial=True` | `File has not been read in full yet. Read it first, then edit.` |
| mtime newer than at read time, content differs | `File has been modified since it was read. Read it again before editing.` |
| mtime newer, content identical | allowed; touched but unchanged |

`read_file` calls `record(path, text, partial=...)` after every read. A read with `offset` or `limit`, or one truncated at `MAX_READ_CHARS`, is partial. `write_file` and `edit_file` call `record` with the new content after writing, so a follow-up edit is allowed without another read.

## Token estimation

`context/tokens.py`. There is no tokenizer. Two sources are combined:

1. The most recent assistant message that carries `usage`. Its `input_tokens + cache_read_tokens + cache_write_tokens + output_tokens` is exact for everything up to and including that message.
2. Messages after it are estimated by `message_tokens`, which sums `estimate_tokens` over text, thinking, tool inputs, and tool results, adds 10 per tool block, and counts an image as `IMAGE_TOKENS` (1,500).

`estimate_tokens(text)` is `len(text) / CHARS_PER_TOKEN * PAD + 1`, with `CHARS_PER_TOKEN = 4` and `PAD = 4/3`. The padding makes estimates err high, so compaction runs early rather than the request failing at the API.

`conversation_tokens(messages, system_tokens, tool_tokens)` is the number `Loop._tokens` feeds to `should_compact` and `is_blocking`. When no assistant message has usage yet, it falls back to estimating every message plus system and tool schema tokens.

## Redaction

`safety/redact.py`, `Redactor(workspace)`. The pipeline applies it to every tool result before size control. Calling the instance runs `paths` then `secrets`:

- `paths`: replaces the absolute workspace path with `<workspace>` and the home directory with `~`. The model reasons about relative paths and the transcript stays portable.
- `secrets`: masks cloud access key ids, bearer tokens, private key blocks, and `KEY=value` pairs whose key contains `SECRET`, `TOKEN`, `PASSWORD`, `PASSWD`, `API_KEY`, or `ACCESS_KEY`. A key-value match becomes `KEY=<redacted>`; anything else becomes `<redacted>`.

Extra patterns can be passed as `Redactor(workspace, extra_patterns=[...])`.

## Example

```python
from harnessing_loop.core.state import RunConfig
from harnessing_loop.tools.base import Tool
from harnessing_loop.tools.results import size_control

cfg = RunConfig(tool_result_max_chars=100, tool_result_preview_chars=20)
t = Tool()
t.name = "x"
out = size_control("a" * 500, tool=t, call_id="c1", workspace=workspace, config=cfg)
assert out.startswith("<persisted_output>") and "c1.txt" in out
assert size_control("   ", tool=Tool(), call_id="c", workspace=workspace, config=RunConfig()) == "(no output)"
```

`workspace` is any `Path`; the file lands at `workspace / ".harness" / "tool-results" / "c1.txt"`.

## Run this

```
python -m pytest tests/test_tools.py tests/test_compact.py -q
```

Next: `docs/04_state_on_disk.md`
