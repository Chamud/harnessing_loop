"""Gates: requirements a phase entry or a finish must satisfy.

A requirement is a callable `(state, ctx) -> str | None`. It returns a
problem description or None. Built-ins cover the common cases; an
application adds its own in the profile module.

    gates = Gates(
        phases=["plan", "build", "verify", "done"],
        entry={"verify": [file_exists("out/result.json")]},
        finish=[fresh_file("verify/verify.json", newer_than="out/*"), json_field("verify/verify.json", "ok", True)],
    )

ゲート。フェーズへの入場、または完了が満たさなければならない要件である。

要件は `(state, ctx) -> str | None` という呼び出し可能オブジェクトである。問題の説明か
None を返す。組み込みのものが典型的な場合を覆う。アプリケーションは独自の要件を
プロファイルのモジュールで追加する。組み立て方は上の例のとおりである。
"""

from __future__ import annotations

import glob
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

Requirement = Callable[[Any, Any], str | None]


@dataclass
class Gates:
    phases: list[str] = field(default_factory=list)
    entry: dict[str, list[Requirement]] = field(default_factory=dict)
    finish: list[Requirement] = field(default_factory=list)

    def index(self, name: str) -> int:
        return self.phases.index(name) if name in self.phases else -1

    def check_entry(self, phase: str, state: Any, ctx: Any) -> list[str]:
        return _run(self.entry.get(phase, []), state, ctx)

    def check_finish(self, state: Any, ctx: Any) -> list[str]:
        problems = _run(self.finish, state, ctx)
        if self.phases and state.phase != self.phases[-1]:
            problems.append(f"Phase is {state.phase!r}; finish requires the last phase {self.phases[-1]!r}.")
        return problems


def _run(reqs: list[Requirement], state: Any, ctx: Any) -> list[str]:
    out = []
    for r in reqs:
        try:
            msg = r(state, ctx)
        except Exception as exc:  # a broken gate blocks, it never lets work through
            # 壊れたゲートは遮断する。作業を通すことは決してない
            msg = f"gate {getattr(r, '__name__', 'check')} crashed: {exc}"
        if msg:
            out.append(msg)
    return out


# ---- built-in requirements -------------------------------------------------------
# ---- 組み込みの要件 -------------------------------------------------------

def evidence(key: str, message: str | None = None) -> Requirement:
    def check(state: Any, ctx: Any) -> str | None:
        if key in state.evidence and state.evidence[key]:
            return None
        return message or f"missing evidence: {key}"

    check.__name__ = f"evidence_{key}"
    return check


def file_exists(rel: str, message: str | None = None) -> Requirement:
    def check(state: Any, ctx: Any) -> str | None:
        p = Path(state.workspace) / rel
        if p.exists() and (p.is_dir() or p.stat().st_size > 0):
            return None
        return message or f"required file is missing or empty: {rel}"

    check.__name__ = f"file_exists_{rel}"
    return check


def fresh_file(rel: str, newer_than: str, message: str | None = None) -> Requirement:
    """`rel` must exist and be newer than every file matching `newer_than` (glob).

    `rel` は存在し、かつ `newer_than`（glob）に一致するすべてのファイルより新しくなければ
    ならない。
    """

    def check(state: Any, ctx: Any) -> str | None:
        ws = Path(state.workspace)
        p = ws / rel
        if not p.exists():
            return message or f"{rel} does not exist; run the verifier"
        t = p.stat().st_mtime
        newest = 0.0
        newest_name = ""
        for m in glob.glob(str(ws / newer_than), recursive=True):
            mt = Path(m).stat().st_mtime
            if mt > newest:
                newest, newest_name = mt, m
        if newest > t:
            try:
                rel_name = Path(newest_name).relative_to(ws).as_posix()
            except ValueError:
                rel_name = newest_name
            return message or f"{rel} is older than {rel_name}; re-run the verifier after the last change"
        return None

    check.__name__ = f"fresh_{rel}"
    return check


def json_field(rel: str, key: str, expected: Any, message: str | None = None) -> Requirement:
    def check(state: Any, ctx: Any) -> str | None:
        p = Path(state.workspace) / rel
        if not p.exists():
            return message or f"{rel} does not exist"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return f"{rel} is not valid JSON: {exc}"
        cur: Any = data
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return message or f"{rel} has no field {key}"
            cur = cur[part]
        if cur != expected:
            return message or f"{rel}: {key} is {cur!r}, expected {expected!r}"
        return None

    check.__name__ = f"json_{rel}_{key}"
    return check


def todos_done(state: Any, ctx: Any) -> str | None:
    open_items = [t["content"] for t in state.todos if t.get("status") != "completed"]
    if open_items:
        return "open todo items: " + "; ".join(open_items[:5])
    return None
