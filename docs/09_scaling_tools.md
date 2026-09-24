# Scaling the tool set

[日本語](ja/09_scaling_tools.md)

This doc covers the six mechanisms that let a profile carry many capabilities without paying for all of them every turn: deferred tools, subagents, background tasks, model-defined tools, skills, and memory recall.

| Mechanism | What it saves | When to use |
|---|---|---|
| Deferred tools | schema tokens every turn | tools needed in a minority of runs |
| Subagents | the parent's context | wide searches, side investigations |
| Background tasks | turn time | commands that run for minutes |
| Model-defined tools | repeated `run_python` boilerplate | a computation called many times |
| Skills | system prompt size | long procedures needed for some tasks |
| Memory recall | re-explaining facts each run | preferences and project facts across runs |

## Deferred tools

```yaml
defer_tools: [web_fetch, web_search, define_tool, task_stop, run_background]
```

`Profile.build` sets `tool.defer = True` on each. `Registry.schemas()` in `harnessing_loop/tools/registry.py` omits them from the request; `Registry.deferred_stub_text()` lists them in the static system prompt, one line each, using `search_hint` or the first sentence of the description.

`tool_search` in `harnessing_loop/tools/packs/meta.py` loads them:

- `select:web_fetch,web_search` loads by name and reports unknown names.
- `fetch page` scores each deferred tool (2 per term in the name, 1 per term in description or hint) and loads the top `max_results`, default 5.

Calling an unloaded tool returns an error from `run_tool_call` in `harnessing_loop/tools/pipeline.py`: `Tool web_fetch is not loaded. Call tool_search with query "select:web_fetch" first, then retry.`

Cost: the tool schema list is the first cached segment of the request. Loading a tool changes it, so the whole prefix is cached again once.

## Subagents

`Subagent` in `harnessing_loop/tools/packs/agents.py` runs a child loop with a fresh message history and returns only its final text.

1. Input: `description`, `prompt`, optional `tools` and `max_turns` (default 30). The prompt must carry everything; the child shares no context.
2. Tools: the names in `tools`, or by default every tool with `read_only` true. `CHILD_EXCLUDED` (`subagent`, `finish`, `define_tool`, `set_phase`) are never given.
3. `Runtime.child(registry=..., system_prompt=..., max_turns=...)` in `harnessing_loop/core/runtime.py` builds the child: same workspace, model, sandbox, permissions and hooks; a cloned file state cache; no gates or transcript, `require_finish=False`, `depth + 1`.
4. `MAX_DEPTH` = 2; `validate` refuses beyond it. Every child tool call is permission-checked on its own.
5. The result is one tool result: `[subagent: <description> | <reason> in <n> turns]` plus the child's final text. A child that did not end `completed` comes back as an error result. Evidence: `subagent_ran`.

## Background tasks

`harnessing_loop/tools/packs/tasks.py`, requires a sandbox:

- `run_background(command, timeout)` starts a thread that calls `sandbox.run_shell`, writes the output to `.harness/tasks/<id>.out` when the command ends, and sets status `completed`, `failed` or `timed_out`. Permission subjects are the subcommands.
- `task_output(id, wait)` returns only the bytes after the task's `offset`, then advances it. `wait: true` blocks up to 120 seconds.
- `task_stop(id)` sets `stop_requested`. Limitation: it does not kill the process. The task ends at its next timeout check, and the output file appears only when the command ends.

## Model-defined tools

`define_tool` in `harnessing_loop/tools/packs/meta.py`. `validate` enforces:

- name matches `^[a-z][a-z0-9_]{2,40}$`, is not in `RESERVED` (`finish`, `subagent`, `define_tool`, `tool_search`, `shell`, `run_python`, `read_file`, `write_file`, `edit_file`), and does not shadow a built-in
- `code` contains `def run(`
- `input_schema` has `type: object`
- a sandbox is configured

The contract is `run(input: dict) -> str | dict`:

```python
def run(input):
    return {"double": input["n"] * 2}
```

`call` writes `tools/<name>.py` and `tools/<name>.json` under the workspace, registers a `DynamicTool` with `dynamic=True`, appends to `state.dynamic_tools` and reports `tool_defined`. On the next run of the same workspace, `load_dynamic_tools(registry, workspace)` (called from `Profile.build`) registers every `tools/*.json` with a matching `.py`.

`DynamicTool.call` never imports the code into the harness process. It calls `sandbox.run_python` with a wrapper that loads the file with `runpy`, passes the input as JSON on stdin and prints the return value. The pipeline validates the input schema first; the permission subject is `dynamic:<name>`.

Why this is safe: a model-made tool can do exactly what `run_python` can do and nothing more. It cannot touch `RunState`, call the model, reach the registry or permissions, or escape the sandbox.

## Skills

`harnessing_loop/skills/loader.py`. Layout is `<skills_dir>/<name>/SKILL.md`:

```markdown
---
name: verify
description: Check the output against the request before declaring the job done.
when_to_use: before calling finish, and after any change that touched more than one file
---

# Verify
1. Re-read the original request. ...
```

```yaml
skills:
  enabled: true
  bundled: true      # include harnessing_loop/skills/bundled/
  dirs: [skills]     # relative to the workspace
```

`SkillSet.listing_text()` puts only name, description and `when_to_use` into the static system prompt. The `skill` tool (`SkillTool`) returns the full body inside `<skill name="...">` and records `skill_used`. The bundled `verify` skill is the one above. The model can write its own with `write_file` to `skills/<name>/SKILL.md`; `discover()` scans on every `skill` call, so it is usable at once and listed on the next run.

## Memory recall

With `memory: {dir: ...}` in the profile, `Loop.run` in `harnessing_loop/core/loop.py` calls `runtime.memory.recall_text(prompt)` once and appends the result to the first user message inside `<system_reminder>` under "Relevant memory:". `MemoryDir` in `harnessing_loop/persistence/memory.py` keeps one fact per `<slug>.md` with `name`, `description` and `type` frontmatter, plus a `MEMORY.md` index. Recall is keyword scoring over name and description, top 5, capped at 6,000 chars; `selector` can replace it.

## Run this

```
pytest tests/test_memory_skills_meta.py tests/test_tools.py -v
```

`test_deferred_tool_hint_and_tool_search` covers deferral; `test_subagent_returns_child_final_text` runs parent and child on one fake model; `test_define_tool_then_call_it` defines, calls and reloads a dynamic tool; `test_skills_listing_and_tool` and `test_memory_recall_injected_into_prompt` cover skills and memory.

Next: `docs/10_application.md`
