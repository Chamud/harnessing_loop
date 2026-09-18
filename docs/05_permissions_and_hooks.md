# Permissions and hooks

This doc covers how a tool call is allowed, denied, or sent to a human, and how hooks steer the run without widening its permissions.

## Modes

`permissions.mode` in a profile, one of `MODES` in `safety/permissions.py`.

| Mode | Behaviour |
|---|---|
| `default` | Read-only tools run. Anything else needs an allow rule or a yes from the ask handler |
| `accept_edits` | Like `default`, plus `edit` tools run when every path is inside the workspace |
| `plan` | Read-only tools only. Everything else is denied |
| `bypass` | Everything runs, except the bypass-immune checks below |
| `dont_ask` | Like `default`, but every non-immune "ask" becomes a deny. For unattended runs |

## Rules

`safety/rules.py`. One rule per string under `allow`, `deny`, or `ask`:

```yaml
permissions:
  mode: default
  allow: ["shell(pytest *)", "read_file"]
  deny:  ["shell(git *)", "shell(rm -rf *)"]
  ask:   ["shell(npm publish*)"]
```

- `shell` (or `shell(*)`) matches every call of the tool.
- `shell(git *)` matches when the pattern matches a subject; `git *` also matches the bare `git`.
- A rule with a real pattern is `content_specific`. Matching is case-sensitive `fnmatch`.

Subjects come from `Tool.permission_subjects(input)`: a path for file tools; for `shell` and `run_background`, `split_subcommands(command)` from `tools/packs/exec.py`, which splits on `&&`, `||`, `;`, `|`, and newlines. A rule matches when any subject matches, so `ls && git push` is seen by `shell(git *)`. This also means `shell(pytest *)` allows `pytest -q && rm -rf /`. Put dangerous prefixes in `deny`, which is checked first.

A blanket deny removes the tool from the registry entirely (`Registry.apply_deny_rules`).

## The decision order

`decide(tool, input, ctx, *, hook_allow=False)` in `safety/permissions.py`. First match wins:

1. deny rule for the tool: deny
2. content-specific ask rule: ask (bypass-immune)
3. protected path guard: ask (bypass-immune)
4. the tool's own `check_permissions`: its answer, if not passthrough
5. mode `bypass`: allow
6. blanket ask rule: ask
7. allow rule: allow
8. mode-specific default (read-only, edits inside workspace): allow or deny
9. passthrough: ask
10. mode `dont_ask` turns ask into deny

Step 10 lives inside `_ask`, so it covers every ask from steps 2 to 9. Every decision is logged to `ctx.log`.

## Bypass-immune checks

These return `Decision(..., immune=True)` and hold in every mode:

- deny rules (step 1)
- content-specific ask rules (step 2)
- protected paths (step 3), from `safety/guards.py`. `PROTECTED_RELATIVE`: `.harness/`, `.git/`, `.hg/`, `.svn/`, `.env`, `.envrc`, `profile.yaml`, `hooks.yaml` inside the workspace. `PROTECTED_HOME`: credential directories such as `.ssh/` and `.gnupg/` plus shell startup files. Checked only for non-read-only calls; `PermissionContext.allow_unsafe_paths` switches it off.

Editing these files is how an agent would widen its own permissions.

## The ask handler

`AskHandler = Callable[[str, dict, str], bool]`, called as `handler(tool_name, input, reason)`. The CLI wires one only when stdin is a terminal. Without a handler an ask is a deny with reason `(no ask handler; unattended run)`. A handler that raises is a deny. In `dont_ask` mode only immune asks reach the handler.

## Dangerous allow rules

An allow rule such as `shell(python *)` grants arbitrary code execution, because anything can be run through an interpreter. That is acceptable when the operator wrote the rule and the sandbox is the real boundary, so by default such rules stand.

