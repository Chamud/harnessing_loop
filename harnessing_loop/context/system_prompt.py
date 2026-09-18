"""System prompt assembly.

Static part (cached across turns, identical bytes every call):
    profile prompt + instruction files + skill listing + deferred tool stubs

Dynamic part (small, changes per run or per turn):
    date, workspace name, sandbox backend, phase

Instruction files are named AGENT.md. They are read from the user's home
config dir, then each directory from the filesystem root down to the
workspace, so closer files come later and carry more weight. `@path`
lines include other files, up to five levels deep.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

INSTRUCTION_FILE = "AGENT.md"
USER_CONFIG_DIR = Path.home() / ".harnessing_loop"
MAX_INCLUDE_DEPTH = 5
WARN_CHARS = 40_000
_IMPORT = re.compile(r"^@([^\s]+)\s*$", re.M)


def _expand_imports(text: str, base: Path, depth: int, seen: set[Path]) -> str:
    if depth >= MAX_INCLUDE_DEPTH:
        return text

    def repl(m: re.Match) -> str:
        target = m.group(1)
        p = Path(target).expanduser()
        if not p.is_absolute():
            p = base / p
        p = p.resolve()
        if p in seen or not p.is_file() or p.suffix.lower() not in (".md", ".txt"):
            return ""
        seen.add(p)
        try:
            inner = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return f"\n<!-- included from {p.name} -->\n" + _expand_imports(inner, p.parent, depth + 1, seen)

    return _IMPORT.sub(repl, text)


def load_instruction_files(workspace: Path) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    candidates: list[Path] = [USER_CONFIG_DIR / INSTRUCTION_FILE]
    ws = workspace.resolve()
    chain = list(reversed([ws, *ws.parents]))
    for d in chain:
        candidates.append(d / INSTRUCTION_FILE)
    for p in candidates:
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            seen.add(p.resolve())
            out.append((p, _expand_imports(text, p.parent, 0, seen)))
    return out


def instruction_text(workspace: Path) -> tuple[str, list[str]]:
    files = load_instruction_files(workspace)
    warnings = []
    parts = []
    for p, text in files:
        if len(text) > WARN_CHARS:
            warnings.append(f"{p} is {len(text):,} chars; large instruction files cost tokens every turn")
        parts.append(f"<instructions source=\"{p.name}\">\n{text.strip()}\n</instructions>")
    return "\n\n".join(parts), warnings


def assemble_static_prompt(profile_prompt: str, *, workspace: Path, registry: Any = None, skills: Any = None) -> str:
    parts = [profile_prompt.strip()]
    instr, _ = instruction_text(workspace)
    if instr:
        parts.append(instr)
    if skills is not None:
        listing = skills.listing_text()
        if listing:
            parts.append(listing)
    if registry is not None:
        stub = registry.deferred_stub_text()
        if stub:
            parts.append(stub)
    return "\n\n".join(p for p in parts if p)


def dynamic_prompt(runtime: Any, state: Any) -> str:
    lines = [
        f"Today's date is {dt.date.today().isoformat()}.",
        f"Workspace: {runtime.workspace.name} (all paths are relative to it).",
    ]
    if runtime.sandbox is not None:
        lines.append(f"Sandbox backend: {runtime.sandbox.name}. Network inside the sandbox: {'allowed' if getattr(runtime.sandbox, 'policy', None) and runtime.sandbox.policy.network else 'blocked'}.")
    if runtime.gates is not None and runtime.gates.phases:
        lines.append(f"Current phase: {state.phase}.")
    return "\n".join(lines)
