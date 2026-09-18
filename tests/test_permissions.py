from __future__ import annotations

from pathlib import Path

from harnessing_loop.safety.guards import is_protected_path, rule_is_dangerous, strip_dangerous_allow_rules
from harnessing_loop.safety.permissions import PermissionContext, decide
from harnessing_loop.safety.rules import parse_rule, parse_rules
from harnessing_loop.tools.packs.exec import Shell, split_subcommands
from harnessing_loop.tools.packs.files import EditFile, ReadFile, WriteFile


def ctx(ws: Path, mode="default", allow=(), deny=(), ask=(), handler=None) -> PermissionContext:
    rules = parse_rules({"allow": list(allow), "deny": list(deny), "ask": list(ask)})
    return PermissionContext(mode=mode, rules=rules, workspace=ws, ask_handler=handler)


def test_rule_parsing():
    r = parse_rule("shell(git *)", "allow")
    assert r.tool == "shell" and r.pattern == "git *" and r.content_specific
    assert not parse_rule("shell", "deny").content_specific
    assert r.matches("shell", ["git push"]) and r.matches("shell", ["git"]) and not r.matches("shell", ["ls"])


def test_split_subcommands():
    assert split_subcommands("ls && git push | cat; rm x") == ["ls", "git push", "cat", "rm x"]


def test_read_only_allowed_by_default(ws):
    d = decide(ReadFile(), {"path": "a.txt"}, ctx(ws))
    assert d.behavior == "allow"


def test_write_needs_approval_by_default(ws):
    d = decide(WriteFile(), {"path": "a.txt", "content": ""}, ctx(ws))
    assert d.behavior == "deny" and "no ask handler" in d.reason


def test_ask_handler_consulted(ws):
    seen = []
    d = decide(WriteFile(), {"path": "a.txt", "content": ""}, ctx(ws, handler=lambda t, i, r: seen.append(t) or True))
    assert d.behavior == "allow" and seen == ["write_file"]


def test_accept_edits_inside_workspace_only(ws):
    assert decide(EditFile(), {"path": "x.py", "old_string": "a", "new_string": "b"}, ctx(ws, "accept_edits")).behavior == "allow"
    outside = str(ws.parent / "other.py")
    assert decide(EditFile(), {"path": outside, "old_string": "a", "new_string": "b"}, ctx(ws, "accept_edits")).behavior == "deny"


def test_deny_rule_beats_bypass(ws):
    d = decide(Shell(), {"command": "ls && git push origin"}, ctx(ws, "bypass", deny=["shell(git push*)"]))
    assert d.behavior == "deny" and d.immune


def test_content_specific_ask_survives_bypass(ws):
    d = decide(Shell(), {"command": "npm publish"}, ctx(ws, "bypass", ask=["shell(npm publish*)"]))
    assert d.behavior == "deny" and d.immune  # no handler -> deny, still immune


def test_bypass_allows_otherwise(ws):
    assert decide(Shell(), {"command": "make"}, ctx(ws, "bypass")).behavior == "allow"


def test_plan_mode_denies_writes(ws):
    assert decide(WriteFile(), {"path": "a", "content": ""}, ctx(ws, "plan")).behavior == "deny"
    assert decide(ReadFile(), {"path": "a"}, ctx(ws, "plan")).behavior == "allow"


def test_allow_rule_with_subcommand_matching(ws):
    c = ctx(ws, allow=["shell(pytest *)"])
    assert decide(Shell(), {"command": "pytest -q"}, c).behavior == "allow"
    assert decide(Shell(), {"command": "pytest -q && rm -rf /"}, c).behavior == "allow"  # any-subject semantics
    assert decide(Shell(), {"command": "rm -rf /"}, c).behavior == "deny"


def test_dont_ask_turns_ask_into_deny(ws):
    d = decide(WriteFile(), {"path": "a", "content": ""}, ctx(ws, "dont_ask", handler=lambda *a: True))
    assert d.behavior == "deny" and "dont_ask" in d.reason


def test_protected_paths_are_immune(ws):
    assert is_protected_path(ws / ".harness" / "control.json", ws)
    assert is_protected_path(ws / ".git" / "config", ws)
    assert is_protected_path(ws / ".env", ws)
    assert is_protected_path(ws / "src" / "ok.py", ws) is None
    d = decide(WriteFile(), {"path": ".harness/state.json", "content": ""}, ctx(ws, "bypass"))
    assert d.behavior == "deny" and d.immune


def test_dangerous_allow_rules_stripped():
    rules = parse_rules({"allow": ["shell(python *)", "shell(pytest *)", "shell(bash *)"]})
    kept, dropped = strip_dangerous_allow_rules(rules)
    assert [r.pattern for r in dropped] == ["python *", "bash *"] and [r.pattern for r in kept] == ["pytest *"]
    assert rule_is_dangerous(parse_rule("shell(sudo *)", "allow"))


def test_decision_log(ws):
    c = ctx(ws)
    decide(ReadFile(), {"path": "a"}, c)
    assert c.log[-1]["tool"] == "read_file" and c.log[-1]["behavior"] == "allow"
