"""Hooks: the extension point for policy, logging and gates.

Events (payload fields in brackets):
    session_start        [workspace, profile]
    user_prompt          [prompt]                 may add context
    pre_tool_use         [tool, input]            may allow/deny/ask, rewrite input, add context, stop
    post_tool_use        [tool, input, result]    may add context
    post_tool_failure    [tool, input, error]     may add context
    stop                 [final_text, state]      may block the stop and force continuation
    pre_compact          [messages]                (count of messages about to be summarized)
    post_compact         [summary]
    session_end          [reason]

Hook kinds:
    python   a callable `fn(payload) -> HookResult | dict | None`
    command  a subprocess; payload as JSON on stdin; exit code 2 blocks with
             stderr as the reason; exit 0 with JSON on stdout is parsed as a
             HookResult

Merging several results for one event:
    any deny  -> deny;  else any ask -> ask;  else any allow -> allow
    any stop  -> stop
    contexts are concatenated
    the last non-empty updated_input wins

A hook's allow never overrides a deny rule or an immune decision. That is
enforced in tools/pipeline.py, not here.

フック: ポリシー、ログ、ゲートのための拡張点。

イベント（角括弧内はペイロードのフィールド）:
    session_start        [workspace, profile]
    user_prompt          [prompt]                 コンテキストを追加できる
    pre_tool_use         [tool, input]            allow/deny/ask、input の書き換え、
                                                  コンテキストの追加、停止ができる
    post_tool_use        [tool, input, result]    コンテキストを追加できる
    post_tool_failure    [tool, input, error]     コンテキストを追加できる
    stop                 [final_text, state]      停止を阻止して継続を強制できる
    pre_compact          [messages]               （要約されようとしているメッセージの件数）
    post_compact         [summary]
    session_end          [reason]

フックの種類:
    python   `fn(payload) -> HookResult | dict | None` という呼び出し可能オブジェクト
    command  サブプロセス。ペイロードは JSON として stdin に渡る。終了コード 2 は
             stderr を理由としてブロックし、終了コード 0 で stdout に JSON があれば
             HookResult として解釈される

1つのイベントに対する複数の結果の統合:
    deny が1つでもあれば deny。なければ ask が1つでもあれば ask。
    なければ allow が1つでもあれば allow
    stop が1つでもあれば stop
    コンテキストは連結される
    空でない最後の updated_input が採用される

フックの allow が deny ルールやバイパス不可の判定を覆すことはない。これはここでは
なく tools/pipeline.py で強制される。
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable


class Events:
    SESSION_START = "session_start"
    USER_PROMPT = "user_prompt"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    POST_TOOL_FAILURE = "post_tool_failure"
    STOP = "stop"
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"
    SESSION_END = "session_end"
    ALL = (
        SESSION_START,
        USER_PROMPT,
        PRE_TOOL_USE,
        POST_TOOL_USE,
        POST_TOOL_FAILURE,
        STOP,
        PRE_COMPACT,
        POST_COMPACT,
        SESSION_END,
    )


@dataclass
class HookResult:
    decision: str | None = None  # allow | deny | ask | None
    # decision は allow | deny | ask | None のいずれか
    reason: str = ""
    updated_input: dict[str, Any] | None = None
    additional_context: str = ""
    stop: bool = False  # stop the run now
    # stop は実行を直ちに終わらせる
    block_stop: bool = False  # for the stop event: do not let the model finish
    # block_stop は stop イベント用で、モデルに応答を終わらせない
    name: str = ""

    @classmethod
    def from_any(cls, value: Any, name: str = "") -> "HookResult":
        if value is None:
            return cls(name=name)
        if isinstance(value, HookResult):
            value.name = value.name or name
            return value
        if isinstance(value, dict):
            return cls(
                decision=value.get("decision"),
                reason=str(value.get("reason", "")),
                updated_input=value.get("updated_input"),
                additional_context=str(value.get("additional_context", "")),
                stop=bool(value.get("stop", False)),
                block_stop=bool(value.get("block_stop", False)),
                name=name,
            )
        if isinstance(value, bool):
            return cls(decision="allow" if value else "deny", name=name)
        return cls(additional_context=str(value), name=name)


@dataclass
class Hook:
    event: str
    matcher: str | None  # fnmatch on tool name, or None for all
    # matcher はツール名に対する fnmatch。None はすべてに一致する
    fn: Callable[[dict[str, Any]], Any] | None = None
    command: list[str] | None = None
    timeout: float = 60.0
    name: str = ""

    def applies(self, payload: dict[str, Any]) -> bool:
        if self.matcher is None:
            return True
        return fnmatch.fnmatchcase(str(payload.get("tool", "")), self.matcher)

    def run(self, payload: dict[str, Any], cwd: str | None = None) -> HookResult:
        if self.fn is not None:
            return HookResult.from_any(self.fn(payload), self.name)
        assert self.command
        try:
            proc = subprocess.run(
                self.command,
                input=json.dumps(payload, default=str),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            return HookResult(decision="deny", reason=f"hook {self.name} timed out", name=self.name)
        except OSError as exc:
            return HookResult(decision="deny", reason=f"hook {self.name} failed to start: {exc}", name=self.name)
        if proc.returncode == 2:
            return HookResult(decision="deny", reason=proc.stderr.strip() or "blocked by hook", block_stop=True, name=self.name)
        if proc.returncode != 0:
            # ブロックしない失敗。無視され、呼び出し側が記録する
            return HookResult(name=self.name)  # non-blocking failure: ignored, logged by caller
        out = proc.stdout.strip()
        if not out:
            return HookResult(name=self.name)
        try:
            return HookResult.from_any(json.loads(out), self.name)
        except json.JSONDecodeError:
            return HookResult(additional_context=out, name=self.name)


@dataclass
class MergedHookResult:
    decision: str | None = None
    reason: str = ""
    updated_input: dict[str, Any] | None = None
    additional_context: list[str] = field(default_factory=list)
    stop: bool = False
    block_stop: bool = False
    results: list[HookResult] = field(default_factory=list)

    @property
    def context_text(self) -> str:
        return "\n\n".join(c for c in self.additional_context if c)


class HookRegistry:
    def __init__(self, workspace: str | None = None) -> None:
        self._hooks: list[Hook] = []
        self.workspace = workspace  # command hooks run here, so relative script paths resolve
        # command フックはここで動くため、相対的なスクリプトパスが解決できる

    def on(self, event: str, fn: Callable[[dict[str, Any]], Any], *, matcher: str | None = None, name: str = "") -> None:
        if event not in Events.ALL:
            raise ValueError(f"unknown hook event {event!r}")
        self._hooks.append(Hook(event=event, matcher=matcher, fn=fn, name=name or getattr(fn, "__name__", "hook")))

    def command(self, event: str, argv: list[str], *, matcher: str | None = None, timeout: float = 60.0, name: str = "") -> None:
        if event not in Events.ALL:
            raise ValueError(f"unknown hook event {event!r}")
        self._hooks.append(Hook(event=event, matcher=matcher, command=list(argv), timeout=timeout, name=name or argv[0]))

    def has(self, event: str) -> bool:
        return any(h.event == event for h in self._hooks)

    def run(self, event: str, payload: dict[str, Any]) -> MergedHookResult:
        merged = MergedHookResult()
        for h in self._hooks:
            if h.event != event or not h.applies(payload):
                continue
            try:
                r = h.run(payload, cwd=self.workspace)
            except Exception as exc:  # a crashing hook must not crash the run; it blocks instead
                # 例外で落ちるフックが実行全体を落としてはならない。代わりにブロックする
                r = HookResult(decision="deny", reason=f"hook {h.name} raised: {exc}", name=h.name)
            merged.results.append(r)
            if r.decision == "deny" or (merged.decision != "deny" and r.decision == "ask") or (
                merged.decision is None and r.decision == "allow"
            ):
                merged.decision = r.decision
                merged.reason = r.reason or merged.reason
            if r.updated_input:
                merged.updated_input = r.updated_input
            if r.additional_context:
                merged.additional_context.append(r.additional_context)
            merged.stop = merged.stop or r.stop
            merged.block_stop = merged.block_stop or r.block_stop
            if (r.block_stop or r.stop) and r.reason and not merged.reason:
                merged.reason = r.reason
        return merged
