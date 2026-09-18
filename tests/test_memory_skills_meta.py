from __future__ import annotations

import json

from harnessing_loop.core.loop import Loop
from harnessing_loop.core.messages import ToolUseBlock
from harnessing_loop.llm.fake import FakeModel, tool
from harnessing_loop.persistence.memory import MemoryDir
from harnessing_loop.safety.redact import Redactor
from harnessing_loop.skills.loader import SkillSet
from harnessing_loop.tools.pipeline import run_tool_call

from conftest import make_runtime


def test_memory_write_index_recall(tmp_path):
    m = MemoryDir(tmp_path / "mem")
    m.write("prefers-tabs", "User prefers tabs over spaces in Python", "user", "Use tabs.\n\n**Why:** asked on day one.")
    m.write("deploy-url", "Staging deploy dashboard link", "reference", "https://example.invalid/deploy")
    idx = m.index_text()
    assert "(prefers-tabs.md)" in idx and "(deploy-url.md)" in idx
    picked = m.relevant("what indentation does the user prefer in python?")
    assert [e.name for e in picked] == ["prefers-tabs"]
    text = m.recall_text("deploy dashboard")
    assert "<memory name=\"deploy-url\"" in text and "example.invalid" in text


def test_memory_recall_injected_into_prompt(tmp_path):
    rt, fake = make_runtime(tmp_path, ["ok"], memory={"dir": "mem"})
    rt.memory.write("fact-one", "The project codename is Kestrel", "project", "Codename: Kestrel.")
    Loop(rt).run("what is the project codename?")
    assert "Kestrel" in fake.requests[0].messages[0].text()


def test_skills_listing_and_tool(tmp_path):
    d = tmp_path / "skills" / "deploy"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: deploy\ndescription: Ship it.\nwhen_to_use: after tests pass\n---\n\n1. build\n2. push\n")
    rt, _ = make_runtime(tmp_path, [], skills={"enabled": True, "dirs": [str(tmp_path / "skills")]})
    assert "deploy: Ship it. Use when: after tests pass" in rt.system_prompt
    assert "verify:" in rt.system_prompt  # bundled skill
    r = run_tool_call(ToolUseBlock("t", "skill", {"name": "deploy"}), Loop(rt).ctx)
    assert not r.is_error and "2. push" in r.content
    r = run_tool_call(ToolUseBlock("t", "skill", {"name": "nope"}), Loop(rt).ctx)
    assert r.is_error


def test_define_tool_then_call_it(tmp_path, sandboxed):
    rt, _ = make_runtime(tmp_path, [], **sandboxed)
    ctx = Loop(rt).ctx
    code = "def run(input):\n    return {'double': input['n'] * 2}\n"
    r = run_tool_call(ToolUseBlock("d", "define_tool", {"name": "doubler", "description": "Doubles a number.", "input_schema": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}, "code": code}), ctx)
    assert not r.is_error and "doubler" in rt.registry
    r2 = run_tool_call(ToolUseBlock("c", "doubler", {"n": 21}), ctx)
    assert not r2.is_error and json.loads(r2.content) == {"double": 42}
    r3 = run_tool_call(ToolUseBlock("c2", "doubler", {"n": "x"}), ctx)
    assert r3.is_error  # schema enforced on dynamic tools too
    # persists across runtimes of the same workspace
    rt2, _ = make_runtime(tmp_path, [], **sandboxed)
    assert "doubler" in rt2.registry and "doubler" in rt2.registry.dynamic


def test_define_tool_rejects_bad_names(tmp_path, sandboxed):
    rt, _ = make_runtime(tmp_path, [], **sandboxed)
    ctx = Loop(rt).ctx
    for bad in ("shell", "Bad-Name", "x"):
        r = run_tool_call(ToolUseBlock("d", "define_tool", {"name": bad, "description": "something long enough", "input_schema": {"type": "object"}, "code": "def run(input): return 1"}), ctx)
        assert r.is_error


def test_subagent_returns_child_final_text(tmp_path):
    # parent asks for a subagent; the same fake serves both loops in order
    fake = FakeModel([
        [tool("subagent", description="count files", prompt="how many files?")],
        [tool("list_dir")],        # child turn 1
        "child says: two files",   # child turn 2
        "parent done",
    ])
    rt, _ = make_runtime(tmp_path, fake, tool_packs=["files", "agents"])
    (rt.workspace / "a").write_text("1")
    (rt.workspace / "b").write_text("2")
    loop = Loop(rt)
    res = loop.run("go")
    assert res.reason == "completed" and res.final_text == "parent done"
    result_text = loop.messages[2].tool_results_blocks()[0].content
    assert "child says: two files" in result_text and "[subagent: count files | completed" in result_text


def test_redactor_masks_secrets_and_paths(tmp_path):
    r = Redactor(tmp_path)
    text = f"path {tmp_path}/x.py key AKIAABCDEFGHIJKLMNOP and API_KEY=abc123 and Bearer xyz.123"
    out = r(text)
    assert "<workspace>/x.py" in out or "<workspace>\\x.py" in out
    assert "AKIA" not in out and "abc123" not in out and "xyz.123" not in out
