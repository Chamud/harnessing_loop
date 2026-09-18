from __future__ import annotations

import json
import os
import time

from harnessing_loop.core.loop import Loop
from harnessing_loop.core.messages import ToolUseBlock
from harnessing_loop.core.state import RunState
from harnessing_loop.llm.fake import tool
from harnessing_loop.progress.evidence import infer_phase
from harnessing_loop.progress.gates import Gates, evidence, file_exists, fresh_file, json_field
from harnessing_loop.tools.pipeline import run_tool_call

from conftest import make_runtime

PHASED = dict(
    phases=["plan", "build", "verify", "done"],
    phase_evidence={"build": "file_written"},
    gates={"entry": {"verify": ["file_exists:out"]}, "finish": [{"fresh_file": {"path": "verify/verify.json", "newer_than": "out/*"}}, {"json_field": {"path": "verify/verify.json", "key": "ok", "expected": True}}]},
    require_finish=True,
)


def test_gate_builtins(tmp_path):
    st = RunState(workspace=tmp_path)
    assert "missing evidence" in evidence("x")(st, None)
    st.add_evidence("x")
    assert evidence("x")(st, None) is None
    assert "missing or empty" in file_exists("out.txt")(st, None)
    (tmp_path / "out.txt").write_text("1")
    assert file_exists("out.txt")(st, None) is None
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "a.step").write_text("a")
    (tmp_path / "verify.json").write_text(json.dumps({"ok": False}))
    old = time.time() - 100
    os.utime(tmp_path / "verify.json", (old, old))
    assert "older than" in fresh_file("verify.json", "out/*")(st, None)
    now = time.time() + 10
    os.utime(tmp_path / "verify.json", (now, now))
    assert fresh_file("verify.json", "out/*")(st, None) is None
    assert "expected True" in json_field("verify.json", "ok", True)(st, None)


def test_infer_phase_forward_only(tmp_path):
    st = RunState(workspace=tmp_path, phase="plan")
    st.add_evidence("file_written", "x")
    assert infer_phase(st, ["plan", "build", "verify"], {"build": "file_written"}) == "build"
    st.phase = "verify"
    assert infer_phase(st, ["plan", "build", "verify"], {"build": "file_written"}) is None and st.phase == "verify"


def test_set_phase_forward_only_and_entry_gate(tmp_path):
    rt, _ = make_runtime(tmp_path, [], **PHASED)
    ctx = Loop(rt).ctx
    assert not run_tool_call(ToolUseBlock("1", "set_phase", {"phase": "plan"}), ctx).is_error
    r = run_tool_call(ToolUseBlock("2", "set_phase", {"phase": "verify"}), ctx)
    assert r.is_error and "Cannot enter verify" in r.content
    (rt.workspace / "out").mkdir()
    (rt.workspace / "out" / "x.txt").write_text("x")
    assert not run_tool_call(ToolUseBlock("3", "set_phase", {"phase": "verify"}), ctx).is_error
    r = run_tool_call(ToolUseBlock("4", "set_phase", {"phase": "plan"}), ctx)
    assert r.is_error and "forward only" in r.content
    r = run_tool_call(ToolUseBlock("5", "set_phase", {"phase": "nope"}), ctx)
    assert r.is_error and "Unknown phase" in r.content


def test_finish_requires_last_phase_and_fresh_verifier(tmp_path):
    rt, _ = make_runtime(
        tmp_path,
        [
            [tool("write_file", path="out/a.txt", content="a")],  # phase inferred -> build
            [tool("finish", summary="too early")],
            [tool("write_file", path="verify/verify.json", content='{"ok": true}'), tool("set_phase", phase="verify")],
            [tool("set_phase", phase="done"), tool("finish", summary="all good")],
        ],
        **PHASED,
    )
    loop = Loop(rt)
    res = loop.run("go")
    assert res.reason == "completed" and res.final_text == "all good"
    errs = [b.content for m in loop.messages for b in m.tool_results_blocks() if b.is_error]
    assert any("verify.json does not exist" in e for e in errs) and any("finish requires the last phase" in e for e in errs)
    assert "build" in loop.state.phases_seen
