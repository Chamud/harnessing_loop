"""Dispatching a batch of tool calls from one assistant message.

Consecutive calls that are all concurrency-safe run in parallel, capped by
the config. Anything else runs one at a time, in order. Results come back
in the original order regardless of how they ran.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ..core.messages import ToolResultBlock, ToolUseBlock
from .base import ToolContext
from .pipeline import run_tool_call


def _is_safe(call: ToolUseBlock, ctx: ToolContext) -> bool:
    t = ctx.registry.get(call.name) if ctx.registry else None
    if t is None:
        return False
    try:
        return bool(t.is_concurrency_safe(call.input))
    except Exception:
        return False


def partition(calls: list[ToolUseBlock], ctx: ToolContext) -> list[tuple[bool, list[ToolUseBlock]]]:
    batches: list[tuple[bool, list[ToolUseBlock]]] = []
    for c in calls:
        safe = _is_safe(c, ctx)
        if safe and batches and batches[-1][0]:
            batches[-1][1].append(c)
        else:
            batches.append((safe, [c]))
    return batches


def run_tool_calls(
    calls: list[ToolUseBlock],
    ctx: ToolContext,
    *,
    should_abort: Callable[[], bool] | None = None,
    runner: Callable[[ToolUseBlock, ToolContext], ToolResultBlock] = run_tool_call,
) -> list[ToolResultBlock]:
    results: dict[str, ToolResultBlock] = {}
    cap = max(1, getattr(ctx.config, "max_tool_concurrency", 10))
    for safe, batch in partition(calls, ctx):
        if should_abort and should_abort():
            for c in batch:
                results[c.id] = ToolResultBlock(c.id, "Run was cancelled before this tool ran.", is_error=True)
            continue
        if safe and len(batch) > 1:
            with ThreadPoolExecutor(max_workers=min(cap, len(batch))) as pool:
                for c, r in zip(batch, pool.map(lambda c: runner(c, ctx), batch)):
                    results[c.id] = r
        else:
            for c in batch:
                results[c.id] = runner(c, ctx)
    return [results[c.id] for c in calls]
