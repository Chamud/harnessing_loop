"""Full compaction: replace the conversation with a structured summary.

When: the estimated context reaches `window - compact_buffer`. If it
reaches `window - blocking_buffer` and compaction is off or failing, the
run stops with `blocking_limit` instead of erroring at the API.

How: the model is asked, with no tools, to write a summary with fixed
sections. The reply is parsed, the conversation is rebuilt as
[boundary + summary] + [kept tail], and the loop continues.

The summary sections exist so nothing structural is lost: intent, files,
errors and how they were fixed, what is pending, and the exact next step.

完全なコンパクション。会話を構造化された要約に置き換える。

いつ: 推定コンテキストが `window - compact_buffer` に達したとき。`window - blocking_buffer`
に達していて、コンパクションが無効または失敗しているときは、API でエラーになる代わりに
`blocking_limit` で実行を止める。

どのように: ツールを与えずに、決まったセクションを持つ要約をモデルに書かせる。応答を解析し、
会話を [境界 + 要約] + [残したテール] として組み直し、ループを続ける。

要約のセクションは、構造的なものを何も失わないために存在する。意図、ファイル、失敗した内容と
その解決方法、保留中の作業、そして正確な次の一手である。
"""

from __future__ import annotations

import re
from typing import Any

from ..core.messages import Message, TextBlock, ToolResultBlock, ToolUseBlock
from ..llm.base import ModelRequest, collect
from .tokens import message_tokens

SUMMARY_SECTIONS = [
    "Task and intent: what the user asked for, in their words where useful",
    "Key facts and decisions: constraints, chosen approaches, numbers that matter",
    "Files: paths touched, what each contains or was changed to",
    "Errors and fixes: what failed and what resolved it",
    "Verification: what has been checked and what has not",
    "Pending work: what remains, in order",
    "Current step: exactly what was in progress when the summary was made",
    "Next action: the single next tool call or message, stated concretely",
]

NO_TOOLS_PREAMBLE = (
    "You are summarizing a working session so it can continue in a fresh context. "
    "Reply with TEXT ONLY. Do not call tools.\n\n"
)

TAIL_KEEP_TOKENS = 8_000
MAX_RESULT_CHARS_IN_PROMPT = 1_500


def should_compact(tokens: int, config: Any) -> bool:
    if not getattr(config, "compact_enabled", True):
        return False
    return tokens >= config.context_window_tokens - config.compact_buffer_tokens


def is_blocking(tokens: int, config: Any) -> bool:
    return tokens >= config.context_window_tokens - config.blocking_buffer_tokens


def render_for_summary(messages: list[Message]) -> str:
    lines: list[str] = []
    names: dict[str, str] = {}
    for m in messages:
        for b in m.content:
            if isinstance(b, ToolUseBlock):
                names[b.id] = b.name
                lines.append(f"[assistant tool_use {b.name}] {str(b.input)[:600]}")
            elif isinstance(b, ToolResultBlock):
                flag = "error" if b.is_error else "ok"
                lines.append(f"[tool_result {names.get(b.tool_use_id, '?')} {flag}] {b.content[:MAX_RESULT_CHARS_IN_PROMPT]}")
            elif isinstance(b, TextBlock) and b.text.strip():
                tag = "user" if m.role == "user" else "assistant"
                if m.meta:
                    tag += " (harness)"
                lines.append(f"[{tag}] {b.text[:4000]}")
    return "\n".join(lines)


def build_summary_prompt(messages: list[Message]) -> str:
    sections = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(SUMMARY_SECTIONS))
    return (
        NO_TOOLS_PREAMBLE
        + "Session transcript follows between <transcript> tags. Then write your summary between <summary> tags "
        + "with these numbered sections:\n"
        + sections
        + "\n\n<transcript>\n"
        + render_for_summary(messages)
        + "\n</transcript>\n\nWrite the summary now."
    )


def parse_summary(text: str) -> str:
    m = re.search(r"<summary>(.*?)</summary>", text, re.S)
    body = m.group(1) if m else text
    return body.strip()


def split_tail(messages: list[Message], keep_tokens: int = TAIL_KEEP_TOKENS) -> tuple[list[Message], list[Message]]:
    """Return (head, tail). The tail starts at a user message that is not a tool result.

    (head, tail) を返す。テールは、ツール結果ではないユーザメッセージから始まる。
    """
    budget = 0
    cut = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        budget += message_tokens(messages[i])
        if budget > keep_tokens:
            break
        m = messages[i]
        if m.role == "user" and not m.is_tool_result_message() and not m.meta:
            cut = i
    if cut == len(messages) or cut == 0:
        return messages, []
    return messages[:cut], messages[cut:]


def summarize(model: Any, messages: list[Message], config: Any, system: list[dict[str, Any]] | None = None) -> str:
    req = ModelRequest(
        system=system or [{"type": "text", "text": "You write precise handover summaries."}],
        messages=[Message.user(build_summary_prompt(messages))],
        tools=[],
        max_output_tokens=getattr(config, "compact_max_output_tokens", 20_000),
        thinking="off",
    )
    resp = collect(model.stream(req))
    return parse_summary(resp.message.text())


def compact(model: Any, messages: list[Message], config: Any, *, keep_tail: bool = True) -> tuple[str, list[Message]]:
    """Return (summary, tail_messages). Raises whatever the model client raises.

    (summary, tail_messages) を返す。モデルクライアントが投げた例外はそのまま投げる。
    """
    head, tail = split_tail(messages) if keep_tail else (messages, [])
    if not head:
        head, tail = messages, []
    summary = summarize(model, head, config)
    if not summary:
        raise RuntimeError("empty summary")
    return summary, tail
