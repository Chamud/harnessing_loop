"""Microcompaction: clear old tool results, keep the conversation shape.

Old file reads, command output and search results are the bulk of a long
context and are rarely needed again verbatim. Their content is replaced by
a marker. The tool_use and tool_result blocks stay, so the structure the
API requires is intact and the model still sees what it did.

マイクロコンパクション。古いツール結果を消し、会話の形は保つ。

古いファイル読み取り、コマンド出力、検索結果は長いコンテキストの大部分を占めるが、そのままの
形で再び必要になることはまずない。その内容はマーカーに置き換えられる。tool_use と
tool_result のブロックは残るので、API が要求する構造は保たれ、モデルは自分が何をしたかを
引き続き見られる。
"""

from __future__ import annotations

from ..core.messages import Message, ToolResultBlock, ToolUseBlock
from ..tools.results import CLEARED_MARKER

COMPACTABLE = {
    "read_file",
    "shell",
    "run_python",
    "grep_files",
    "glob_files",
    "list_dir",
    "web_fetch",
    "web_search",
    "edit_file",
    "write_file",
    "task_output",
}


def microcompact(messages: list[Message], keep_recent: int = 5, compactable: set[str] = COMPACTABLE) -> tuple[list[Message], int]:
    names: dict[str, str] = {}
    for m in messages:
        for b in m.content:
            if isinstance(b, ToolUseBlock):
                names[b.id] = b.name
    candidates: list[tuple[int, int]] = []  # (message index, block index)
    # （メッセージのインデックス, ブロックのインデックス）
    for mi, m in enumerate(messages):
        for bi, b in enumerate(m.content):
            if isinstance(b, ToolResultBlock) and names.get(b.tool_use_id) in compactable:
                if b.content != CLEARED_MARKER and not b.content.startswith("<persisted_output>"):
                    candidates.append((mi, bi))
    to_clear = candidates[:-keep_recent] if keep_recent > 0 else candidates
    if not to_clear:
        return messages, 0
    out = list(messages)
    touched: dict[int, list] = {}
    for mi, bi in to_clear:
        blocks = touched.setdefault(mi, list(out[mi].content))
        old = blocks[bi]
        blocks[bi] = ToolResultBlock(old.tool_use_id, CLEARED_MARKER, old.is_error)
    for mi, blocks in touched.items():
        m = out[mi]
        out[mi] = Message(role=m.role, content=blocks, id=m.id, parent_id=m.parent_id, usage=m.usage, stop_reason=m.stop_reason, meta=m.meta, kind=m.kind)
    return out, len(to_clear)
