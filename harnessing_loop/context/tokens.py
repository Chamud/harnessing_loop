"""Token accounting without a tokenizer.

The last model response carries exact usage for everything up to and
including that response. Messages added after it are estimated from their
size and padded by a third, so estimates err on the side of compacting
early rather than hitting the wall.

トークナイザを使わないトークン計算。

直前のモデル応答が、その応答までのすべてについて正確な使用量を持つ。そのあとに追加された
メッセージはサイズから推定し、3分の1だけ上乗せする。これにより推定は、上限に突き当たる
のではなく早めにコンパクションする側へ寄る。
"""

from __future__ import annotations

from typing import Iterable

from ..core.messages import ImageBlock, Message, TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock

CHARS_PER_TOKEN = 4
PAD = 4 / 3
IMAGE_TOKENS = 1500


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN * PAD) + 1


def message_tokens(m: Message) -> int:
    n = 0
    for b in m.content:
        if isinstance(b, TextBlock):
            n += estimate_tokens(b.text)
        elif isinstance(b, ThinkingBlock):
            n += estimate_tokens(b.thinking)
        elif isinstance(b, ToolUseBlock):
            n += estimate_tokens(str(b.input)) + 10
        elif isinstance(b, ToolResultBlock):
            n += estimate_tokens(b.content) + 10
        elif isinstance(b, ImageBlock):
            n += IMAGE_TOKENS
    return n


def conversation_tokens(messages: list[Message], system_tokens: int = 0, tool_tokens: int = 0) -> int:
    """Best estimate of the next request's input size.

    次のリクエストの入力サイズの最良推定。
    """
    last_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == "assistant" and messages[i].usage is not None:
            last_idx = i
            break
    if last_idx == -1:
        return system_tokens + tool_tokens + sum(message_tokens(m) for m in messages)
    u = messages[last_idx].usage
    base = u.input_tokens + u.cache_read_tokens + u.cache_write_tokens + u.output_tokens
    tail = sum(message_tokens(m) for m in messages[last_idx + 1 :])
    return base + tail


def total_tokens(messages: Iterable[Message]) -> int:
    return sum(message_tokens(m) for m in messages)
