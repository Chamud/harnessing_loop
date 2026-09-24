"""Permission rules.

Syntax, one rule per string:

    shell                 every call of the shell tool
    shell(git *)          shell calls whose subject matches "git *"
    edit_file(src/*)      edits under src/
    write_file(*)         same as bare write_file

A tool exposes one or more "subjects" for a call (a shell command split
into subcommands, a file path). A rule with a pattern matches when ANY
subject matches, so `ls && git push` cannot slip past a `shell(git *)` rule.

Precedence between behaviours is decided in permissions.py, not here.

権限ルール。

構文は1文字列につき1ルールで、上の例のとおりである。`shell` は shell ツールの
すべての呼び出し、`shell(git *)` は判定対象が "git *" に一致する shell 呼び出し、
`edit_file(src/*)` は src/ 以下の編集、`write_file(*)` は裸の `write_file` と同じ。

ツールは1回の呼び出しに対して1つ以上の「判定対象」を提示する（サブコマンドに分割した
shell コマンド、ファイルパスなど）。パターン付きルールはいずれかの判定対象が一致した
時点で一致するため、`ls && git push` が `shell(git *)` のルールをすり抜けることは
ない。

behavior 間の優先順位はここではなく permissions.py で決まる。
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Iterable

_RULE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_\-]*)\s*(?:\((.*)\))?\s*$")


@dataclass(frozen=True)
class Rule:
    behavior: str  # allow | deny | ask
    # behavior は allow | deny | ask のいずれか
    tool: str
    pattern: str | None = None
    source: str = "profile"

    @property
    def content_specific(self) -> bool:
        return self.pattern is not None and self.pattern not in ("*", "")

    def matches(self, tool: str, subjects: Iterable[str]) -> bool:
        if tool != self.tool:
            return False
        if not self.content_specific:
            return True
        pat = self.pattern or "*"
        for s in subjects:
            if fnmatch.fnmatchcase(s, pat) or fnmatch.fnmatchcase(s.replace("\\", "/"), pat):
                return True
            # prefix form: "git *" should also match the bare "git"
            # 前方一致の形式。"git *" は裸の "git" にも一致すべきである
            if pat.endswith(" *") and s.strip() == pat[:-2].strip():
                return True
        return False


def parse_rule(text: str, behavior: str, source: str = "profile") -> Rule:
    m = _RULE_RE.match(text)
    if not m:
        raise ValueError(f"bad permission rule: {text!r}")
    tool, pattern = m.group(1), m.group(2)
    if pattern is not None:
        pattern = pattern.strip()
    return Rule(behavior=behavior, tool=tool, pattern=pattern, source=source)


def parse_rules(spec: dict[str, list[str]] | None, source: str = "profile") -> list[Rule]:
    """spec = {"allow": [...], "deny": [...], "ask": [...]}
    spec は {"allow": [...], "deny": [...], "ask": [...]} という形の辞書である。
    """
    if not spec:
        return []
    out: list[Rule] = []
    for behavior in ("deny", "ask", "allow"):
        for text in spec.get(behavior, []) or []:
            out.append(parse_rule(text, behavior, source))
    return out
