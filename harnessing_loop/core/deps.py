"""Dependencies the loop receives instead of importing.

Injecting these keeps the loop testable: the fake model, a fixed clock, and
deterministic ids make every transition reproducible.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .messages import new_id


@dataclass
class Deps:
    now: Callable[[], float] = time.time
    uuid: Callable[[str], str] = new_id
    sleep: Callable[[float], None] = time.sleep
    tool_result_hook: Callable | None = field(default=None)  # test-only interception

    @classmethod
    def deterministic(cls) -> "Deps":
        counter = {"n": 0}
        clock = {"t": 1_700_000_000.0}

        def _uuid(prefix: str = "") -> str:
            counter["n"] += 1
            return f"{prefix}{counter['n']:06d}"

        def _now() -> float:
            clock["t"] += 1.0
            return clock["t"]

        return cls(now=_now, uuid=_uuid, sleep=lambda s: None)
