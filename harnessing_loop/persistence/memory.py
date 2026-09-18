"""Long-term memory: one fact per file, an index, and relevance recall.

Layout:
    <memory_dir>/MEMORY.md          index, one line per entry, bounded
    <memory_dir>/<name>.md          frontmatter + body

Frontmatter:
    ---
    name: short-slug
    description: one line used for recall
    type: user | feedback | project | reference
    ---

Recall is keyword scoring over name and description by default. A profile
can plug in a model-based selector through `selector`.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

INDEX_NAME = "MEMORY.md"
MAX_INDEX_LINES = 200
MAX_INDEX_BYTES = 25_000
MAX_FILES = 200
FRONTMATTER_LINES = 30
TYPES = ("user", "feedback", "project", "reference")


@dataclass
class MemoryEntry:
    name: str
    description: str
    type: str
    path: Path
    mtime: float

    def body(self) -> str:
        text = self.path.read_text(encoding="utf-8", errors="replace")
        parts = text.split("---", 2)
        return parts[2].strip() if len(parts) == 3 else text


def parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()[:FRONTMATTER_LINES]
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^\s*([A-Za-z_]+)\s*:\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


class MemoryDir:
    def __init__(self, path: Path, selector: Callable[[str, list[MemoryEntry], int], list[str]] | None = None):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.selector = selector

    def entries(self) -> list[MemoryEntry]:
        out = []
        for p in sorted(self.path.glob("*.md"), key=lambda p: -p.stat().st_mtime)[:MAX_FILES]:
            if p.name == INDEX_NAME:
                continue
            fm = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
            out.append(MemoryEntry(fm.get("name", p.stem), fm.get("description", ""), fm.get("type", "project"), p, p.stat().st_mtime))
        return out

    def index_text(self) -> str:
        p = self.path / INDEX_NAME
        if not p.exists():
            return ""
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[:MAX_INDEX_LINES]
        text = "\n".join(lines)
        return text[:MAX_INDEX_BYTES]

    def write(self, name: str, description: str, type: str, body: str) -> Path:
        if type not in TYPES:
            raise ValueError(f"memory type must be one of {TYPES}")
        slug = re.sub(r"[^a-z0-9\-]+", "-", name.lower()).strip("-") or f"memory-{int(time.time())}"
        p = self.path / f"{slug}.md"
        p.write_text(f"---\nname: {slug}\ndescription: {description}\ntype: {type}\n---\n\n{body.strip()}\n", encoding="utf-8")
        self._update_index(slug, description)
        return p

    def _update_index(self, slug: str, description: str) -> None:
        idx = self.path / INDEX_NAME
        line = f"- [{slug}]({slug}.md) — {description}"
        existing = idx.read_text(encoding="utf-8").splitlines() if idx.exists() else []
        existing = [l for l in existing if f"({slug}.md)" not in l]
        existing.append(line)
        idx.write_text("\n".join(existing[-MAX_INDEX_LINES:]) + "\n", encoding="utf-8")

    def relevant(self, query: str, k: int = 5) -> list[MemoryEntry]:
        entries = self.entries()
        if not entries:
            return []
        if self.selector:
            names = set(self.selector(query, entries, k))
            return [e for e in entries if e.name in names][:k]
        terms = {t for t in re.findall(r"[a-z0-9]{3,}", query.lower())}
        scored = []
        for e in entries:
            hay = f"{e.name} {e.description}".lower()
            score = sum(1 for t in terms if t in hay)
            if score:
                scored.append((score, e.mtime, e))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [e for _, _, e in scored[:k]]

    def recall_text(self, query: str, k: int = 5, max_chars: int = 6000) -> str:
        picked = self.relevant(query, k)
        if not picked:
            return ""
        parts = []
        used = 0
        for e in picked:
            body = e.body()
            chunk = f"<memory name=\"{e.name}\" type=\"{e.type}\">\n{body}\n</memory>"
            if used + len(chunk) > max_chars:
                break
            parts.append(chunk)
            used += len(chunk)
        return "\n".join(parts)
