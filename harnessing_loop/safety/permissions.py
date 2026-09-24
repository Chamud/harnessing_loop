"""The permission decision.

Modes:
    default       read-only tools run; anything else needs an allow rule or a
                  yes from the ask handler
    accept_edits  like default, but file edits inside the workspace run
    plan          read-only tools only; everything else is denied
    bypass        everything runs, except the bypass-immune checks below
    dont_ask      like default, but "ask" resolves to deny (unattended runs)

Order of the decision (first match wins):
    1. deny rule for the tool                       -> deny
    2. content-specific ask rule                    -> ask   (bypass-immune)
    3. protected path guard                         -> ask   (bypass-immune)
    4. the tool's own check_permissions             -> its answer, if not passthrough
    5. mode bypass                                  -> allow
    6. blanket ask rule                             -> ask
    7. allow rule                                   -> allow
    8. mode-specific default (read-only, edits)     -> allow / deny
    9. passthrough                                  -> ask
   10. mode dont_ask turns ask into deny

An "ask" goes to the ask handler. Without a handler (unattended) it is a
deny. A hook's "allow" never overrides a deny rule (see hooks.py).

権限の判定。

モード:
    default       読み取り専用ツールは実行される。それ以外は allow ルールか
                  ask ハンドラの許可が必要
    accept_edits  default と同じだが、ワークスペース内のファイル編集は実行される
    plan          読み取り専用ツールのみ。それ以外はすべて拒否
    bypass        下記のバイパス不可の検査を除き、すべて実行される
    dont_ask      default と同じだが、"ask" は deny に解決される（無人実行）

判定の順序（最初に一致したものが決まる）:
    1. そのツールへの deny ルール                -> deny
    2. 内容依存の ask ルール                     -> ask   (バイパス不可)
    3. 保護パスのガード                          -> ask   (バイパス不可)
    4. ツール自身の check_permissions            -> passthrough でなければその答え
    5. モードによるバイパス                      -> allow
    6. 包括的な ask ルール                       -> ask
    7. allow ルール                              -> allow
    8. モード固有の既定（読み取り専用、編集）    -> allow / deny
    9. passthrough                               -> ask
   10. モード dont_ask は ask を deny に変える

"ask" は ask ハンドラに渡る。ハンドラがない（無人実行）場合は deny になる。
フックの "allow" が deny ルールを覆すことはない（hooks.py を参照）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .guards import is_inside, is_protected_path
from .rules import Rule

PermissionMode = str  # default | accept_edits | plan | bypass | dont_ask
# 権限モードは default | accept_edits | plan | bypass | dont_ask のいずれか
MODES = ("default", "accept_edits", "plan", "bypass", "dont_ask")


@dataclass
class Decision:
    behavior: str  # allow | deny | ask
    # behavior は allow | deny | ask のいずれか
    reason: str = ""
    updated_input: dict[str, Any] | None = None
    immune: bool = False  # cannot be overridden by bypass or hooks
    # immune が True の判定は bypass モードやフックでは上書きできない

    @classmethod
    def allow(cls, reason: str = "") -> "Decision":
        return cls("allow", reason)

    @classmethod
    def deny(cls, reason: str = "", immune: bool = False) -> "Decision":
        return cls("deny", reason, immune=immune)

    @classmethod
    def ask(cls, reason: str = "", immune: bool = False) -> "Decision":
        return cls("ask", reason, immune=immune)

    @classmethod
    def passthrough(cls) -> "Decision":
        return cls("passthrough")


AskHandler = Callable[[str, dict[str, Any], str], bool]


@dataclass
class PermissionContext:
    mode: PermissionMode = "default"
    rules: list[Rule] = field(default_factory=list)
    workspace: Path = field(default_factory=Path.cwd)
    ask_handler: AskHandler | None = None
    allow_unsafe_paths: bool = False
    log: list[dict[str, Any]] = field(default_factory=list)

    def rules_for(self, tool: str, behavior: str) -> list[Rule]:
        return [r for r in self.rules if r.tool == tool and r.behavior == behavior]


def decide(tool: Any, input: dict[str, Any], ctx: PermissionContext, *, hook_allow: bool = False) -> Decision:
    """Produce the final decision for one tool call. `tool` follows tools.base.Tool.

    `hook_allow` means a pre-tool hook approved the call. It resolves an
    "ask" without consulting the handler, but never a deny or an immune ask.

    1回のツール呼び出しに対する最終判定を出す。`tool` は tools.base.Tool に従う。

    `hook_allow` はツール実行前フックがその呼び出しを承認したことを表す。これは
    ハンドラに問い合わせずに "ask" を解決するが、deny やバイパス不可の ask を
    解決することは決してない。
    """
    ctx = replace_handler(ctx) if hook_allow else ctx
    name = tool.name
    subjects = list(tool.permission_subjects(input))
    paths = [_resolve(p, ctx.workspace) for p in tool.paths(input) if p]

    # 1. deny rules
    # 1. deny ルール
    for r in ctx.rules_for(name, "deny"):
        if r.matches(name, subjects):
            return _log(ctx, name, Decision.deny(f"denied by rule {name}({r.pattern or ''})", immune=True))

    # 2. content-specific ask rules survive bypass
    # 2. 内容依存の ask ルールは bypass でも生き残る
    for r in ctx.rules_for(name, "ask"):
        if r.content_specific and r.matches(name, subjects):
            return _log(ctx, name, _ask(ctx, name, input, f"ask rule {name}({r.pattern})", immune=True))

    # 3. protected paths survive bypass
    # 3. 保護パスは bypass でも生き残る
    if not ctx.allow_unsafe_paths and not tool.is_read_only(input):
        for p in paths:
            why = is_protected_path(p, ctx.workspace)
            if why:
                return _log(ctx, name, _ask(ctx, name, input, why, immune=True))

    # 4. the tool's own view
    # 4. ツール自身の見解
    own = tool.check_permissions(input, ctx)
    if own.behavior == "deny":
        return _log(ctx, name, own)
    if own.behavior == "allow":
        return _log(ctx, name, own)
    if own.behavior == "ask" and ctx.mode != "bypass":
        return _log(ctx, name, _ask(ctx, name, input, own.reason))

    # 5. bypass
    # 5. バイパス
    if ctx.mode == "bypass":
        return _log(ctx, name, Decision.allow("bypass mode"))

    # 6. blanket ask rule
    # 6. 包括的な ask ルール
    for r in ctx.rules_for(name, "ask"):
        if not r.content_specific:
            return _log(ctx, name, _ask(ctx, name, input, f"ask rule {name}"))

    # 7. allow rules
    # 7. allow ルール
    for r in ctx.rules_for(name, "allow"):
        if r.matches(name, subjects):
            return _log(ctx, name, Decision.allow(f"allowed by rule {name}({r.pattern or ''})"))

    # 8. mode defaults
    # 8. モードごとの既定
    if tool.is_read_only(input):
        return _log(ctx, name, Decision.allow("read-only"))
    if ctx.mode == "plan":
        return _log(ctx, name, Decision.deny("plan mode: read-only tools only"))
    if ctx.mode == "accept_edits" and tool.category == "edit":
        if all(is_inside(p, ctx.workspace) for p in paths) and paths:
            return _log(ctx, name, Decision.allow("accept_edits: edit inside workspace"))

    # 9. passthrough -> ask
    # 9. passthrough は ask になる
    return _log(ctx, name, _ask(ctx, name, input, f"{name} needs approval"))


def replace_handler(ctx: PermissionContext) -> PermissionContext:
    """A copy of the context whose ask handler approves non-immune asks (hook allow).

    ask ハンドラがバイパス不可でない ask を承認するようにしたコンテキストの複製
    （フックによる allow）。
    """
    original = ctx.ask_handler

    def handler(name: str, input: dict[str, Any], reason: str) -> bool:
        return True

    copy = PermissionContext(mode=ctx.mode, rules=ctx.rules, workspace=ctx.workspace, ask_handler=handler, allow_unsafe_paths=ctx.allow_unsafe_paths, log=ctx.log)
    copy._immune_handler = original  # type: ignore[attr-defined]
    return copy


def _resolve(p: str, workspace: Path) -> Path:
    path = Path(p).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path


def _ask(ctx: PermissionContext, name: str, input: dict[str, Any], reason: str, immune: bool = False) -> Decision:
    if ctx.mode == "dont_ask" and not immune:
        return Decision.deny(f"{reason} (dont_ask mode)")
    handler = ctx.ask_handler
    if immune and hasattr(ctx, "_immune_handler"):
        handler = getattr(ctx, "_immune_handler")  # a hook allow never answers an immune ask
        # フックによる allow がバイパス不可の ask に答えることはない
    if handler is None:
        return Decision.deny(f"{reason} (no ask handler; unattended run)", immune=immune)
    try:
        ok = bool(handler(name, input, reason))
    except Exception as exc:  # a broken handler is a deny, never an allow
        # 壊れたハンドラは deny になり、allow になることはない
        return Decision.deny(f"ask handler failed: {exc}", immune=immune)
    return Decision.allow(f"approved: {reason}") if ok else Decision.deny(f"declined: {reason}", immune=immune)


def _log(ctx: PermissionContext, name: str, d: Decision) -> Decision:
    ctx.log.append({"tool": name, "behavior": d.behavior, "reason": d.reason})
    return d
