"""Background tasks: long commands that should not block the turn.

Output goes to a file under `.harness/tasks/`. `task_output` returns only
the bytes the model has not seen yet. A task ends when its command ends or
its timeout fires; `task_stop` asks for an early stop, which the local and
container backends honour at the next timeout check.

バックグラウンドタスク。ターンを止めるべきでない、長いコマンドのためのもの。

出力は `.harness/tasks/` の下のファイルへ行く。`task_output` はモデルがまだ見て
いない分だけを返す。タスクは、そのコマンドが終わるか、タイムアウトが発火したときに
終わる。`task_stop` は早めの停止を要求し、local とコンテナのバックエンドは次の
タイムアウト検査のときにそれに従う。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Iterable

from ..base import Tool, ToolContext, ToolResult
from .exec import format_output, split_subcommands


class _Task:
    def __init__(self, id: str, command: str, path: Path):
        self.id = id
        self.command = command
        self.path = path
        self.status = "running"
        self.offset = 0
        self.started = time.time()
        self.stop_requested = False


def _tasks_dir(ctx: ToolContext) -> Path:
    d = ctx.harness_dir / "tasks"
    d.mkdir(exist_ok=True)
    return d


class RunBackground(Tool):
    name = "run_background"
    description = "Start a shell command in the background inside the sandbox. Returns a task id. Poll it with task_output."
    input_schema = {
        "type": "object",
        "properties": {"command": {"type": "string", "minLength": 1}, "timeout": {"type": "integer", "minimum": 1, "maximum": 7200}},
        "required": ["command"],
        "additionalProperties": False,
    }
    category = "exec"

    def permission_subjects(self, input: dict[str, Any]) -> Iterable[str]:
        return split_subcommands(input.get("command", ""))

    def validate(self, input: dict[str, Any], ctx: ToolContext) -> str | None:
        return None if ctx.sandbox is not None else "No sandbox is configured for this profile."

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        tid = ctx.deps.uuid("t_") if ctx.deps else f"t_{int(time.time() * 1000)}"
        path = _tasks_dir(ctx) / f"{tid}.out"
        task = _Task(tid, input["command"], path)
        ctx.tasks[tid] = task
        timeout = input.get("timeout", 600)

        def worker() -> None:
            res = ctx.sandbox.run_shell(task.command, timeout=timeout)
            path.write_text(format_output(res), encoding="utf-8")
            task.status = "completed" if res.ok else ("timed_out" if res.timed_out else "failed")

        threading.Thread(target=worker, name=tid, daemon=True).start()
        rel = path.relative_to(ctx.workspace).as_posix()
        return ToolResult.ok(f"Started task {tid}. Output file: {rel}. Use task_output to read it.")


class TaskOutput(Tool):
    name = "task_output"
    description = "Read new output from a background task. Set wait to block until it finishes (up to 120s)."
    input_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "wait": {"type": "boolean"}},
        "required": ["id"],
        "additionalProperties": False,
    }
    category = "read"
    read_only = True

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        task: _Task | None = ctx.tasks.get(input["id"])
        if task is None:
            return ToolResult.error(f"No task {input['id']}. Known: {', '.join(ctx.tasks) or 'none'}")
        if input.get("wait"):
            deadline = time.time() + 120
            while task.status == "running" and time.time() < deadline:
                time.sleep(0.5)
        text = task.path.read_text(encoding="utf-8", errors="replace") if task.path.exists() else ""
        new = text[task.offset :]
        task.offset = len(text)
        elapsed = time.time() - task.started
        head = f"[task {task.id}: {task.status}, {elapsed:.0f}s]"
        return ToolResult.ok(head + ("\n" + new if new else "\n(no new output)"))


class TaskStop(Tool):
    name = "task_stop"
    description = "Request a background task to stop."
    input_schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"], "additionalProperties": False}
    category = "exec"

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        task: _Task | None = ctx.tasks.get(input["id"])
        if task is None:
            return ToolResult.error(f"No task {input['id']}.")
        if task.status != "running":
            return ToolResult.ok(f"Task {task.id} already {task.status}.")
        task.stop_requested = True
        return ToolResult.ok(f"Stop requested for {task.id}. It ends at its next timeout check.")


def make() -> list[Tool]:
    return [RunBackground(), TaskOutput(), TaskStop()]
