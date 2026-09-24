"""Phase inference from evidence.

A profile can map evidence keys to phases:

    PHASE_EVIDENCE = {"build": "file_written", "verify": "verified"}

After each turn, the highest phase whose evidence key is present becomes
the current phase, but only moving forward. The model's own `set_phase`
calls are respected; inference only catches the case where the model did
the work without declaring it.

証跡からのフェーズ推定。

プロファイルは、上の `PHASE_EVIDENCE` の例のように証跡のキーをフェーズへ対応づけられる。

各ターンのあと、証跡のキーが存在する最も先のフェーズが現在のフェーズになる。ただし前に進む
方向だけである。モデル自身の `set_phase` の呼び出しは尊重される。推定は、モデルが宣言せずに
作業を終えた場合だけを捕まえる。
"""

from __future__ import annotations

from typing import Any


def infer_phase(state: Any, phases: list[str], phase_evidence: dict[str, str]) -> str | None:
    if not phases:
        return None
    cur = phases.index(state.phase) if state.phase in phases else -1
    best = cur
    for i, name in enumerate(phases):
        key = phase_evidence.get(name)
        if key and state.evidence.get(key) and i > best:
            best = i
    if best > cur:
        state.phase = phases[best]
        state.phases_seen.append(phases[best])
        return phases[best]
    return None
