"""Per-model token and cost accumulation.

Prices are per million tokens. Unknown models cost zero, which keeps the
tracker honest: it never invents a number it cannot back.

モデルごとのトークンとコストの積み上げ。

価格は100万トークンあたりである。未知のモデルはコスト0として扱う。これにより
集計は正直なままになる。裏づけのない数値を作り出すことはない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .messages import Usage

# input, output, cache_read, cache_write in USD per 1M tokens.
# input、output、cache_read、cache_write の順。100万トークンあたりの USD である。
PRICES: dict[str, tuple[float, float, float, float]] = {
    "opus": (15.0, 75.0, 1.5, 18.75),
    "sonnet": (3.0, 15.0, 0.3, 3.75),
    "haiku": (0.8, 4.0, 0.08, 1.0),
}


def price_for(model: str) -> tuple[float, float, float, float]:
    low = model.lower()
    for key, price in PRICES.items():
        if key in low:
            return price
    return (0.0, 0.0, 0.0, 0.0)


@dataclass
class CostTracker:
    usage_by_model: dict[str, Usage] = field(default_factory=dict)
    cost_usd: float = 0.0
    calls: int = 0

    def add(self, model: str, usage: Usage) -> float:
        prev = self.usage_by_model.get(model, Usage())
        self.usage_by_model[model] = prev.add(usage)
        pi, po, pr, pw = price_for(model)
        delta = (
            usage.input_tokens * pi
            + usage.output_tokens * po
            + usage.cache_read_tokens * pr
            + usage.cache_write_tokens * pw
        ) / 1_000_000
        self.cost_usd += delta
        self.calls += 1
        return delta

    def total_usage(self) -> Usage:
        total = Usage()
        for u in self.usage_by_model.values():
            total = total.add(u)
        return total

    def snapshot(self) -> dict:
        return {
            "cost_usd": round(self.cost_usd, 6),
            "calls": self.calls,
            "usage": {k: vars(v) for k, v in self.usage_by_model.items()},
        }
