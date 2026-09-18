"""Rebuild the conversation after compaction.

Order: boundary message with the summary, then re-attached state from disk
(notes, todo list, the most recently read files), then the kept tail.

The disk state is the point. Notes and todos were written by the model as
it worked; they come back verbatim. Files come back fresh, so the model is
never editing from a stale memory of them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.messages import Message

MAX_FILES = 5
MAX_CHARS_PER_FILE = 20_000
MAX_TOTAL_CHARS = 120_000


def _read_capped(path: Path, cap: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > cap:
        return text[:cap] + f"\n... [truncated at {cap:,} chars]"
    return text


def build_post_compact(summary: str, tail: list[Message], *, workspace: Path, file_state: Any = None, transcript_path: Path | None = None, state: Any = None) -> list[Message]:
    parts = [
        "This session is being continued from a previous conversation that ran out of context. "
        "The summary below covers the earlier portion. Continue directly from the current step; "
        "do not acknowledge the summary and do not restart completed work.",
        "<summary>\n" + summary.strip() + "\n</summary>",
    ]
    if transcript_path:
        try:
            rel = transcript_path.relative_to(workspace).as_posix()
        except ValueError:
            rel = str(transcript_path)
        parts.append(f"The full transcript is at {rel} if a detail is needed.")

    total = 0
    harness = workspace / ".harness"
    notes = harness / "notes.md"
    if notes.exists():
        t = _read_capped(notes, MAX_CHARS_PER_FILE)
        total += len(t)
        parts.append("<notes>\n" + t + "\n</notes>")
    todo = harness / "todo.json"
    if todo.exists():
        t = _read_capped(todo, 8_000)
        total += len(t)
        parts.append("<todo>\n" + t + "\n</todo>")
    if state is not None:
        parts.append(f"Current phase: {getattr(state, 'phase', 'start')}. Files written so far: {', '.join(getattr(state, 'files_written', [])[-20:]) or 'none'}.")

    if file_state is not None:
        shown = set()
        for key in reversed(file_state.recent(MAX_FILES)):
            p = Path(key)
            if not p.exists() or key in shown:
                continue
            t = _read_capped(p, MAX_CHARS_PER_FILE)
            if total + len(t) > MAX_TOTAL_CHARS:
                break
            total += len(t)
            shown.add(key)
            try:
                rel = p.relative_to(workspace).as_posix()
            except ValueError:
                rel = str(p)
            parts.append(f"<file path=\"{rel}\">\n{t}\n</file>")
            file_state.record(p, p.read_text(encoding="utf-8", errors="replace"))
        for key in list(file_state.recent(1000)):
            if key not in shown:
                file_state.forget(key)

    boundary = Message.user("\n\n".join(parts), meta=True, kind="compact_boundary")
    return [boundary] + list(tail)
