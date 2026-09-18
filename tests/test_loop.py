"""Every Terminal and Continue reason, driven by the fake model."""

from __future__ import annotations

from harnessing_loop.core.errors import ModelError, PromptTooLong
from harnessing_loop.core.loop import Loop
from harnessing_loop.core.messages import Usage
from harnessing_loop.llm.fake import FakeModel, tool
from harnessing_loop.persistence.control import set_flag
from harnessing_loop.safety.hooks import Events

from conftest import make_runtime


def reasons(loop: Loop) -> list[str]:
    return [t.reason for t in loop.transitions]


def test_completed_on_plain_reply(tmp_path):
    rt, _ = make_runtime(tmp_path, ["Hello there."])
    res = Loop(rt).run("hi")
    assert res.reason == "completed" and res.final_text == "Hello there." and res.turns == 1


def test_tool_use_loops_back(tmp_path):
    rt, fake = make_runtime(tmp_path, [[tool("list_dir")], "done"])
    (rt.workspace / "a.txt").write_text("x")
    loop = Loop(rt)
    res = loop.run("list")
    assert res.reason == "completed" and reasons(loop) == ["tool_use", "completed"]
    # the tool result reached the model on the second request
    second = fake.requests[1]
    assert any("a.txt" in b.content for m in second.messages for b in m.tool_results_blocks())


def test_max_turns(tmp_path):
    rt, _ = make_runtime(tmp_path, FakeModel(lambda req: [tool("list_dir")]), limits={"max_turns": 3})
    res = Loop(rt).run("loop forever")
    assert res.reason == "max_turns" and res.turns == 3


def test_finish_gate_and_completion(tmp_path):
    rt, _ = make_runtime(
        tmp_path,
        [
            [tool("todo_write", todos=[{"content": "x", "status": "pending"}])],
            [tool("finish", summary="early")],  # refused
            [tool("todo_write", todos=[{"content": "x", "status": "completed"}]), tool("finish", summary="ok")],
        ],
        gates={"finish": ["todos_done"]},
        require_finish=True,
    )
    loop = Loop(rt)
    res = loop.run("go")
    assert res.reason == "completed" and res.final_text == "ok"
    # the refusal text reached the model
    refused = [b for m in loop.messages for b in m.tool_results_blocks() if b.is_error]
    assert any("Cannot finish yet" in b.content for b in refused)


def test_require_finish_nudges_then_incomplete(tmp_path):
    rt, fake = make_runtime(tmp_path, ["done", "really done", "still no finish"], require_finish=True)
    loop = Loop(rt)
    res = loop.run("go")
    assert res.reason == "incomplete"
    assert reasons(loop) == ["stop_hook_blocking", "stop_hook_blocking", "incomplete"]
    assert len(fake.requests) == 3


def test_stop_hook_blocks_once(tmp_path):
    rt, fake = make_runtime(tmp_path, ["first answer", "second answer"])
    calls = []

    def stop_hook(payload):
        calls.append(payload["reentry"])
        return {"block_stop": True, "reason": "Check your work first."} if not payload["reentry"] else None

    rt.hooks.on(Events.STOP, stop_hook)
    loop = Loop(rt)
    res = loop.run("go")
    assert res.reason == "completed" and res.final_text == "second answer"
    assert calls == [False, True]
    assert "Check your work first." in fake.requests[1].messages[-1].text()


def test_output_limit_recovery(tmp_path):
    fake = FakeModel([{"text": "partial", "stop_reason": "max_tokens"}, "rest"])
    rt, _ = make_runtime(tmp_path, fake)
    loop = Loop(rt)
    res = loop.run("go")
    assert res.reason == "completed" and reasons(loop) == ["output_limit_recovery", "completed"]
    assert "output token limit" in fake.requests[1].messages[-1].text()


def test_output_limit_exhausted(tmp_path):
    fake = FakeModel(lambda req: {"text": "x", "stop_reason": "max_tokens"})
    rt, _ = make_runtime(tmp_path, fake)
    res = Loop(rt).run("go")
    assert res.reason == "model_error" and "output limit" in res.message


