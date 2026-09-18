"""Run state, configuration snapshot, and typed loop transitions.

`RunConfig` is read once when a run starts. Nothing reads settings from the
environment mid-run, so behaviour cannot flip between turns.

`RunState` is the mutable struct the loop carries across iterations. Tools
update it through their context when they produce evidence (a file written,
a verifier passed). The loop reads it to infer progress.

`Terminal` and `Continue` are the only two ways an iteration ends. Their
`reason` strings are the vocabulary tests assert against.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunConfig:
    model: str = "fake"
    max_turns: int = 200
    max_cost_usd: float | None = None
    max_output_tokens: int = 16000
    context_window_tokens: int = 200_000
    thinking: str = "adaptive"  # "off" | "adaptive" | "budget"
    thinking_budget_tokens: int = 8000
    temperature: float | None = None
    # context management
    compact_enabled: bool = True
    compact_buffer_tokens: int = 13_000
    blocking_buffer_tokens: int = 3_000
    compact_max_output_tokens: int = 20_000
    compact_max_failures: int = 3
    microcompact_keep_recent: int = 5
    microcompact_min_tokens: int = 40_000
    # tools
    tool_result_max_chars: int = 50_000
    tool_results_budget_per_message: int = 200_000
    tool_result_preview_chars: int = 2_000
    max_tool_concurrency: int = 10
    # recovery
    max_output_recovery_attempts: int = 3
    # stuck detection
    stuck_repeat_limit: int = 3
    stuck_no_evidence_turns: int = 12
    # sandbox
    allow_unsafe_local: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunState:
    workspace: Path
    turn: int = 0
    phase: str = "start"
    phases_seen: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    files_written: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    todos: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    verify: dict[str, Any] | None = None
    last_tool_calls: list[tuple[str, str]] = field(default_factory=list)  # (name, input-hash)
    turns_without_evidence: int = 0
    compactions: int = 0
    compact_failures: int = 0
    output_recovery_attempts: int = 0
    stop_hook_active: bool = False
    started_at: float = field(default_factory=time.time)
    dynamic_tools: list[str] = field(default_factory=list)
    last_usage_total: int = 0

    def add_evidence(self, key: str, value: Any = True) -> None:
        self.evidence[key] = value
        self.turns_without_evidence = 0

    def snapshot(self) -> dict[str, Any]:
        d = {k: v for k, v in vars(self).items() if k not in ("workspace", "last_tool_calls")}
        d["workspace"] = str(self.workspace)
        return d


@dataclass
class Terminal:
    reason: str  # completed | incomplete | max_turns | aborted | blocking_limit | prompt_too_long | hook_prevented | model_error | budget_exceeded | stuck
    turns: int
    message: str = ""
    final_text: str = ""

    def __bool__(self) -> bool:
        return self.reason == "completed"


@dataclass
class Continue:
    reason: str  # tool_use | output_limit_recovery | stop_hook_blocking | compact_retry | budget_continuation


TERMINAL_REASONS = {
    "completed",
    "incomplete",
    "max_turns",
    "aborted",
    "blocking_limit",
    "prompt_too_long",
    "hook_prevented",
    "model_error",
    "budget_exceeded",
    "stuck",
}
CONTINUE_REASONS = {
    "tool_use",
    "output_limit_recovery",
    "stop_hook_blocking",
    "compact_retry",
    "budget_continuation",
}
