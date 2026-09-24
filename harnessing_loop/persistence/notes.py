"""Readers for the files the planning tools write.

計画用のツールが書くファイルの読み取り。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_notes(workspace: Path, max_chars: int = 20_000) -> str:
    p = Path(workspace) / ".harness" / "notes.md"
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def read_todos(workspace: Path) -> list[dict[str, Any]]:
    p = Path(workspace) / ".harness" / "todo.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
