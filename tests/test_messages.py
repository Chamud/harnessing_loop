from harnessing_loop.core.messages import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    drop_empty_assistant,
    merge_adjacent_user,
    message_from_record,
    message_to_record,
    normalize_for_api,
    repair_orphans,
    to_api,
)


def test_repair_orphans_adds_error_results():
    a = Message(role="assistant", content=[ToolUseBlock("t1", "read_file", {"path": "x"}), ToolUseBlock("t2", "read_file", {"path": "y"})])
    partial = Message(role="user", content=[ToolResultBlock("t1", "ok")])
    fixed = repair_orphans([Message.user("hi"), a, partial])
    results = fixed[2].tool_results_blocks()
    assert {r.tool_use_id for r in results} == {"t1", "t2"}
    assert [r for r in results if r.tool_use_id == "t2"][0].is_error


def test_repair_orphans_when_no_result_message_at_all():
    a = Message(role="assistant", content=[ToolUseBlock("t1", "shell", {"command": "ls"})])
    fixed = repair_orphans([Message.user("hi"), a])
    assert len(fixed) == 3
    assert fixed[2].role == "user" and fixed[2].tool_results_blocks()[0].is_error


def test_drop_empty_assistant_and_merge_users():
    msgs = [
        Message.user("a"),
        Message(role="assistant", content=[ThinkingBlock("...")]),
        Message.user("b"),
    ]
    out = normalize_for_api(msgs)
    assert len(out) == 1 and out[0].text() == "ab"


def test_first_message_must_be_user():
    msgs = [Message.assistant("stray"), Message.user("x")]
    out = normalize_for_api(msgs)
    assert out[0].role == "user"


def test_record_roundtrip():
    m = Message(role="assistant", content=[TextBlock("hi"), ToolUseBlock("t", "n", {"a": 1})], stop_reason="tool_use")
    back = message_from_record(message_to_record(m))
    assert back.id == m.id and back.tool_uses()[0].input == {"a": 1} and back.stop_reason == "tool_use"


def test_to_api_shape():
    api = to_api([Message.user("q")])
    assert api == [{"role": "user", "content": [{"type": "text", "text": "q"}]}]


def test_merge_adjacent_keeps_first_id():
    a, b = Message.user("1"), Message.user("2")
    out = merge_adjacent_user([a, b])
    assert out[0].id == a.id and out[0].text() == "12"


def test_drop_empty_keeps_tool_use_only_messages():
    m = Message(role="assistant", content=[ToolUseBlock("t", "n", {})])
    assert drop_empty_assistant([m]) == [m]
