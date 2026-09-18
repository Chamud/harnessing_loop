from __future__ import annotations

import sys

from harnessing_loop.core.loop import Loop
from harnessing_loop.core.messages import ToolUseBlock
from harnessing_loop.safety.hooks import Events, HookRegistry, HookResult
from harnessing_loop.tools.pipeline import run_tool_call

from conftest import make_runtime


def test_pre_hook_deny(tmp_path):
    rt, _ = make_runtime(tmp_path)
    rt.hooks.on(Events.PRE_TOOL_USE, lambda p: {"decision": "deny", "reason": "not today"}, matcher="read_*")
    r = run_tool_call(ToolUseBlock("t", "read_file", {"path": "x"}), Loop(rt).ctx)
    assert r.is_error and "not today" in r.content


def test_pre_hook_matcher_scopes(tmp_path):
    rt, _ = make_runtime(tmp_path)
    rt.hooks.on(Events.PRE_TOOL_USE, lambda p: {"decision": "deny"}, matcher="write_*")
    (rt.workspace / "x").write_text("1")
    r = run_tool_call(ToolUseBlock("t", "read_file", {"path": "x"}), Loop(rt).ctx)
    assert not r.is_error


def test_pre_hook_rewrites_input(tmp_path):
    rt, _ = make_runtime(tmp_path)
    (rt.workspace / "real.txt").write_text("real")
    rt.hooks.on(Events.PRE_TOOL_USE, lambda p: {"updated_input": {"path": "real.txt"}}, matcher="read_file")
    r = run_tool_call(ToolUseBlock("t", "read_file", {"path": "missing.txt"}), Loop(rt).ctx)
    assert not r.is_error and "real" in r.content


def test_hook_allow_cannot_override_deny_rule(tmp_path):
    rt, _ = make_runtime(tmp_path, permissions={"mode": "default", "deny": ["write_file"]}, tool_packs=["files"])
    # blanket deny removed the tool entirely
    assert "write_file" not in rt.registry
    rt2, _ = make_runtime(tmp_path / "b", permissions={"mode": "default", "deny": ["write_file(secret*)"]})
    rt2.hooks.on(Events.PRE_TOOL_USE, lambda p: {"decision": "allow"})
    r = run_tool_call(ToolUseBlock("t", "write_file", {"path": "secret.txt", "content": ""}), Loop(rt2).ctx)
    assert r.is_error and "denied by rule" in r.content


def test_hook_allow_resolves_ask(tmp_path):
    rt, _ = make_runtime(tmp_path, permissions={"mode": "default"})
    rt.hooks.on(Events.PRE_TOOL_USE, lambda p: {"decision": "allow"}, matcher="write_file")
    r = run_tool_call(ToolUseBlock("t", "write_file", {"path": "ok.txt", "content": "1"}), Loop(rt).ctx)
    assert not r.is_error


def test_post_hook_adds_context(tmp_path):
    rt, _ = make_runtime(tmp_path)
    (rt.workspace / "x").write_text("1")
    rt.hooks.on(Events.POST_TOOL_USE, lambda p: {"additional_context": "remember to cite"})
    r = run_tool_call(ToolUseBlock("t", "read_file", {"path": "x"}), Loop(rt).ctx)
    assert "<hook_context>remember to cite</hook_context>" in r.content


def test_command_hook_exit_2_blocks(tmp_path):
    reg = HookRegistry()
    reg.command(Events.PRE_TOOL_USE, [sys.executable, "-c", "import sys; sys.stderr.write('nope'); sys.exit(2)"], name="blocker")
    m = reg.run(Events.PRE_TOOL_USE, {"tool": "x", "input": {}})
    assert m.decision == "deny" and m.reason == "nope"


def test_command_hook_json_output(tmp_path):
    reg = HookRegistry()
    reg.command(Events.PRE_TOOL_USE, [sys.executable, "-c", "import json,sys; d=json.load(sys.stdin); print(json.dumps({'additional_context': 'saw '+d['tool']}))"])
    m = reg.run(Events.PRE_TOOL_USE, {"tool": "shell", "input": {}})
    assert m.context_text == "saw shell"


def test_merge_precedence():
    reg = HookRegistry()
    reg.on(Events.PRE_TOOL_USE, lambda p: {"decision": "allow"})
    reg.on(Events.PRE_TOOL_USE, lambda p: {"decision": "deny", "reason": "d"})
    reg.on(Events.PRE_TOOL_USE, lambda p: {"decision": "ask"})
    assert reg.run(Events.PRE_TOOL_USE, {"tool": "x"}).decision == "deny"


def test_crashing_hook_blocks():
    reg = HookRegistry()

    def bad(p):
        raise ValueError("oops")

    reg.on(Events.PRE_TOOL_USE, bad)
    m = reg.run(Events.PRE_TOOL_USE, {"tool": "x"})
    assert m.decision == "deny" and "oops" in m.reason


def test_hook_result_from_bool():
    assert HookResult.from_any(False).decision == "deny"
    assert HookResult.from_any(True).decision == "allow"