When rules come from a source you do not fully trust, set `permissions.strip_dangerous_allows: true`. `Profile.build` then runs `strip_dangerous_allow_rules` from `safety/guards.py`: an allow rule whose pattern is a code-execution prefix from `CODE_EXEC_PREFIXES` (`python`, `bash`, `sudo`, `curl`, `npx`, `pip install`, and others) in the forms `prefix`, `prefix *`, `prefix*`, or `prefix:*` is dropped. A bare rule without a pattern, such as `run_python`, is kept. The dropped rules are listed in `runtime.extra["dropped_allow_rules"]`.

## Hooks

`safety/hooks.py`. Events and the payload keys actually sent:

| Event | Payload | May |
|---|---|---|
| `session_start` | `workspace`, `profile` | add context |
| `user_prompt` | `prompt` | add context, stop |
| `pre_tool_use` | `tool`, `input`, `state` | allow, deny, ask, rewrite input, add context, stop |
| `post_tool_use` | `tool`, `input`, `result`, `is_error` | add context, stop |
| `post_tool_failure` | same as above | add context, stop |
| `stop` | `final_text`, `state`, `reentry` | stop, block the stop |
| `pre_compact` | `messages` | observe |
| `post_compact` | `summary` | observe |
| `session_end` | `reason`, `turns` | observe |

Two kinds:

- `python`: `HookRegistry.on(event, fn, matcher=None, name="")`. `fn(payload)` returns a `HookResult`, a dict, `None`, a bool (allow or deny), or a string (context).
- `command`: `HookRegistry.command(event, argv, matcher=None, timeout=60.0, name="")`. The payload arrives as JSON on stdin.

`matcher` is an `fnmatch` pattern on the payload's `tool`; `None` matches all.

Command output: exit `2` blocks, stderr is the reason. Exit `0` with JSON on stdout is parsed into:

```json
{"decision": "allow" | "deny" | "ask", "reason": "...",
 "updated_input": {...}, "additional_context": "...",
 "stop": false, "block_stop": false}
```

Non-JSON stdout becomes `additional_context`. Other non-zero exits are ignored. A timeout, a failure to start, or a Python hook that raises all count as deny.

Merge precedence in `HookRegistry.run`: any deny, else any ask, else any allow. `stop` and `block_stop` are OR'd. Contexts are concatenated. The last non-empty `updated_input` wins.

### Where hooks meet permissions

`tools/pipeline.py` runs `pre_tool_use` after schema validation and before `decide`. A rewritten input is validated again. A hook deny ends the call with `Blocked by hook`. A hook allow becomes `decide(..., hook_allow=True)`, which swaps in an auto-approving ask handler via `replace_handler`. Ordinary asks resolve; a deny rule still denies, and immune asks go to the original handler. Post-hook context is appended to the result as `<hook_context>...</hook_context>`.

### Stop hooks

`Loop._on_model_stop` runs stop hooks when the model ends a turn without tool calls. `stop: true` ends the run as `hook_prevented`. `block_stop: true` sets `state.stop_hook_active`, sends the reason back as a user message, and continues. The next stop event carries `reentry: true`; a second `block_stop` is only a warning, so a hook cannot loop the model forever.

### Declaring hooks

In a profile YAML (`command` is a string or list; `matcher`, `timeout`, `name` optional):

```yaml
hooks:
  - {event: pre_tool_use, matcher: "shell", command: ["python", "hooks/check.py"], timeout: 30}
```

In Python, before or after `build`:

```python
from harnessing_loop.safety.hooks import Events
from harnessing_loop.profiles.base import load_profile

profile = load_profile("coder")
profile.python_hooks.append((Events.PRE_TOOL_USE, lambda p: {"decision": "deny", "reason": "not today"}, "write_*"))
runtime = profile.build("./work")

runtime.hooks.on(Events.POST_TOOL_USE, lambda p: {"additional_context": "remember to cite"})
```

`python_hooks` entries are `(event, fn, matcher)` tuples.

## Run this

```
python -m pytest tests/test_permissions.py tests/test_hooks.py -q
```

`test_permissions.py` covers rules, modes, immunity under bypass, and stripping. `test_hooks.py` covers deny, matchers, input rewrite, exit code 2, merge precedence, and hook allow versus deny rule.

Next: `docs/06_sandbox.md`
