"""Stuck detection.

Three signals, each cheap:
- the same tool call with the same input, repeated `repeat_limit` times in a row
- `no_evidence_turns` turns without any new evidence
- cost above the configured budget

The first two produce a nudge (a meta user message) before they produce a
stop, because the model can usually recover once told.

スタック検出。

兆候は3つあり、どれも安価である。
- 同じ入力の同じツール呼び出しが `repeat_limit` 回続く
- 新しい証跡のないターンが `no_evidence_turns` 回続く
- コストが設定された予算を超える

最初の2つは、停止させる前にまず促し（メタのユーザメッセージ）を出す。言われればモデルは
たいてい立て直せるからである。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from ..core.messages import ToolUseBlock


def call_signature(call: ToolUseBlock) -> str:
    raw = json.dumps({"n": call.name, "i": call.input}, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class StuckDetector:
    repeat_limit: int = 3
    no_evidence_turns: int = 12
    max_cost_usd: float | None = None
    nudged_repeat: bool = False
    nudged_evidence: bool = False
    history: list[str] = field(default_factory=list)

    def observe(self, calls: list[ToolUseBlock], state: Any, cost_usd: float) -> tuple[str | None, str | None]:
        """Return (nudge_text, stop_reason). Either may be None.

        (nudge_text, stop_reason) を返す。どちらも None になりうる。
        """
        if self.max_cost_usd is not None and cost_usd > self.max_cost_usd:
            return None, f"cost {cost_usd:.2f} USD exceeded the budget of {self.max_cost_usd:.2f} USD"

        for c in calls:
            self.history.append(call_signature(c))
        self.history = self.history[-50:]
        if len(self.history) >= self.repeat_limit:
            tail = self.history[-self.repeat_limit :]
            if len(set(tail)) == 1 and calls:
                if self.nudged_repeat:
                    return None, f"the same tool call was repeated {self.repeat_limit * 2} times with identical input"
                self.nudged_repeat = True
                self.history.clear()
                return (
                    f"You have made the same tool call {self.repeat_limit} times with identical input. "
                    "It will not produce a different result. Change the approach or explain why you are blocked.",
                    None,
                )

        if state.turns_without_evidence >= self.no_evidence_turns:
            if self.nudged_evidence:
                return None, f"{state.turns_without_evidence} turns without new evidence of progress"
            self.nudged_evidence = True
            state.turns_without_evidence = 0
            return (
                "Many turns have passed without a file written, a verifier run, or a phase change. "
                "State what is blocking you, or write the intermediate result to disk now.",
                None,
            )
        return None, None
