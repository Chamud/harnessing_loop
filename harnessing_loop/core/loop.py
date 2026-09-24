"""The loop.

One iteration:

    control file      cancel / pause / operator messages
    context           microcompact, then full compaction if near the limit
    model call        stream, retry inside the client
    output limit      recover from a truncated reply, a few times
    no tool calls?    stop hooks, finish requirement, then terminal
    tool calls        dispatch, size budget, stuck check, reminders
    progress          phase inference, evidence bookkeeping
    limits            max turns, cost budget

The loop-back signal is the presence of a tool_use block, never the
provider's stop reason. Every exit is a `Terminal` with a reason string.

ループ本体。

1回の反復は次のとおりである。

    control file      取り消し / 一時停止 / 運用者からのメッセージ
    context           マイクロコンパクション、上限が近ければ完全なコンパクション
    model call        ストリーミング。再試行はクライアント内部で行う
    output limit      切り詰められた応答から、数回まで回復する
    no tool calls?    stop フック、完了要求の確認、そのあと終了
    tool calls        ディスパッチ、サイズ予算、スタック検査、リマインダ
    progress          フェーズの推定、証跡の記録
    limits            最大ターン数、コスト予算

ループが次の周に戻る合図は `tool_use` ブロックの存在であり、提供元が返す停止理由
ではない。すべての出口は理由文字列を持つ `Terminal` である。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..context.attachments import AttachmentState, build_attachments
from ..context.compact import compact, is_blocking, should_compact
from ..context.microcompact import microcompact
from ..context.rebuild import build_post_compact
from ..context.system_prompt import dynamic_prompt as default_dynamic_prompt
from ..context.tokens import conversation_tokens, estimate_tokens
from ..llm.base import DoneEvent, ModelRequest, ModelResponse
from ..llm.cache_layout import system_blocks
from ..persistence.control import read_control, write_control
from ..progress.evidence import infer_phase
from ..progress.stuck import StuckDetector
from ..safety.hooks import Events
from ..tools.base import ToolContext
from ..tools.dispatch import run_tool_calls
from ..tools.results import apply_message_budget
from . import events as ev
from .cost import CostTracker
from .errors import Aborted, ModelError, PromptTooLong
from .messages import Message, TextBlock, ToolResultBlock, normalize_for_api
from .runtime import Runtime
from .state import Continue, RunState, Terminal

OUTPUT_LIMIT_TEXT = (
    "Your previous reply hit the output token limit. Resume directly from where it stopped. "
    "No apology, no recap. Break remaining work into smaller pieces."
)
FINISH_NUDGE_TEXT = (
    "You ended your turn without calling finish. If the job is complete, call finish with a summary. "
    "If it is not, continue working."
)


class Loop:
    def __init__(self, runtime: Runtime, *, state: RunState | None = None, messages: list[Message] | None = None):
        self.rt = runtime
        self.config = runtime.config
        self.state = state or runtime.new_state()
        self.messages: list[Message] = messages or []
        self.cost = CostTracker()
        self.stuck = StuckDetector(
            repeat_limit=self.config.stuck_repeat_limit,
            no_evidence_turns=self.config.stuck_no_evidence_turns,
            max_cost_usd=self.config.max_cost_usd,
        )
        self.attach = AttachmentState()
        self.tasks: dict[str, Any] = {}
        self.transitions: list[Continue | Terminal] = []
        self._finish_nudges = 0
        self._compacted_for_ptl = False
        self.ctx = ToolContext(
            workspace=runtime.workspace,
            state=self.state,
            config=self.config,
            sandbox=runtime.sandbox,
            file_state=runtime.file_state,
            events=runtime.events,
            registry=runtime.registry,
            permissions=runtime.permissions,
            hooks=runtime.hooks,
            redactor=runtime.redactor,
            model=runtime.model,
            gates=runtime.gates,
            checkpoints=runtime.checkpoints,
            deps=runtime.deps,
            tasks=self.tasks,
            extra={**runtime.extra, "runtime": runtime},
            depth=runtime.depth,
        )
        if hasattr(runtime.model, "on_retry"):
            runtime.model.on_retry = self._on_retry

    # ---- public --------------------------------------------------------------------
    # ---- 公開 ----------------------------------------------------------------------
    @classmethod
    def resume(cls, runtime: Runtime) -> "Loop":
        """Continue a run from its transcript and saved state.

        トランスクリプトと保存された状態から実行を継続する。
        """
        if runtime.transcript is None:
            raise ValueError("resume needs a runtime with a transcript")
        messages = runtime.transcript.load_live()
        state = runtime.new_state()
        _load_state(state, runtime.workspace)
        loop = cls(runtime, state=state, messages=messages)
        return loop

    def run(self, prompt: str | None = None) -> Terminal:
        bus = self.rt.events
        bus.emit(ev.RUN_START, profile=self.rt.profile_name, model=getattr(self.rt.model, "model", "?"), prompt=(prompt or "")[:500], resume=prompt is None)
        if self.rt.transcript:
            self.rt.transcript.meta("run_start", prompt=prompt, resume=prompt is None, profile=self.rt.profile_name)
        hooks = self.rt.hooks
        if hooks and hooks.has(Events.SESSION_START):
            merged = hooks.run(Events.SESSION_START, {"workspace": str(self.rt.workspace), "profile": self.rt.profile_name})
            if merged.context_text:
                self._append(Message.user(merged.context_text, meta=True, kind="attachment"))
        if prompt is not None:
            text = prompt
            if hooks and hooks.has(Events.USER_PROMPT):
                merged = hooks.run(Events.USER_PROMPT, {"prompt": prompt})
                if merged.stop:
                    return self._end(Terminal("hook_prevented", 0, merged.reason))
                if merged.context_text:
                    text = prompt + "\n\n<system_reminder>\n" + merged.context_text + "\n</system_reminder>"
            if self.rt.memory is not None:
                recall = self.rt.memory.recall_text(prompt)
                if recall:
                    text += "\n\n<system_reminder>\nRelevant memory:\n" + recall + "\n</system_reminder>"
            self._append(Message.user(text))
        elif not self.messages:
            return self._end(Terminal("completed", 0, "nothing to resume"))
        elif self.messages[-1].role == "assistant" and not self.messages[-1].tool_uses():
            self._append(Message.user("Continue from where you left off.", meta=True))

        try:
            while True:
                result = self._iteration()
                self.transitions.append(result)
                if isinstance(result, Terminal):
                    return self._end(result)
        except Aborted as exc:
            return self._end(Terminal("aborted", self.state.turn, str(exc)))

    # ---- one iteration ---------------------------------------------------------------
    # ---- 1回の反復 -------------------------------------------------------------------
    def _iteration(self) -> Continue | Terminal:
        st = self.state
        st.turn += 1
        self.rt.events.turn = st.turn
        st.turns_without_evidence += 1
        if self.rt.checkpoints:
            self.rt.checkpoints.snapshot(st.turn)

        # control file
        # 制御ファイル
        inbox = self._control()

        # context management
        # コンテキスト管理
        blocked = self._manage_context()
        if blocked is not None:
            return blocked

        # model
        # モデル呼び出し
        try:
            response = self._call_model()
        except PromptTooLong:
            if not self._compacted_for_ptl and self.config.compact_enabled:
                self._compacted_for_ptl = True
                if self._compact(force=True):
                    return Continue("compact_retry")
            return Terminal("prompt_too_long", st.turn, "request exceeds the context window and compaction did not help")
        except ModelError as exc:
            return Terminal("model_error", st.turn, str(exc))

        msg = response.message
        self.cost.add(response.model, response.usage)
        st.last_usage_total = response.usage.total
        self.rt.events.emit(ev.COST, **self.cost.snapshot())
        self._append(msg)
        self.rt.events.emit(ev.ASSISTANT_MESSAGE, text=msg.text()[:2000], tool_uses=[t.name for t in msg.tool_uses()], stop_reason=response.stop_reason)

        tool_uses = msg.tool_uses()

        # truncated reply without tool calls: ask for a continuation
        # ツール呼び出しのない切り詰められた応答: 続きを書くよう求める。
        if response.stop_reason == "max_tokens" and not tool_uses:
            if st.output_recovery_attempts < self.config.max_output_recovery_attempts:
                st.output_recovery_attempts += 1
                self.rt.events.emit(ev.WARNING, kind="output_limit", attempt=st.output_recovery_attempts)
                self._append(Message.user(OUTPUT_LIMIT_TEXT, meta=True))
                return Continue("output_limit_recovery")
            return Terminal("model_error", st.turn, "output limit hit repeatedly")

        if not tool_uses:
            return self._on_model_stop(msg)

        # tools
        # ツール
        control = read_control(self.rt.workspace)
        results = run_tool_calls(tool_uses, self.ctx, should_abort=lambda: read_control(self.rt.workspace).get("cancel", False))
        results = apply_message_budget(results, workspace=self.rt.workspace, config=self.config)
        if control.get("cancel"):
            self._append(Message.tool_results(results))
            raise Aborted("cancelled during tool execution")

        # stuck detection, reminders
        # スタック検出とリマインダ
        nudge, stop_reason = self.stuck.observe(tool_uses, st, self.cost.cost_usd)
        extra_blocks: list[TextBlock] = []
        reminder = build_attachments(self.rt, st, self.attach, tasks=self.tasks, inbox=inbox)
        if reminder:
            extra_blocks.append(TextBlock(reminder))
        if nudge:
            self.rt.events.emit(ev.STUCK, nudge=nudge)
            extra_blocks.append(TextBlock("<system_reminder>\n" + nudge + "\n</system_reminder>"))
        self._append(Message(role="user", content=[*results, *extra_blocks]))

        # progress
        # 進捗
        if self.rt.gates is not None and self.rt.gates.phases:
            moved = infer_phase(st, self.rt.gates.phases, self.rt.extra.get("phase_evidence", {}))
            if moved:
                self.rt.events.emit(ev.PHASE, phase=moved, inferred=True)
        _save_state(st, self.rt.workspace)

        # exits
        # 出口
        if st.evidence.get("finished"):
            return Terminal("completed", st.turn, "finish accepted", final_text=str(st.evidence.get("final_summary", "")))
        if st.evidence.get("stop_requested"):
            return Terminal("hook_prevented", st.turn, str(st.evidence["stop_requested"]))
        if stop_reason:
            reason = "budget_exceeded" if "budget" in stop_reason else "stuck"
            return Terminal(reason, st.turn, stop_reason)
        if st.turn >= self.config.max_turns:
            return Terminal("max_turns", st.turn, f"reached max_turns={self.config.max_turns}")
        return Continue("tool_use")

    # ---- pieces ------------------------------------------------------------------------
    # ---- 部品 --------------------------------------------------------------------------
    def _on_model_stop(self, msg: Message) -> Continue | Terminal:
        st = self.state
        final_text = msg.text()
        if st.evidence.get("finished"):
            return Terminal("completed", st.turn, "finish accepted", final_text=final_text or str(st.evidence.get("final_summary", "")))
        hooks = self.rt.hooks
        if hooks and hooks.has(Events.STOP):
            merged = hooks.run(Events.STOP, {"final_text": final_text, "state": st.snapshot(), "reentry": st.stop_hook_active})
            if merged.stop:
                return Terminal("hook_prevented", st.turn, merged.reason or "stopped by hook")
            if merged.block_stop and not st.stop_hook_active:
                st.stop_hook_active = True
                self.rt.events.emit(ev.HOOK_BLOCKED, event="stop", reason=merged.reason)
                self._append(Message.user(merged.reason or "A stop hook asked you to continue.", meta=True))
                return Continue("stop_hook_blocking")
            if merged.block_stop and st.stop_hook_active:
                self.rt.events.emit(ev.WARNING, kind="stop_hook_reentry", reason=merged.reason)
        if self.rt.require_finish and "finish" in self.rt.registry:
            if self._finish_nudges < 2:
                self._finish_nudges += 1
                self._append(Message.user(FINISH_NUDGE_TEXT, meta=True))
                return Continue("stop_hook_blocking")
            return Terminal("incomplete", st.turn, "ended without calling finish", final_text=final_text)
        return Terminal("completed", st.turn, "end of turn", final_text=final_text)

    def _control(self) -> list[str]:
        ctrl = read_control(self.rt.workspace)
        if ctrl.get("cancel"):
            raise Aborted("cancelled by control file")
        if ctrl.get("pause"):
            self.rt.events.emit(ev.CONTROL, state="paused")
            while True:
                self.rt.deps.sleep(1.0)
                ctrl = read_control(self.rt.workspace)
                if ctrl.get("cancel"):
                    raise Aborted("cancelled while paused")
                if not ctrl.get("pause"):
                    self.rt.events.emit(ev.CONTROL, state="resumed")
                    break
        inbox = list(ctrl.get("inbox") or [])
        if inbox and len(inbox) > self.attach.inbox_seen:
            pass  # consumed by build_attachments
            # build_attachments が消費する。
        return inbox

    def _system(self) -> list[dict[str, Any]]:
        dyn_fn = self.rt.dynamic_prompt or default_dynamic_prompt
        return system_blocks(self.rt.system_prompt, dyn_fn(self.rt, self.state), ttl=self.rt.cache_ttl)

    def _tokens(self) -> int:
        sys_tokens = sum(estimate_tokens(b.get("text", "")) for b in self._system())
        tool_tokens = sum(estimate_tokens(json.dumps(s)) for s in self.rt.registry.schemas())
        return conversation_tokens(self.messages, sys_tokens, tool_tokens)

    def _manage_context(self) -> Terminal | None:
        cfg = self.config
        tokens = self._tokens()
        if tokens >= cfg.microcompact_min_tokens:
            self.messages, cleared = microcompact(self.messages, cfg.microcompact_keep_recent)
            if cleared:
                self.rt.events.emit(ev.MICROCOMPACT, cleared=cleared, tokens_before=tokens)
                tokens = self._tokens()
        if should_compact(tokens, cfg):
            if self.state.compact_failures >= cfg.compact_max_failures:
                if is_blocking(tokens, cfg):
                    return Terminal("blocking_limit", self.state.turn, "context full and compaction keeps failing")
                return None
            self._compact()
            return None
        if is_blocking(tokens, cfg) and not cfg.compact_enabled:
            return Terminal("blocking_limit", self.state.turn, "context full and compaction is disabled")
        return None

    def _compact(self, force: bool = False) -> bool:
        hooks = self.rt.hooks
        if hooks and hooks.has(Events.PRE_COMPACT):
            hooks.run(Events.PRE_COMPACT, {"messages": len(self.messages)})
        try:
            summary, tail = compact(self.rt.model, self.messages, self.config, keep_tail=not force)
        except Exception as exc:  # noqa: BLE001
            self.state.compact_failures += 1
            self.rt.events.emit(ev.WARNING, kind="compact_failed", error=str(exc), failures=self.state.compact_failures)
            return False
        self.state.compact_failures = 0
        self.state.compactions += 1
        transcript_path = self.rt.transcript.path if self.rt.transcript else None
        rebuilt = build_post_compact(summary, tail, workspace=self.rt.workspace, file_state=self.rt.file_state, transcript_path=transcript_path, state=self.state)
        self.messages = normalize_for_api(rebuilt)
        if self.rt.transcript:
            self.rt.transcript.boundary(self.messages[0])
        self.rt.events.emit(ev.COMPACT, summary_chars=len(summary), kept_tail=len(tail), compactions=self.state.compactions)
        if hooks and hooks.has(Events.POST_COMPACT):
            hooks.run(Events.POST_COMPACT, {"summary": summary})
        return True

    def _call_model(self) -> ModelResponse:
        request = ModelRequest(
            system=self._system(),
            messages=normalize_for_api(self.messages),
            tools=self.rt.registry.schemas(),
            max_output_tokens=self.config.max_output_tokens,
            thinking=self.config.thinking,
            thinking_budget_tokens=self.config.thinking_budget_tokens,
            temperature=self.config.temperature,
        )
        self.rt.events.emit(ev.TURN_START, tokens_estimate=self._tokens(), tools=len(request.tools))
        response: ModelResponse | None = None
        for event in self.rt.model.stream(request):
            if isinstance(event, DoneEvent):
                response = event.response
            elif event.kind == "text":
                self.rt.events.emit(ev.TEXT_DELTA, text=event.text)
            elif event.kind == "thinking":
                self.rt.events.emit(ev.THINKING_DELTA, text=event.text)
        if response is None:
            raise ModelError("stream ended without a response", retryable=True)
        return response

    def _on_retry(self, attempt: int, exc: BaseException, delay: float) -> None:
        self.rt.events.emit(ev.RETRY, attempt=attempt, error=str(exc)[:300], delay=round(delay, 2))

    def _append(self, m: Message) -> None:
        self.messages.append(m)
        if self.rt.transcript:
            self.rt.transcript.append(m)

    def _end(self, t: Terminal) -> Terminal:
        if not t.final_text:
            for m in reversed(self.messages):
                if m.role == "assistant" and m.text().strip():
                    t.final_text = m.text()
                    break
        _save_state(self.state, self.rt.workspace)
        hooks = self.rt.hooks
        if hooks and hooks.has(Events.SESSION_END):
            hooks.run(Events.SESSION_END, {"reason": t.reason, "turns": t.turns})
        if self.rt.transcript:
            self.rt.transcript.meta("run_end", reason=t.reason, turns=t.turns, cost=self.cost.snapshot())
        self.rt.events.emit(ev.RUN_END, reason=t.reason, turns=t.turns, message=t.message, cost=self.cost.cost_usd, final_text=t.final_text[:2000])
        return t

    @property
    def final_text(self) -> str:
        for m in reversed(self.messages):
            if m.role == "assistant" and m.text().strip():
                return m.text()
        return ""


# ---- state persistence ---------------------------------------------------------------
# ---- 状態の永続化 ---------------------------------------------------------------------

STATE_KEYS = ("turn", "phase", "phases_seen", "evidence", "files_written", "files_read", "todos", "notes", "compactions", "dynamic_tools")


def _save_state(state: RunState, workspace: Path) -> None:
    p = workspace / ".harness" / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {k: getattr(state, k) for k in STATE_KEYS}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=str, indent=1), encoding="utf-8")
    tmp.replace(p)


def _load_state(state: RunState, workspace: Path) -> None:
    p = workspace / ".harness" / "state.json"
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    for k in STATE_KEYS:
        if k in data:
            setattr(state, k, data[k])
    state.evidence.pop("finished", None)
    state.evidence.pop("stop_requested", None)
