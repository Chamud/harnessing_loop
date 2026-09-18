"""Message and content-block types, plus the repairs the loop relies on.

The conversation is a list of Message objects. Each message holds content
blocks: text, thinking, tool_use, tool_result. The API shape is produced by
`to_api()` and nothing else touches the wire format.

Repairs:
- `repair_orphans()` gives every tool_use without a tool_result a synthetic
  error result. Needed after aborts, model errors, and on resume.
- `drop_empty_assistant()` removes assistant messages that hold only
  thinking or whitespace, which are rejected by the API on resume.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:24]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ThinkingBlock:
    thinking: str
    signature: str | None = None
    type: str = "thinking"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False
    type: str = "tool_result"


@dataclass
class ImageBlock:
    media_type: str
    data_b64: str
    type: str = "image"


Block = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock | ImageBlock


@dataclass
class Message:
    role: str  # "user" | "assistant"
    content: list[Block] = field(default_factory=list)
    id: str = field(default_factory=lambda: new_id("m_"))
    parent_id: str | None = None
    usage: Usage | None = None
    stop_reason: str | None = None
    meta: bool = False  # injected by the harness, not typed by a person
    kind: str = "normal"  # "normal" | "compact_boundary" | "attachment"

    # ---- helpers ---------------------------------------------------------
    @classmethod
    def user(cls, text: str, *, meta: bool = False, kind: str = "normal") -> "Message":
        return cls(role="user", content=[TextBlock(text)], meta=meta, kind=kind)

    @classmethod
    def assistant(cls, text: str) -> "Message":
        return cls(role="assistant", content=[TextBlock(text)])

    @classmethod
    def tool_results(cls, results: Iterable[ToolResultBlock]) -> "Message":
        return cls(role="user", content=list(results))

    def text(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]

    def tool_results_blocks(self) -> list[ToolResultBlock]:
        return [b for b in self.content if isinstance(b, ToolResultBlock)]

    def is_tool_result_message(self) -> bool:
        return self.role == "user" and bool(self.content) and all(
            isinstance(b, ToolResultBlock) for b in self.content
        )


# ---- wire format ----------------------------------------------------------

def block_to_api(block: Block) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ThinkingBlock):
        d: dict[str, Any] = {"type": "thinking", "thinking": block.thinking}
        if block.signature:
            d["signature"] = block.signature
        return d
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": block.is_error,
        }
    if isinstance(block, ImageBlock):
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": block.media_type, "data": block.data_b64},
        }
    raise TypeError(f"unknown block type: {type(block)!r}")


def block_from_api(d: dict[str, Any]) -> Block:
    t = d.get("type")
    if t == "text":
        return TextBlock(d.get("text", ""))
    if t == "thinking":
        return ThinkingBlock(d.get("thinking", ""), d.get("signature"))
    if t == "tool_use":
        return ToolUseBlock(d["id"], d["name"], dict(d.get("input") or {}))
    if t == "tool_result":
        content = d.get("content", "")
        if isinstance(content, list):
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        return ToolResultBlock(d["tool_use_id"], content, bool(d.get("is_error", False)))
    if t == "image":
        src = d.get("source", {})
        return ImageBlock(src.get("media_type", "image/png"), src.get("data", ""))
    raise ValueError(f"unknown block type in data: {t!r}")


def to_api(messages: Iterable[Message]) -> list[dict[str, Any]]:
    """Messages in API shape. Thinking blocks stay in assistant turns."""
    out = []
    for m in messages:
        out.append({"role": m.role, "content": [block_to_api(b) for b in m.content]})
    return out


def message_to_record(m: Message) -> dict[str, Any]:
    return {
        "id": m.id,
        "parent_id": m.parent_id,
        "role": m.role,
        "content": [block_to_api(b) for b in m.content],
        "usage": None if m.usage is None else vars(m.usage),
        "stop_reason": m.stop_reason,
        "meta": m.meta,
        "kind": m.kind,
    }


def message_from_record(d: dict[str, Any]) -> Message:
    usage = Usage(**d["usage"]) if d.get("usage") else None
    return Message(
        role=d["role"],
        content=[block_from_api(b) for b in d.get("content", [])],
        id=d.get("id") or new_id("m_"),
        parent_id=d.get("parent_id"),
        usage=usage,
        stop_reason=d.get("stop_reason"),
        meta=bool(d.get("meta", False)),
        kind=d.get("kind", "normal"),
    )


# ---- repairs --------------------------------------------------------------

ORPHAN_TEXT = "Tool call was interrupted before it produced a result."


def repair_orphans(messages: list[Message], reason: str = ORPHAN_TEXT) -> list[Message]:
    """Ensure every tool_use is followed by a tool_result.

    Works in place on a copy. Adds one synthetic error result per orphan
    immediately after the assistant message that issued it.
    """
    out: list[Message] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        out.append(m)
        if m.role == "assistant" and m.tool_uses():
            expected = {b.id for b in m.tool_uses()}
            nxt = messages[i + 1] if i + 1 < len(messages) else None
            if nxt is not None and nxt.is_tool_result_message():
                have = {b.tool_use_id for b in nxt.tool_results_blocks()}
                missing = [tid for tid in expected if tid not in have]
                if missing:
                    nxt = Message(
                        role="user",
                        content=list(nxt.content)
                        + [ToolResultBlock(tid, reason, is_error=True) for tid in missing],
                        id=nxt.id,
                        parent_id=nxt.parent_id,
                        meta=nxt.meta,
                        kind=nxt.kind,
                    )
                out.append(nxt)
                i += 2
                continue
            out.append(
                Message(
                    role="user",
                    content=[ToolResultBlock(tid, reason, is_error=True) for tid in expected],
                    parent_id=m.id,
                    meta=True,
                )
            )
        i += 1
    return out


def drop_empty_assistant(messages: list[Message]) -> list[Message]:
    """Drop assistant messages that carry no text, no tool_use, and no image."""
    kept = []
    for m in messages:
        if m.role == "assistant":
            has_content = any(
                (isinstance(b, TextBlock) and b.text.strip()) or isinstance(b, (ToolUseBlock, ImageBlock))
                for b in m.content
            )
            if not has_content:
                continue
        kept.append(m)
    return kept


def merge_adjacent_user(messages: list[Message]) -> list[Message]:
    """The API needs strictly alternating roles. Merge neighbouring user turns."""
    out: list[Message] = []
    for m in messages:
        if out and out[-1].role == "user" and m.role == "user":
            prev = out[-1]
            out[-1] = Message(
                role="user",
                content=list(prev.content) + list(m.content),
                id=prev.id,
                parent_id=prev.parent_id,
                meta=prev.meta and m.meta,
                kind=prev.kind,
            )
        else:
            out.append(m)
    return out


def normalize_for_api(messages: list[Message]) -> list[Message]:
    """All repairs in the order the API needs them."""
    fixed = repair_orphans(messages)
    fixed = drop_empty_assistant(fixed)
    fixed = merge_adjacent_user(fixed)
    while fixed and fixed[0].role != "user":
        fixed = fixed[1:]
    return fixed
