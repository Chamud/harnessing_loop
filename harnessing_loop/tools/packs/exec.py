"""Execution tools. Everything here goes through the sandbox.

The permission subject of a shell call is the list of its subcommands, so
a rule such as `shell(git *)` sees `git push` inside `ls && git push`.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from ..base import Tool, ToolContext, ToolResult

MAX_OUTPUT_CHARS = 30_000
_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")


def split_subcommands(command: str) -> list[str]:
    parts = [p.strip() for p in _SPLIT.split(command) if p.strip()]
    return parts or [command.strip()]


def format_output(res: Any) -> str:
    out = res.stdout or ""
    err = res.stderr or ""
    body = out
    if err.strip():
        body += ("\n" if body and not body.endswith("\n") else "") + "[stderr]\n" + err
    if len(body) > MAX_OUTPUT_CHARS:
        half = MAX_OUTPUT_CHARS // 2
        body = body[:half] + f"\n... [{len(body) - MAX_OUTPUT_CHARS:,} chars truncated] ...\n" + body[-half:]
    status = "timed out" if res.timed_out else f"exit code {res.exit_code}"
    return f"[{status}, {res.duration_s:.1f}s, sandbox={res.backend}]\n{body}".rstrip()


class Shell(Tool):
    name = "shell"
    description = (
        "Run a shell command inside the sandbox, with the workspace as the working directory. "
        "No network. Long-running commands should use run_background."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 1800, "description": "seconds"},
            "cwd": {"type": "string", "description": "Working directory relative to the workspace"},
        },
        "required": ["command"],
        "additionalProperties": False,
    }
    category = "exec"
    max_result_chars = MAX_OUTPUT_CHARS

    def permission_subjects(self, input: dict[str, Any]) -> Iterable[str]:
        return split_subcommands(input.get("command", ""))

    def validate(self, input: dict[str, Any], ctx: ToolContext) -> str | None:
        if ctx.sandbox is None:
            return "No sandbox is configured for this profile."
        if re.search(r"\bsleep\s+(\d{3,})", input["command"]):
            return "Long sleeps are not allowed. Use run_background and task_output instead."
        return None

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        cwd = ctx.resolve(input["cwd"]) if input.get("cwd") else None
        res = ctx.sandbox.run_shell(input["command"], cwd=cwd, timeout=input.get("timeout"))
        text = format_output(res)
        if res.ok:
            return ToolResult.ok(text, data=res, shell_ran=True)
        return ToolResult(content=text, is_error=True, data=res)


class RunPython(Tool):
    name = "run_python"
    description = "Run a Python snippet inside the sandbox. Print what you want to see. The workspace is on sys.path."
    input_schema = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 1800},
        },
        "required": ["code"],
        "additionalProperties": False,
    }
    category = "exec"
    max_result_chars = MAX_OUTPUT_CHARS

    def permission_subjects(self, input: dict[str, Any]) -> Iterable[str]:
        first = input.get("code", "").strip().splitlines()[:1]
        return ["python " + (first[0] if first else "")]

    def validate(self, input: dict[str, Any], ctx: ToolContext) -> str | None:
        if ctx.sandbox is None:
            return "No sandbox is configured for this profile."
        return None

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        res = ctx.sandbox.run_python(input["code"], timeout=input.get("timeout"))
        text = format_output(res)
        if res.ok:
            return ToolResult.ok(text, data=res, python_ran=True)
        return ToolResult(content=text, is_error=True, data=res)


def make() -> list[Tool]:
    return [Shell(), RunPython()]
