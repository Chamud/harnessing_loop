"""Typed events emitted by the loop.

Everything a UI, a log, or a test needs to observe the run comes through
`EventBus`. The loop never prints. Subscribers decide what to show.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    turn: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


# Event types the loop emits. Kept as constants so subscribers can filter.
RUN_START = "run_start"
RUN_END = "run_end"
TURN_START = "turn_start"
TEXT_DELTA = "text_delta"
THINKING_DELTA = "thinking_delta"
ASSISTANT_MESSAGE = "assistant_message"
TOOL_START = "tool_start"
TOOL_END = "tool_end"
PERMISSION_ASK = "permission_ask"
PERMISSION_DENIED = "permission_denied"
HOOK_BLOCKED = "hook_blocked"
PHASE = "phase"
COMPACT = "compact"
MICROCOMPACT = "microcompact"
RETRY = "retry"
WARNING = "warning"
COST = "cost"
STUCK = "stuck"
CONTROL = "control"

Subscriber = Callable[[Event], None]


class EventBus:
    """Fan-out with optional JSONL persistence. Thread-safe."""

    def __init__(self, log_path: Path | None = None):
        self._subs: list[Subscriber] = []
        self._lock = threading.Lock()
        self._log_path = log_path
        self.turn = 0
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subs.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)

        return unsubscribe

    def emit(self, type: str, **data: Any) -> Event:
        ev = Event(type=type, data=data, turn=self.turn)
        with self._lock:
            subs = list(self._subs)
            if self._log_path:
                with self._log_path.open("a", encoding="utf-8") as fh:
                    fh.write(ev.to_json() + "\n")
        for s in subs:
            try:
                s(ev)
            except Exception:  # a broken subscriber must not stop the run
                pass
        return ev


def print_subscriber(ev: Event) -> None:
    """A minimal terminal renderer for examples."""
    t = ev.type
    d = ev.data
    if t == TEXT_DELTA:
        print(d.get("text", ""), end="", flush=True)
    elif t == ASSISTANT_MESSAGE:
        print()
    elif t == TOOL_START:
        print(f"\n[tool] {d.get('name')} {json.dumps(d.get('input', {}), ensure_ascii=False)[:200]}")
    elif t == TOOL_END:
        flag = "error" if d.get("is_error") else "ok"
        preview = (d.get("preview") or "").strip().replace("\n", " ")[:160]
        print(f"[tool] {d.get('name')} -> {flag} {preview}")
    elif t in (PHASE, COMPACT, MICROCOMPACT, RETRY, WARNING, STUCK, HOOK_BLOCKED, PERMISSION_DENIED):
        print(f"[{t}] {json.dumps(d, ensure_ascii=False, default=str)[:300]}")
    elif t == RUN_END:
        print(f"\n[done] {d.get('reason')} turns={d.get('turns')} cost=${d.get('cost', 0):.4f}")
