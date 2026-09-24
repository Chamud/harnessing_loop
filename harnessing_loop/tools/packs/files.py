"""File tools. They run in the harness process, so every path is checked.

Reads bound themselves and never spill to disk (a spilled read would just be
read again). Writes and edits go through the file state cache and the
checkpoint store.

ファイルツール。ハーネスのプロセス内で動くため、すべてのパスを検査する。

読み取りは自ら大きさを抑え、ディスクへ退避しない（退避しても、また読み直される
だけである）。書き込みと編集は、ファイル状態キャッシュとチェックポイントの保管庫を
通る。
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any, Iterable

from ..base import Tool, ToolContext, ToolResult

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".harness", ".mypy_cache", ".pytest_cache"}
MAX_READ_CHARS = 100_000
MAX_FILE_BYTES = 8 * 1024 * 1024
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _rel(ctx: ToolContext, p: Path) -> str:
    try:
        return p.relative_to(ctx.workspace).as_posix()
    except ValueError:
        return str(p)


def _check_inside(ctx: ToolContext, p: Path) -> str | None:
    if not ctx.inside_workspace(p):
        return f"{p} is outside the workspace. Only paths under the workspace are allowed."
    return None


class ReadFile(Tool):
    name = "read_file"
    description = (
        "Read a text file from the workspace. Returns numbered lines. "
        "Use offset and limit for large files. Reading a file is required before editing it."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace, or absolute inside it"},
            "offset": {"type": "integer", "minimum": 1, "description": "First line to return (1-based)"},
            "limit": {"type": "integer", "minimum": 1, "description": "Number of lines to return"},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    category = "read"
    read_only = True
    concurrency_safe = True
    max_result_chars = 0

    def paths(self, input: dict[str, Any]) -> Iterable[str]:
        return [input.get("path", "")]

    def permission_subjects(self, input: dict[str, Any]) -> Iterable[str]:
        return [input.get("path", "")]

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        p = ctx.resolve(input["path"])
        err = _check_inside(ctx, p)
        if err:
            return ToolResult.error(err)
        if not p.exists():
            hint = _suggest(ctx, p)
            return ToolResult.error(f"File not found: {_rel(ctx, p)}." + (f" Did you mean {hint}?" if hint else ""))
        if p.is_dir():
            return ToolResult.error(f"{_rel(ctx, p)} is a directory. Use list_dir.")
        if p.suffix.lower() in IMAGE_EXT:
            return ToolResult.error("Binary image files cannot be read as text.")
        if p.stat().st_size > MAX_FILE_BYTES:
            return ToolResult.error(f"File is larger than {MAX_FILE_BYTES // (1024*1024)} MB. Use grep_files or shell tools to inspect parts of it.")
        raw = p.read_bytes()
        if b"\x00" in raw[:4096]:
            return ToolResult.error("File appears to be binary.")
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        offset = int(input.get("offset", 1))
        limit = input.get("limit")
        start = max(0, offset - 1)
        end = len(lines) if limit is None else min(len(lines), start + int(limit))
        selected = lines[start:end]
        partial = start > 0 or end < len(lines)
        out_lines = [f"{i + 1:6d}\t{line}" for i, line in enumerate(selected, start=start)]
        out = "\n".join(out_lines)
        if len(out) > MAX_READ_CHARS:
            out = out[:MAX_READ_CHARS] + f"\n... [truncated at {MAX_READ_CHARS:,} chars; use offset/limit]"
            partial = True
        if ctx.file_state is not None:
            ctx.file_state.record(p, text, partial=partial)
        rel = _rel(ctx, p)
        if rel not in ctx.state.files_read:
            ctx.state.files_read.append(rel)
        header = f"{rel} ({len(lines)} lines{', partial' if partial else ''})\n"
        return ToolResult.ok(header + (out if out else "(empty file)"))


class WriteFile(Tool):
    name = "write_file"
    description = "Create or overwrite a file in the workspace. To change part of an existing file prefer edit_file."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
        "additionalProperties": False,
    }
    category = "edit"

    def paths(self, input: dict[str, Any]) -> Iterable[str]:
        return [input.get("path", "")]

    def permission_subjects(self, input: dict[str, Any]) -> Iterable[str]:
        return [input.get("path", "")]

    def validate(self, input: dict[str, Any], ctx: ToolContext) -> str | None:
        p = ctx.resolve(input["path"])
        err = _check_inside(ctx, p)
        if err:
            return err
        if p.is_dir():
            return f"{_rel(ctx, p)} is a directory."
        if p.exists() and ctx.file_state is not None:
            return ctx.file_state.check_before_edit(p)
        return None

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        p = ctx.resolve(input["path"])
        if ctx.checkpoints is not None:
            ctx.checkpoints.backup(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(input["content"], encoding="utf-8")
        if ctx.file_state is not None:
            ctx.file_state.record(p, input["content"])
        rel = _rel(ctx, p)
        ctx.state.files_written.append(rel)
        return ToolResult.ok(f"Wrote {len(input['content']):,} chars to {rel}", file_written=rel)


class EditFile(Tool):
    name = "edit_file"
    description = (
        "Replace an exact string in a file. old_string must match exactly once unless replace_all is true. "
        "Read the file first."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean"},
        },
        "required": ["path", "old_string", "new_string"],
        "additionalProperties": False,
    }
    category = "edit"

    def paths(self, input: dict[str, Any]) -> Iterable[str]:
        return [input.get("path", "")]

    def permission_subjects(self, input: dict[str, Any]) -> Iterable[str]:
        return [input.get("path", "")]

    def validate(self, input: dict[str, Any], ctx: ToolContext) -> str | None:
        p = ctx.resolve(input["path"])
        err = _check_inside(ctx, p)
        if err:
            return err
        if input["old_string"] == input["new_string"]:
            return "old_string and new_string are identical."
        if not p.exists():
            return f"File not found: {_rel(ctx, p)}. Use write_file to create it."
        if input["old_string"] == "":
            return "old_string is empty. Use write_file to create or overwrite."
        if ctx.file_state is not None:
            err = ctx.file_state.check_before_edit(p)
            if err:
                return err
        text = p.read_text(encoding="utf-8", errors="replace")
        n = text.count(input["old_string"])
        if n == 0:
            return "old_string was not found in the file. Check whitespace and indentation."
        if n > 1 and not input.get("replace_all"):
            return f"old_string appears {n} times. Add more context to make it unique, or set replace_all."
        return None

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        p = ctx.resolve(input["path"])
        text = p.read_text(encoding="utf-8", errors="replace")
        if input.get("replace_all"):
            new_text = text.replace(input["old_string"], input["new_string"])
            n = text.count(input["old_string"])
        else:
            new_text = text.replace(input["old_string"], input["new_string"], 1)
            n = 1
        if ctx.checkpoints is not None:
            ctx.checkpoints.backup(p)
        p.write_text(new_text, encoding="utf-8")
        if ctx.file_state is not None:
            ctx.file_state.record(p, new_text)
        rel = _rel(ctx, p)
        ctx.state.files_written.append(rel)
        return ToolResult.ok(f"Replaced {n} occurrence(s) in {rel}", file_written=rel)


class ListDir(Tool):
    name = "list_dir"
    description = "List files and folders at a path in the workspace."
    input_schema = {"type": "object", "properties": {"path": {"type": "string"}}, "additionalProperties": False}
    category = "read"
    read_only = True
    concurrency_safe = True

    def paths(self, input: dict[str, Any]) -> Iterable[str]:
        return [input.get("path", ".")]

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        p = ctx.resolve(input.get("path", "."))
        err = _check_inside(ctx, p)
        if err:
            return ToolResult.error(err)
        if not p.is_dir():
            return ToolResult.error(f"{_rel(ctx, p)} is not a directory.")
        rows = []
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if child.name in SKIP_DIRS:
                continue
            if child.is_dir():
                rows.append(f"{child.name}/")
            else:
                try:
                    rows.append(f"{child.name}  ({child.stat().st_size:,} bytes)")
                except OSError:
                    rows.append(child.name)
        return ToolResult.ok(f"{_rel(ctx, p) or '.'}:\n" + ("\n".join(rows) if rows else "(empty)"))


def _walk(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            yield Path(dirpath) / f


class GlobFiles(Tool):
    name = "glob_files"
    description = "Find files matching a glob pattern such as '**/*.py' or 'src/*.md'."
    input_schema = {
        "type": "object",
        "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1}},
        "required": ["pattern"],
        "additionalProperties": False,
    }
    category = "read"
    read_only = True
    concurrency_safe = True

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        root = ctx.resolve(input.get("path", "."))
        err = _check_inside(ctx, root)
        if err:
            return ToolResult.error(err)
        limit = int(input.get("max_results", 200))
        pat = input["pattern"]
        hits: list[tuple[float, str]] = []
        for f in _walk(root):
            rel = f.relative_to(root).as_posix()
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(f.name, pat):
                try:
                    hits.append((f.stat().st_mtime, _rel(ctx, f)))
                except OSError:
                    continue
        hits.sort(reverse=True)
        names = [h[1] for h in hits[:limit]]
        more = f"\n... {len(hits) - limit} more" if len(hits) > limit else ""
        return ToolResult.ok("\n".join(names) + more if names else "No files matched.")


class GrepFiles(Tool):
    name = "grep_files"
    description = "Search file contents with a regular expression. Returns path:line: text."
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "glob": {"type": "string", "description": "Only files whose name matches, e.g. '*.py'"},
            "max_results": {"type": "integer", "minimum": 1},
            "ignore_case": {"type": "boolean"},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }
    category = "read"
    read_only = True
    concurrency_safe = True
    max_result_chars = 20_000

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        root = ctx.resolve(input.get("path", "."))
        err = _check_inside(ctx, root)
        if err:
            return ToolResult.error(err)
        try:
            rx = re.compile(input["pattern"], re.IGNORECASE if input.get("ignore_case") else 0)
        except re.error as exc:
            return ToolResult.error(f"Invalid regular expression: {exc}")
        limit = int(input.get("max_results", 100))
        name_glob = input.get("glob")
        out: list[str] = []
        files = [root] if root.is_file() else _walk(root)
        for f in files:
            if name_glob and not fnmatch.fnmatch(f.name, name_glob):
                continue
            try:
                if f.stat().st_size > MAX_FILE_BYTES:
                    continue
                with f.open("r", encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, start=1):
                        if rx.search(line):
                            out.append(f"{_rel(ctx, f)}:{i}: {line.rstrip()[:300]}")
                            if len(out) >= limit:
                                break
            except (OSError, UnicodeDecodeError):
                continue
            if len(out) >= limit:
                break
        if not out:
            return ToolResult.ok("No matches.")
        tail = f"\n... stopped at {limit} results" if len(out) >= limit else ""
        return ToolResult.ok("\n".join(out) + tail)


def _suggest(ctx: ToolContext, missing: Path) -> str | None:
    parent = missing.parent if missing.parent.exists() else ctx.workspace
    try:
        names = [c.name for c in parent.iterdir()]
    except OSError:
        return None
    target = missing.name.lower()
    for n in names:
        if n.lower() == target or target in n.lower() or n.lower() in target:
            return _rel(ctx, parent / n)
    return None


def make() -> list[Tool]:
    return [ReadFile(), WriteFile(), EditFile(), ListDir(), GlobFiles(), GrepFiles()]
