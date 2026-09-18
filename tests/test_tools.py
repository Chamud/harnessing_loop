from __future__ import annotations

import time

from harnessing_loop.core.loop import Loop
from harnessing_loop.core.messages import ToolUseBlock
from harnessing_loop.core.state import RunConfig, RunState
from harnessing_loop.llm.fake import tool
from harnessing_loop.tools.base import Tool, ToolContext, ToolResult, tool as tool_deco, validate_schema
from harnessing_loop.tools.dispatch import partition, run_tool_calls
from harnessing_loop.tools.file_state import FileStateCache
from harnessing_loop.tools.pipeline import run_tool_call
from harnessing_loop.tools.registry import Registry
from harnessing_loop.tools.results import apply_message_budget, size_control

from conftest import make_runtime


def ctx_for(rt, loop=None):
    return (loop or Loop(rt)).ctx


# ---- schema -------------------------------------------------------------------------------

def test_validate_schema_basic():
    s = {"type": "object", "properties": {"n": {"type": "integer"}, "s": {"type": "string", "enum": ["a"]}}, "required": ["n"], "additionalProperties": False}
    assert validate_schema(s, {"n": 1, "s": "a"}) is None
    assert "missing required" in validate_schema(s, {})
    assert "expected integer" in validate_schema(s, {"n": "x"})
    assert "must be one of" in validate_schema(s, {"n": 1, "s": "b"})
    assert "unexpected field" in validate_schema(s, {"n": 1, "z": 1})
    assert "expected integer" in validate_schema(s, {"n": True})


# ---- pipeline -------------------------------------------------------------------------------

def test_unknown_tool_is_error_result(tmp_path):
    rt, _ = make_runtime(tmp_path)
    r = run_tool_call(ToolUseBlock("t", "nope", {}), ctx_for(rt))
    assert r.is_error and "No such tool" in r.content and "read_file" in r.content


def test_invalid_input_is_error_result(tmp_path):
    rt, _ = make_runtime(tmp_path)
    r = run_tool_call(ToolUseBlock("t", "read_file", {}), ctx_for(rt))
    assert r.is_error and "missing required field 'path'" in r.content


def test_crashing_tool_becomes_error_result(tmp_path):
    rt, _ = make_runtime(tmp_path)

    @tool_deco("boom", "crashes", read_only=True)
    def boom(input, ctx):
        raise RuntimeError("kaboom")

    rt.registry.add(boom)
    r = run_tool_call(ToolUseBlock("t", "boom", {}), ctx_for(rt))
    assert r.is_error and "kaboom" in r.content


def test_deferred_tool_hint_and_tool_search(tmp_path):
    rt, _ = make_runtime(tmp_path, tool_packs=["files", "planning", "meta"], defer_tools=["notes_append"])
    ctx = ctx_for(rt)
    assert "notes_append" not in [s["name"] for s in rt.registry.schemas()]
    r = run_tool_call(ToolUseBlock("t", "notes_append", {"text": "x"}), ctx)
    assert r.is_error and "select:notes_append" in r.content
    r2 = run_tool_call(ToolUseBlock("t2", "tool_search", {"query": "select:notes_append"}), ctx)
    assert not r2.is_error and "notes_append" in r2.content
    assert "notes_append" in [s["name"] for s in rt.registry.schemas()]
    r3 = run_tool_call(ToolUseBlock("t3", "notes_append", {"text": "x"}), ctx)
    assert not r3.is_error


def test_tool_search_by_keyword(tmp_path):
    rt, _ = make_runtime(tmp_path, tool_packs=["files", "planning", "meta"], defer_tools=["notes_append", "todo_write"])
    r = run_tool_call(ToolUseBlock("t", "tool_search", {"query": "todo progress"}), ctx_for(rt))
    assert "todo_write" in r.content and rt.registry.is_deferred("notes_append")


# ---- size control -----------------------------------------------------------------------

def test_size_control_persists_large_output(tmp_path):
    rt, _ = make_runtime(tmp_path)
    cfg = RunConfig(tool_result_max_chars=100, tool_result_preview_chars=20)
    t = Tool()
    t.name = "x"
    text = size_control("a" * 500, tool=t, call_id="c1", workspace=rt.workspace, config=cfg)
    assert text.startswith("<persisted_output>") and "c1.txt" in text
    assert (rt.workspace / ".harness" / "tool-results" / "c1.txt").read_text() == "a" * 500


def test_size_control_empty_marker(tmp_path):
    rt, _ = make_runtime(tmp_path)
    assert size_control("   ", tool=Tool(), call_id="c", workspace=rt.workspace, config=RunConfig()) == "(no output)"


def test_message_budget_persists_largest(tmp_path):
    from harnessing_loop.core.messages import ToolResultBlock

    rt, _ = make_runtime(tmp_path)
    cfg = RunConfig(tool_results_budget_per_message=1000, tool_result_preview_chars=10)
    blocks = [ToolResultBlock("a", "x" * 900), ToolResultBlock("b", "y" * 300), ToolResultBlock("c", "z" * 50)]
    out = apply_message_budget(blocks, workspace=rt.workspace, config=cfg)
    assert out[0].content.startswith("<persisted_output>") and out[2].content == "z" * 50


# ---- dispatch ----------------------------------------------------------------------------

