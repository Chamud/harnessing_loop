"""Tool result size control.

Per call: if a result exceeds the tool's cap (clamped to the config cap),
the full text is written to `.harness/tool-results/<id>.txt` and the model
receives a short preview plus the path. The model can read the file with
`read_file` if it needs more.

Per message: if all results in one tool-result message together exceed the
message budget, the largest ones are persisted until the sum fits.

Empty results get a marker. Some models react badly to an empty block.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.messages import ToolResultBlock

EMPTY_MARKER = "(no output)"
CLEARED_MARKER = "[old tool result cleared]"


def persist_dir(workspace: Path) -> Path:
    d = workspace / ".harness" / "tool-results"
    d.mkdir(parents=True, exist_ok=True)
    return d


def persist(workspace: Path, call_id: str, text: str) -> Path:
    path = persist_dir(workspace) / f"{call_id}.txt"
    if not path.exists():  # a replay must not rewrite
        path.write_text(text, encoding="utf-8")
    return path


def large_result_text(size: int, path: Path, preview: str, workspace: Path) -> str:
    try:
        rel = path.relative_to(workspace).as_posix()
    except ValueError:
        rel = str(path)
    return (
        f"<persisted_output>\nOutput too large ({size:,} chars). Full output saved to: {rel}\n\n"
        f"Preview (first {len(preview):,} chars):\n{preview}\n</persisted_output>"
    )


def cap_for(tool: Any, config: Any) -> int:
    declared = getattr(tool, "max_result_chars", None)
    default = getattr(config, "tool_result_max_chars", 50_000)
    if declared is None:
        return default
    if declared == 0:
        return 0  # never persist (the tool bounds itself)
    return min(declared, default)


def size_control(text: str, *, tool: Any, call_id: str, workspace: Path, config: Any) -> str:
    if not text.strip():
        return EMPTY_MARKER
    cap = cap_for(tool, config)
    if cap and len(text) > cap:
        path = persist(workspace, call_id, text)
        preview_len = getattr(config, "tool_result_preview_chars", 2_000)
        return large_result_text(len(text), path, text[:preview_len], workspace)
    return text


def apply_message_budget(blocks: list[ToolResultBlock], *, workspace: Path, config: Any) -> list[ToolResultBlock]:
    budget = getattr(config, "tool_results_budget_per_message", 200_000)
    total = sum(len(b.content) for b in blocks)
    if total <= budget:
        return blocks
    order = sorted(range(len(blocks)), key=lambda i: -len(blocks[i].content))
    out = list(blocks)
    preview_len = getattr(config, "tool_result_preview_chars", 2_000)
    for i in order:
        if total <= budget:
            break
        b = out[i]
        if b.content.startswith("<persisted_output>"):
            continue
        path = persist(workspace, b.tool_use_id, b.content)
        replaced = large_result_text(len(b.content), path, b.content[:preview_len], workspace)
        total -= len(b.content) - len(replaced)
        out[i] = ToolResultBlock(b.tool_use_id, replaced, b.is_error)
    return out