def test_model_error_terminal(tmp_path):
    fake = FakeModel(["never"])
    fake.fail_next = ModelError("boom", retryable=False)
    rt, _ = make_runtime(tmp_path, fake)
    res = Loop(rt).run("go")
    assert res.reason == "model_error" and "boom" in res.message


def test_prompt_too_long_triggers_compact_then_retry(tmp_path):
    fake = FakeModel(["<summary>compacted</summary>", "after compaction"])
    fake.fail_next = PromptTooLong()
    rt, _ = make_runtime(tmp_path, fake)
    loop = Loop(rt)
    res = loop.run("go")
    assert res.reason == "completed" and reasons(loop)[0] == "compact_retry"
    assert loop.messages[0].kind == "compact_boundary"


def test_prompt_too_long_twice_is_terminal(tmp_path):
    def script(req):
        raise PromptTooLong()

    rt, _ = make_runtime(tmp_path, FakeModel(script))
    res = Loop(rt).run("go")
    assert res.reason == "prompt_too_long"


def test_cancel_via_control_file(tmp_path):
    rt, _ = make_runtime(tmp_path, FakeModel(lambda req: [tool("list_dir")]))

    def cancel_after_first_tool(ev):
        if ev.type == "tool_end":
            set_flag(rt.workspace, cancel=True)

    rt.events.subscribe(cancel_after_first_tool)
    res = Loop(rt).run("go")
    assert res.reason == "aborted"


def test_operator_inbox_reaches_model(tmp_path):
    rt, fake = make_runtime(tmp_path, [[tool("list_dir")], "ok"])
    set_flag(rt.workspace, inbox=["please hurry"])
    Loop(rt).run("go")
    assert "please hurry" in fake.requests[1].messages[-1].text()


def test_stuck_repeat_nudges_then_stops(tmp_path):
    rt, fake = make_runtime(tmp_path, FakeModel(lambda req: [tool("list_dir", path=".")]), limits={"max_turns": 50, "stuck_repeat_limit": 3})
    loop = Loop(rt)
    res = loop.run("go")
    assert res.reason == "stuck"
    nudges = [m for m in loop.messages if m.role == "user" and "same tool call" in m.text()]
    assert len(nudges) == 1


def test_budget_exceeded(tmp_path):
    fake = FakeModel(lambda req: [tool("list_dir")], usage_per_call=Usage(input_tokens=1_000_000, output_tokens=1000))
    fake.model = "opus-test"
    rt, _ = make_runtime(tmp_path, fake, limits={"max_cost_usd": 1.0, "max_turns": 50})
    res = Loop(rt).run("go")
    assert res.reason == "budget_exceeded"


def test_hook_can_stop_run(tmp_path):
    rt, _ = make_runtime(tmp_path, [[tool("list_dir")], "unreachable"])
    rt.hooks.on(Events.PRE_TOOL_USE, lambda p: {"stop": True, "reason": "policy"})
    res = Loop(rt).run("go")
    assert res.reason == "hook_prevented" and "policy" in res.message


def test_events_are_emitted(tmp_path):
    events = []
    rt, _ = make_runtime(tmp_path, [[tool("list_dir")], "ok"], events=events)
    Loop(rt).run("go")
    types = [e.type for e in events]
    for t in ("run_start", "turn_start", "tool_start", "tool_end", "assistant_message", "cost", "run_end"):
        assert t in types


def test_state_saved_and_transcript_written(tmp_path):
    rt, _ = make_runtime(tmp_path, [[tool("notes_append", text="a note")], "ok"])
    Loop(rt).run("go")
    assert (rt.workspace / ".harness" / "state.json").exists()
    assert (rt.workspace / ".harness" / "notes.md").read_text().strip().endswith("a note")
    assert (rt.workspace / ".harness" / "transcript.jsonl").exists()
