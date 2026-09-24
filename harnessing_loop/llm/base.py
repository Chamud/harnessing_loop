"""The model client protocol.

A client turns a `ModelRequest` into a stream of `ModelEvent`s and finally a
`ModelResponse`. The loop never sees provider objects. Swapping providers,
or swapping in the fake, changes nothing above this line.

モデルクライアントのプロトコル。

クライアントは `ModelRequest` を `ModelEvent` の流れに変え、最後に
`ModelResponse` を返す。ループがプロバイダのオブジェクトを見ることはない。
プロバイダを差し替えても、フェイクモデルに差し替えても、この線より上は何も
変わらない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

from ..core.messages import Block, Message, Usage


@dataclass
class ModelRequest:
    system: list[dict[str, Any]]  # system blocks in API shape, cache markers included
    # API 形式のシステムブロック。キャッシュマーカーを含む
    messages: list[Message]
    tools: list[dict[str, Any]]  # tool schemas in API shape
    # API 形式のツールスキーマ
    max_output_tokens: int = 16000
    thinking: str = "adaptive"  # off | adaptive | budget
    # off | adaptive | budget のいずれか
    thinking_budget_tokens: int = 8000
    temperature: float | None = None
    stop_sequences: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelEvent:
    """Incremental output. `kind` is one of text, thinking, tool_use, done.

    逐次出力。`kind` は text、thinking、tool_use、done のいずれかである。
    """

    kind: str
    text: str = ""
    block: Block | None = None


@dataclass
class ModelResponse:
    message: Message
    usage: Usage
    stop_reason: str  # end_turn | tool_use | max_tokens | stop_sequence
    # end_turn | tool_use | max_tokens | stop_sequence のいずれか
    model: str


class ModelClient(Protocol):
    model: str

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]:
        """Yield events, ending with exactly one event of kind 'done'.

        The 'done' event carries the final ModelResponse in `block` position
        via the `.response` attribute of DoneEvent.

        イベントを次々に返し、最後に kind が 'done' のイベントをちょうど 1 つ返す。

        'done' イベントは DoneEvent の `.response` 属性を通じて、最終的な
        ModelResponse を `block` の位置で運ぶ。
        """
        ...


@dataclass
class DoneEvent(ModelEvent):
    response: ModelResponse | None = None

    def __init__(self, response: ModelResponse):
        super().__init__(kind="done")
        self.response = response


def collect(events: Iterator[ModelEvent]) -> ModelResponse:
    """Drain a stream and return the final response. Used by tests and compaction.

    ストリームを最後まで読み、最終の応答を返す。テストとコンパクションが使う。
    """
    last: ModelResponse | None = None
    for ev in events:
        if isinstance(ev, DoneEvent):
            last = ev.response
    if last is None:
        raise RuntimeError("stream ended without a done event")
    return last
