from __future__ import annotations

from harnessing_loop.context.compact import build_summary_prompt, is_blocking, parse_summary, should_compact, split_tail
from harnessing_loop.context.microcompact import microcompact
from harnessing_loop.context.tokens import conversation_tokens
from harnessing_loop.core.loop import Loop
from harnessing_loop.core.messages import Message, TextBlock, ToolResultBlock, ToolUseBlock, Usage
from harnessing_loop.core.state import RunConfig
from harnessing_loop.llm.fake import FakeModel, tool
from harnessing_loop.tools.results import CLEARED_MARKER

from conftest import make_runtime


def test_thresholds():
    cfg = RunConfig(context_window_tokens=100_000, compact_buffer_tokens=13_000, blocking_buffer_tokens=3_000)
    assert not should_compact(86_999, cfg) and should_compact(87_000, cfg)
    assert not is_blocking(96_999, cfg) and is_blocking(97_000, cfg)


def test_conversation_tokens_uses_last_usage():
    a = Message(role="assistant", content=[TextBlock("x")], usage=Usage(input_tokens=5000, output_tokens=100))
    after = Message.user("y" * 400)
    n = conversation_tokens([Message.user("q"), a, after])
    assert 5100 < n < 5100 + 400


def test_microcompact_keeps_recent():
    msgs = []
    for i in range(8):
        msgs.append(Message(role="assistant", content=[ToolUseBlock(f"t{i}", "read_file", {"path": str(i)})]))
        msgs.append(Message(role="user", content=[ToolResultBlock(f"t{i}", "content " * 50)]))
    out, cleared = microcompact(msgs, keep_recent=3)
    assert cleared == 5
    assert out[1].content[0].content == CLEARED_MARKER
    assert out[-1].content[0].content.startswith("content")
    assert msgs[1].content[0].content.startswith("content")  # original untouched


def test_microcompact_skips_non_compactable():
    msgs = [
        Message(role="assistant", content=[ToolUseBlock("t", "todo_write", {})]),
        Message(role="user", content=[ToolResultBlock("t", "todo ok")]),
    ]
    out, cleared = microcompact(msgs, keep_recent=0)
    assert cleared == 0


def test_split_tail_starts_at_user_message():
    msgs = [Message.user("first"), Message.assistant("a"), Message.user("second"), Message.assistant("b")]
    head, tail = split_tail(msgs, keep_tokens=6)
    assert [m.text() for m in tail] == ["second", "b"] and [m.text() for m in head] == ["first", "a"]
    # when everything fits in the budget there is nothing to summarize: all head, no tail
    head, tail = split_tail(msgs, keep_tokens=1000)
    assert tail == [] and head == msgs


def test_summary_prompt_and_parse():
    prompt = build_summary_prompt([Message.user("do x"), Message.assistant("ok")])
    assert "<transcript>" in prompt and "Next action" in prompt and "TEXT ONLY" in prompt
    assert parse_summary("junk <summary> the summary </summary> more") == "the summary"


def test_full_compaction_in_loop_reinjects_notes(tmp_path):
    fake = FakeModel(
        [
            [tool("notes_append", text="decision: use plan B")],
            "<summary>1. Task: continue.\n8. Next action: reply done.</summary>",  # compaction call
            "done after compaction",
        ],
        usage_per_call=Usage(input_tokens=190_000, output_tokens=10),
    )
    events = []
    rt, _ = make_runtime(tmp_path, fake, events=events, limits={"max_turns": 10})
    loop = Loop(rt)
    res = loop.run("start")
    assert res.reason == "completed" and res.final_text == "done after compaction"
    assert any(e.type == "compact" for e in events)
    boundary = loop.messages[0]
    assert boundary.kind == "compact_boundary"
    assert "plan B" in boundary.text() and "Next action" in boundary.text()
    # the transcript recorded the boundary
    kinds = [m.kind for m in rt.transcript.load_all()]
    assert "compact_boundary" in kinds


def test_compaction_circuit_breaker(tmp_path):
    def script(req):
        # every call, including the summary call, is a tool call -> summary is empty
        return [tool("list_dir")]

    fake = FakeModel(script, usage_per_call=Usage(input_tokens=198_000, output_tokens=10))
    events = []
    rt, _ = make_runtime(tmp_path, fake, events=events, limits={"max_turns": 30}, context={"compact_max_failures": 2})
    res = Loop(rt).run("go")
    assert res.reason == "blocking_limit"
    assert sum(1 for e in events if e.type == "warning" and e.data.get("kind") == "compact_failed") == 2


def test_compaction_disabled_hits_blocking_limit(tmp_path):
    fake = FakeModel(lambda req: [tool("list_dir")], usage_per_call=Usage(input_tokens=198_000, output_tokens=10))
    rt, _ = make_runtime(tmp_path, fake, context={"compact": False}, limits={"max_turns": 30})
    assert Loop(rt).run("go").reason == "blocking_limit"
