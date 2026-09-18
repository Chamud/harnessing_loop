"""Prompt cache layout.

The provider caches a prefix of the request. Stable bytes at the front are
cheap on every turn. The layout is:

    tools (stable)  ->  system static (stable)  ->  system dynamic  ->  messages

Markers:
- one on the last tool schema
- one on the static system block
- one on the last message

Rules that keep the cache warm:
- tool schemas are sorted by name and never reordered mid-run
- the static system text never changes during a run
- anything that changes every turn (date, git status, reminders) goes in the
  dynamic block or in a user message, never in the static block
"""

from __future__ import annotations

from typing import Any

from ..core.messages import Message, to_api

CACHE_MARK = {"type": "ephemeral"}


def system_blocks(static_text: str, dynamic_text: str = "", ttl: str | None = None) -> list[dict[str, Any]]:
    mark = dict(CACHE_MARK)
    if ttl:
        mark["ttl"] = ttl
    blocks: list[dict[str, Any]] = [{"type": "text", "text": static_text, "cache_control": mark}]
    if dynamic_text.strip():
        blocks.append({"type": "text", "text": dynamic_text})
    return blocks


def tool_schemas_with_cache(schemas: list[dict[str, Any]], ttl: str | None = None) -> list[dict[str, Any]]:
    if not schemas:
        return schemas
    out = [dict(s) for s in sorted(schemas, key=lambda s: s["name"])]
    mark = dict(CACHE_MARK)
    if ttl:
        mark["ttl"] = ttl
    out[-1]["cache_control"] = mark
    return out


def messages_with_cache(messages: list[Message], ttl: str | None = None) -> list[dict[str, Any]]:
    api = to_api(messages)
    if not api:
        return api
    mark = dict(CACHE_MARK)
    if ttl:
        mark["ttl"] = ttl
    last = api[-1]
    # Put the marker on the last non-thinking block of the last message.
    for block in reversed(last["content"]):
        if block.get("type") not in ("thinking", "redacted_thinking"):
            block["cache_control"] = mark
            break
    return api