def test_partition_groups_consecutive_safe_calls(tmp_path):
    rt, _ = make_runtime(tmp_path)
    ctx = ctx_for(rt)
    calls = [
        ToolUseBlock("1", "read_file", {"path": "a"}),
        ToolUseBlock("2", "list_dir", {}),
        ToolUseBlock("3", "write_file", {"path": "b", "content": ""}),
        ToolUseBlock("4", "read_file", {"path": "a"}),
    ]
    batches = partition(calls, ctx)
    assert [(safe, [c.id for c in b]) for safe, b in batches] == [(True, ["1", "2"]), (False, ["3"]), (True, ["4"])]


def test_parallel_results_keep_order(tmp_path):
    rt, _ = make_runtime(tmp_path)
    ctx = ctx_for(rt)

    @tool_deco("slow", "sleeps", {"type": "object", "properties": {"ms": {"type": "integer"}, "tag": {"type": "string"}}}, read_only=True)
    def slow(input, ctx):
        time.sleep(input["ms"] / 1000)
        return input["tag"]

    rt.registry.add(slow)
    calls = [ToolUseBlock("a", "slow", {"ms": 60, "tag": "first"}), ToolUseBlock("b", "slow", {"ms": 5, "tag": "second"})]
    t0 = time.time()
    out = run_tool_calls(calls, ctx)
    assert [r.content for r in out] == ["first", "second"]
    assert time.time() - t0 < 0.5


# ---- file tools --------------------------------------------------------------------------

def test_read_before_edit_enforced(tmp_path):
    rt, _ = make_runtime(tmp_path)
    ctx = ctx_for(rt)
    (rt.workspace / "f.txt").write_text("hello\n")
    r = run_tool_call(ToolUseBlock("1", "edit_file", {"path": "f.txt", "old_string": "hello", "new_string": "bye"}), ctx)
    assert r.is_error and "not been read" in r.content
    run_tool_call(ToolUseBlock("2", "read_file", {"path": "f.txt"}), ctx)
    r = run_tool_call(ToolUseBlock("3", "edit_file", {"path": "f.txt", "old_string": "hello", "new_string": "bye"}), ctx)
    assert not r.is_error and (rt.workspace / "f.txt").read_text() == "bye\n"


def test_stale_read_detected(tmp_path):
    cache = FileStateCache()
    p = tmp_path / "s.txt"
    p.write_text("a")
    cache.record(p, "a")
    assert cache.check_before_edit(p) is None
    time.sleep(0.02)
    p.write_text("changed")
    import os

    os.utime(p, (time.time() + 5, time.time() + 5))
    assert "modified since" in cache.check_before_edit(p)


def test_edit_uniqueness(tmp_path):
    rt, _ = make_runtime(tmp_path)
    ctx = ctx_for(rt)
    (rt.workspace / "u.txt").write_text("x x x")
    run_tool_call(ToolUseBlock("r", "read_file", {"path": "u.txt"}), ctx)
    r = run_tool_call(ToolUseBlock("e", "edit_file", {"path": "u.txt", "old_string": "x", "new_string": "y"}), ctx)
    assert r.is_error and "3 times" in r.content
    r = run_tool_call(ToolUseBlock("e2", "edit_file", {"path": "u.txt", "old_string": "x", "new_string": "y", "replace_all": True}), ctx)
    assert not r.is_error and (rt.workspace / "u.txt").read_text() == "y y y"


def test_paths_outside_workspace_rejected(tmp_path):
    rt, _ = make_runtime(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("s")
    r = run_tool_call(ToolUseBlock("1", "read_file", {"path": str(outside)}), ctx_for(rt))
    assert r.is_error and "outside the workspace" in r.content


def test_glob_and_grep(tmp_path):
    rt, _ = make_runtime(tmp_path)
    ctx = ctx_for(rt)
    (rt.workspace / "src").mkdir()
    (rt.workspace / "src" / "a.py").write_text("def alpha():\n    pass\n")
    (rt.workspace / "src" / "b.txt").write_text("nothing")
    g = run_tool_call(ToolUseBlock("g", "glob_files", {"pattern": "**/*.py"}), ctx)
    assert "src/a.py" in g.content and "b.txt" not in g.content
    s = run_tool_call(ToolUseBlock("s", "grep_files", {"pattern": r"def \w+", "glob": "*.py"}), ctx)
    assert "src/a.py:1" in s.content


def test_evidence_recorded_on_write(tmp_path):
    rt, _ = make_runtime(tmp_path)
    loop = Loop(rt)
    run_tool_call(ToolUseBlock("w", "write_file", {"path": "o.txt", "content": "1"}), loop.ctx)
    assert loop.state.evidence["file_written"] == "o.txt" and loop.state.files_written == ["o.txt"]


def test_checkpoint_backup_and_rewind(tmp_path):
    rt, _ = make_runtime(tmp_path)
    loop = Loop(rt)
    p = rt.workspace / "c.txt"
    p.write_text("v1")
    run_tool_call(ToolUseBlock("r", "read_file", {"path": "c.txt"}), loop.ctx)
    rt.checkpoints.snapshot(1)
    run_tool_call(ToolUseBlock("w", "write_file", {"path": "c.txt", "content": "v2"}), loop.ctx)
    assert p.read_text() == "v2"
    restored = rt.checkpoints.rewind(1)
    assert p.read_text() == "v1" and restored
