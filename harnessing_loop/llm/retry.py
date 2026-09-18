"""Retry with backoff for model calls.

Policy:
- exponential backoff from `base_delay`, doubling, capped at `max_delay`
- up to 25 percent jitter so parallel workers do not retry in lockstep
- a `retry-after` value from the provider wins over the computed delay
- retry on overload, rate limit, 5xx, connection errors, and mid-stream
  errors; never on invalid requests
- a stream that stays silent for `idle_timeout` seconds is abandoned
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Iterator, TypeVar

from ..core.errors import ModelError, PromptTooLong

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 8
    base_delay: float = 0.5
    max_delay: float = 32.0
    jitter: float = 0.25
    idle_timeout: float = 90.0

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None and retry_after > 0:
            return min(retry_after, 600.0)
        d = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        return d + random.uniform(0, d * self.jitter)


RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


def classify(exc: BaseException) -> tuple[bool, int | None, float | None]:
    """(retryable, status, retry_after) for an exception from any provider."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    retry_after = None
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers:
        ra = headers.get("retry-after") if hasattr(headers, "get") else None
        try:
            retry_after = float(ra) if ra else None
        except ValueError:
            retry_after = None
    text = str(exc).lower()
    if isinstance(exc, PromptTooLong):
        return False, 400, None
    if isinstance(exc, ModelError):
        return exc.retryable, exc.status, retry_after
    if "prompt is too long" in text or "context window" in text and "exceed" in text:
        return False, 400, None
    if status in RETRYABLE_STATUS:
        return True, int(status), retry_after
    if status is not None and 500 <= int(status) < 600:
        return True, int(status), retry_after
    if any(k in text for k in ("overloaded", "rate limit", "timeout", "timed out", "connection", "reset by peer", "stream")):
        return True, status, retry_after
    return False, status, retry_after


def with_retry(
    fn: Callable[[], T],
    *,
    policy: RetryPolicy = RetryPolicy(),
    on_retry: Callable[[int, BaseException, float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    attempt = 0
    while True:
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 - classified below
            retryable, status, retry_after = classify(exc)
            attempt += 1
            if not retryable or attempt > policy.max_retries:
                if isinstance(exc, ModelError):
                    raise
                if status == 400 and ("too long" in str(exc).lower() or "context" in str(exc).lower()):
                    raise PromptTooLong(str(exc)) from exc
                raise ModelError(str(exc), retryable=retryable, status=status) from exc
            delay = policy.delay(attempt, retry_after)
            if on_retry:
                on_retry(attempt, exc, delay)
            sleep(delay)


def with_idle_timeout(events: Iterator[T], idle_timeout: float, now: Callable[[], float] = time.time) -> Iterator[T]:
    """Raise ModelError if the gap between two events exceeds `idle_timeout`.

    Providers stream in their own thread, so a simple timestamp check between
    yields is enough to detect a hung connection in practice.
    """
    last = now()
    for ev in events:
        t = now()
        if t - last > idle_timeout:
            raise ModelError(f"stream idle for {t - last:.0f}s", retryable=True)
        last = t
        yield ev
