from __future__ import annotations

import json

from harnessing_loop.core.loop import Loop
from harnessing_loop.core.messages import Message, ToolUseBlock, message_to_record
from harnessing_loop.llm.fake import FakeModel, tool
from harnessing_loop.persistence.transcript import Transcript

from conftest import make_runtime


def test_transcript_chain_roundtrip(tmp_path):
    t = Transcript(tmp_path / "t.jsonl", "s1")
    m1, m2, m3 = Message.user("a"), Message.assistant("b"), Message.user("c")
    for m in (m1, m2, m3):
        t.append(m)
    t.meta("run_end", reason="completed")
    loaded = Transcript(tmp_path / "t.jsonl", "s2").load_all()
    assert [m.text() for m in loaded] == ["a", "b", "c"]
    assert loaded[1].parent_id == m1.id and loaded[2].parent_id == m2.id


def test_load_live_starts_at_boundary(tmp_path):
    t = Transcript(tmp_path / "t.jsonl", "s")
    t.append(Message.user("old"))
    t.append(Message.assistant("older"))
    t.boundary(Message.user("summary here", meta=True, kind="compact_boundary"))
    t.append(Message.assistant("after"))
    live = Transcript(tmp_path / "t.jsonl", "s").load_live()
    assert live[0].kind == "compact_boundary" and [m.text() for m in live] == ["summary here", "after"]


def test_torn_last_line_is_ignored(tmp_path):
    t = Transcript(tmp_path / "t.jsonl", "s")
    t.append(Message.user("ok"))
    with (tmp_path / "t.jsonl").open("a") as fh:
        fh.write('{"type": "message", "id": "x", "rol')
    assert [m.text() for m in Transcript(tmp_path / "t.jsonl", "s").load_all()] == ["ok"]


def test_resume_repairs_orphaned_tool_use(tmp_path):
    # simulate a crash right after the model asked for a tool
    rt, _ = make_runtime(tmp_path, [])
    t = rt.transcript
    t.append(Message.user("do it"))
    t.append(Message(role="assistant", content=[ToolUseBlock("t1", "list_dir", {})]))
    rt2, fake = make_runtime(tmp_path, ["recovered"])
    rt2.transcript = Transcript(rt.transcript.path, "s2")
    loop = Loop.resume(rt2)
    assert loop.messages[-1].is_tool_result_message() and loop.messages[-1].tool_results_blocks()[0].is_error
    res = loop.run(None)
    assert res.reason == "completed" and res.final_text == "recovered"


def test_resume_restores_state(tmp_path):
    rt, _ = make_runtime(tmp_path, [[tool("notes_append", text="n1")], [tool("write_file", path="a.txt", content="x")], "done"])
    Loop(rt).run("go")
    rt2, _ = make_runtime(tmp_path, ["continued"])
    rt2.transcript = Transcript(rt.transcript.path, "s2")
    loop = Loop.resume(rt2)
    assert loop.state.files_written == ["a.txt"] and loop.state.notes == ["n1"]
    assert loop.state.evidence.get("finished") is None
    assert loop.run(None).reason == "completed"


def test_resume_appends_continue_prompt_after_plain_stop(tmp_path):
    rt, _ = make_runtime(tmp_path, ["first"])
    Loop(rt).run("go")
    rt2, fake = make_runtime(tmp_path, ["second"])
    rt2.transcript = Transcript(rt.transcript.path, "s2")
    Loop.resume(rt2).run(None)
    assert "Continue from where you left off" in fake.requests[0].messages[-1].text()


def test_run_meta_records(tmp_path):
    rt, _ = make_runtime(tmp_path, ["x"])
    Loop(rt).run("prompt text")
    recs = list(rt.transcript.records())
    assert recs[0]["type"] == "run_start" and recs[0]["prompt"] == "prompt text"
    assert recs[-1]["type"] == "run_end" and recs[-1]["reason"] == "completed"
