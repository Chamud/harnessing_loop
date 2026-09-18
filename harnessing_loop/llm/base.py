"""The model client protocol.

A client turns a `ModelRequest` into a stream of `ModelEvent`s and finally a
`ModelResponse`. The loop never sees provider objects. Swapping providers,
or swapping in the fake, changes nothing above this line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

from ..core.messages import Block, Message, Usage


@dataclass
class ModelRequest:
    system: list[dict[str, Any]]  # system blocks in API shape, cache markers included
    messages: list[Message]
    tools: list[dict[str, Any]]  # tool schemas in API shape
    max_output_tokens: int = 16000
    thinking: str = "adaptive"  # off | adaptive | budget
    thinking_budget_tokens: int = 8000
    temperature: float | None = None
    stop_sequences: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelEvent:
    """Incremental output. `kind` is one of text, thinking, tool_use, done."""

    kind: str
    text: str = ""
    block: Block | None = None


@dataclass
class ModelResponse:
    message: Message
    usage: Usage
    stop_reason: str  # end_turn | tool_use | max_tokens | stop_sequence
    model: str


class ModelClient(Protocol):
    model: str

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]:
        """Yield events, ending with exactly one event of kind 'done'.

        The 'done' event carries the final ModelResponse in `block` position
        via the `.response` attribute of DoneEvent.
        """
        ...


@dataclass
class DoneEvent(ModelEvent):
    response: ModelResponse | None = None

    def __init__(self, response: ModelResponse):
        super().__init__(kind="done")
        self.response = response


def collect(events: Iterator[ModelEvent]) -> ModelResponse:
    """Drain a stream and return the final response. Used by tests and compaction."""
    last: ModelResponse | None = None
    for ev in events:
        if isinstance(ev, DoneEvent):
            last = ev.response
    if last is None:
        raise RuntimeError("stream ended without a done event")
    return last
