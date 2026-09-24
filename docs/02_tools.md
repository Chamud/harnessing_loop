# Tools

[日本語](ja/02_tools.md)

This doc covers the `Tool` protocol in `harnessing_loop/tools/base.py`, the call pipeline, the dispatcher, the registry, the packs, and how to add a tool.

## The Tool class

A tool is a class with a name, a description, a JSON schema, a few predicates, and `call`. Class attributes:

| Attribute | Default | Meaning |
|---|---|---|
| `name`, `description` | `""` | sent to the model |
| `input_schema` | empty object schema | validated before `call` |
| `category` | `"other"` | `read`, `edit`, `exec`, `plan`, `web`, `agent`, `meta`, `other` |
| `read_only` | `False` | may run without asking in most modes |
| `concurrency_safe` | `False` | may run in parallel with neighbours |
| `destructive` | `False` | informational flag |
| `max_result_chars` | `None` | `None` uses config; `0` never spills to disk |
| `defer` | `False` | schema withheld until `tool_search` loads it |

Defaults fail closed. A tool is neither read-only nor parallel-safe until it says so. Overridable methods: `is_read_only`, `is_concurrency_safe`, `permission_subjects`, `paths`, `validate`, `check_permissions`, `call`.

## ToolResult and the no-raise rule

`ToolResult.ok(content, data=None, **evidence)` builds a success; keyword arguments are merged into `RunState.evidence` after the call. `ToolResult.error(message)` wraps the message in `<tool_error>...</tool_error>` and sets `is_error`.

A tool never raises to the loop. Return `ToolResult.error(...)` for anything the model should read. An exception that escapes is caught by the pipeline and becomes an error result with a short traceback.

## ToolContext

`ToolContext` is everything a tool may touch: `workspace`, `state`, `config`, `sandbox`, `file_state`, `events`, `registry`, `permissions`, `hooks`, `redactor`, `model`, `gates`, `checkpoints`, `deps`, `tasks`, `extra`, `depth`. Helpers: `harness_dir`, `resolve(path)` (against the workspace, no escape check), `inside_workspace(path)`.

## The `@tool` decorator

```python
from harnessing_loop.tools.base import ToolResult, tool

@tool(
    "line_count",
    "Count lines in a workspace file.",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
    read_only=True,
    category="read",
    subjects=lambda i: [i.get("path", "")],
    paths=lambda i: [i.get("path", "")],
)
def line_count(input, ctx):
    p = ctx.resolve(input["path"])
    if not ctx.inside_workspace(p):
        return ToolResult.error("path is outside the workspace")
    if not p.exists():
        return ToolResult.error(f"no such file: {input['path']}")
    return str(len(p.read_text(encoding="utf-8").splitlines()))
```

The decorator returns a `Tool` instance. A plain string return becomes `ToolResult.ok(str)`. `concurrency_safe` defaults to `read_only` when not given.

## validate_schema

`validate_schema(schema, value)` checks a small subset: `type` (single or list; `bool` is not an `integer` or `number`), `enum`, `required`, `additionalProperties: false`, nested `properties`, `items`, `minLength`, `maxLength`, `minimum`, `maximum`. Returns an error string or `None`.

## The per-call pipeline

`tools/pipeline.py`, `run_tool_call(call, ctx)`. Every exit is a `ToolResultBlock`.

1. Lookup in the registry. Unknown or deferred names return an error naming the fix.
2. Schema validation of the input.
3. `tool.validate(input, ctx)`. A crash here is also an error result.
4. Pre-tool hooks. A hook may stop the run, deny, rewrite the input, or add context.
5. Permission decision via `safety/permissions.py`.
6. `tool.call`. Exceptions become error results.
7. Post-tool hooks (`POST_TOOL_USE` or `POST_TOOL_FAILURE`).
8. Evidence merged into state on success.

On the way out, `finish()` applies the redactor, then size control, then emits `tool_end`.

## Dispatch

`tools/dispatch.py`, `run_tool_calls`. `partition` groups consecutive concurrency-safe calls into one batch. A batch of more than one safe call runs in a thread pool capped at `config.max_tool_concurrency`. Every other call runs alone, in order. Results come back in the original call order.

## Registry and deferred tools

`tools/registry.py`. `schemas()` emits schemas sorted by name and skips deferred tools not yet loaded. `deferred_stub_text()` lists them for the system prompt; `tool_search` calls `load_deferred(names)`. `apply_deny_rules` removes any tool with a blanket deny so the model never sees it.

## Packs

`tools/packs/__init__.py`, `make_pack(name)`.

| Pack | Tools | Notes |
|---|---|---|
| `files` | `read_file`, `write_file`, `edit_file`, `list_dir`, `glob_files`, `grep_files` | in-process, paths checked |
| `exec` | `shell`, `run_python` | needs a sandbox |
| `planning` | `todo_write`, `set_phase`, `notes_append`, `finish` | `finish` refuses until gates pass |
| `web` | `web_fetch`, `web_search` | domain allowlist |
| `agents` | `subagent` | child loop, read-only tools by default |
| `tasks` | `run_background`, `task_output`, `task_stop` | needs a sandbox |
| `meta` | `tool_search`, `define_tool` | model-made tools persist under `tools/` |

## Permission subjects and paths

`permission_subjects(input)` returns strings a rule pattern is matched against. `shell` returns its subcommands, so `shell(git *)` matches `git push` inside `ls && git push`. File tools return the path; `web_fetch` returns the hostname. `paths(input)` returns filesystem paths for guards. A protected path triggers an ask that survives bypass mode.

## Adding a tool to a profile

Two ways. Attach instances with `extra_tools`:

```python
from harnessing_loop.profiles.base import Profile

profile = Profile.from_dict({"name": "demo", "tool_packs": ["files"], "permissions": {"mode": "accept_edits"}})
profile = profile.with_overrides(extra_tools=[line_count])
rt = profile.build("./work", model="fake")
```

Or add a pack: write `make() -> list[Tool]` in a module under `tools/packs/`, register it in `PACKS`, and name it under `tool_packs`.

## Run this

```
python -m pytest tests/test_tools.py -q
```

Next: `docs/03_results_and_context.md`
