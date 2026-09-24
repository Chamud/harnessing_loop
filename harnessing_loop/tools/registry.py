"""The tool registry.

- tools are stored by name; a blanket deny rule removes a tool from what
  the model can see at all
- schemas are emitted sorted by name so the cached prefix stays stable
- deferred tools are sent as name-only stubs until `tool_search` loads
  them; loading is tracked here

ツールレジストリ。

- ツールは名前で保管される。包括的な deny ルールは、モデルから見えないように
  ツールごと削除する
- スキーマは名前順で出力されるため、キャッシュプレフィックスが安定する
- 遅延ツールは `tool_search` が読み込むまで名前だけのスタブとして送られる。
  読み込んだかどうかはここで追跡する
"""

from __future__ import annotations

from typing import Any, Iterable

from ..safety.rules import Rule
from .base import Tool


class Registry:
    def __init__(self, tools: Iterable[Tool] = ()):
        self._tools: dict[str, Tool] = {}
        self.loaded_deferred: set[str] = set()
        self.dynamic: set[str] = set()
        for t in tools:
            self.add(t)

    # ---- membership -----------------------------------------------------------
    # ---- 所属 -----------------------------------------------------------------
    def add(self, tool: Tool, *, dynamic: bool = False) -> None:
        if not tool.name:
            raise ValueError("tool has no name")
        self._tools[tool.name] = tool
        if dynamic:
            self.dynamic.add(tool.name)

    def remove(self, name: str) -> None:
        self._tools.pop(name, None)
        self.loaded_deferred.discard(name)
        self.dynamic.discard(name)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def tools(self) -> list[Tool]:
        return [self._tools[n] for n in self.names()]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def subset(self, names: Iterable[str]) -> "Registry":
        r = Registry(self._tools[n] for n in names if n in self._tools)
        r.loaded_deferred = set(self.loaded_deferred) & set(r._tools)
        return r

    # ---- properties -------------------------------------------------------------
    # ---- 性質 -------------------------------------------------------------------
    def has_exec_tools(self) -> bool:
        return any(t.category == "exec" for t in self._tools.values())

    def deferred_names(self) -> list[str]:
        return sorted(n for n, t in self._tools.items() if t.defer and n not in self.loaded_deferred)

    def is_deferred(self, name: str) -> bool:
        t = self._tools.get(name)
        return bool(t and t.defer and name not in self.loaded_deferred)

    def load_deferred(self, names: Iterable[str]) -> list[str]:
        loaded = []
        for n in names:
            if n in self._tools and self._tools[n].defer:
                self.loaded_deferred.add(n)
                loaded.append(n)
        return loaded

    # ---- wire ------------------------------------------------------------------------
    # ---- 送信形式 --------------------------------------------------------------------
    def apply_deny_rules(self, rules: Iterable[Rule]) -> list[str]:
        """Remove tools with a blanket deny. Returns removed names.

        包括的な deny の対象になるツールを削除する。削除した名前を返す。
        """
        removed = []
        for r in rules:
            if r.behavior == "deny" and not r.content_specific and r.tool in self._tools:
                self.remove(r.tool)
                removed.append(r.tool)
        return removed

    def schemas(self) -> list[dict[str, Any]]:
        out = []
        for t in self.tools():
            if t.defer and t.name not in self.loaded_deferred:
                continue
            out.append(t.schema())
        return out

    def deferred_stub_text(self) -> str:
        """Text for the system prompt listing tools that exist but are not loaded.

        存在するがまだ読み込まれていないツールを列挙する、システムプロンプト用の
        文面。
        """
        names = self.deferred_names()
        if not names:
            return ""
        lines = ["Deferred tools (call tool_search with query \"select:<name>\" before using one):"]
        for n in names:
            t = self._tools[n]
            hint = t.search_hint or t.description.split(".")[0]
            lines.append(f"- {n}: {hint}")
        return "\n".join(lines)
