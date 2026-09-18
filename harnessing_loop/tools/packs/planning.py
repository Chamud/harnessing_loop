"""Planning tools: the model's own bookkeeping, mirrored to disk.

`finish` is the gate. It refuses until the gates in the profile pass, and
its refusal text tells the model exactly what is missing.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..base import Tool, ToolContext, ToolResult

TODO_REMINDER = "Todo list updated. Keep it current: mark items in_progress when you start them and completed when they are done."


class TodoWrite(Tool):
    name = "todo_write"
    description = "Replace the todo list. Use it to plan multi-step work and to track progress."
    input_schema = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "minLength": 1},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                    },
                    "required": ["content", "status"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["todos"],
        "additionalProperties": False,
    }
    category = "plan"
    read_only = True  # touches only harness state
    concurrency_safe = False

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        todos = input["todos"]
        ctx.state.todos = todos
        (ctx.harness_dir / "todo.json").write_text(json.dumps(todos, indent=2, ensure_ascii=False), encoding="utf-8")
        done = sum(1 for t in todos if t["status"] == "completed")
        return ToolResult.ok(f"{TODO_REMINDER} ({done}/{len(todos)} completed)")


class SetPhase(Tool):
    name = "set_phase"
    description = "Declare the phase of work you are entering. Phases move forward only."
    input_schema = {
        "type": "object",
        "properties": {"phase": {"type": "string"}, "note": {"type": "string"}},
        "required": ["phase"],
        "additionalProperties": False,
    }
    category = "plan"
    read_only = True

    def validate(self, input: dict[str, Any], ctx: ToolContext) -> str | None:
        gates = ctx.gates
        if gates is not None and gates.phases and input["phase"] not in gates.phases:
            return f"Unknown phase {input['phase']!r}. Phases in order: {', '.join(gates.phases)}"
        return None

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        phase = input["phase"]
        gates = ctx.gates
        if gates is not None and gates.phases:
            cur = gates.index(ctx.state.phase)
            new = gates.index(phase)
            if new < cur:
                return ToolResult.error(f"Cannot move back from {ctx.state.phase} to {phase}. Phases move forward only.")
            missing = gates.check_entry(phase, ctx.state, ctx)
            if missing:
                return ToolResult.error(f"Cannot enter {phase} yet:\n- " + "\n- ".join(missing))
        ctx.state.phase = phase
        ctx.state.phases_seen.append(phase)
        if ctx.events:
            from ...core import events as ev

            ctx.events.emit(ev.PHASE, phase=phase, note=input.get("note", ""))
        _append_note(ctx, f"phase -> {phase}" + (f": {input['note']}" if input.get("note") else ""))
        return ToolResult.ok(f"Phase set to {phase}.", phase=phase)


class NotesAppend(Tool):
    name = "notes_append"
    description = "Append a line to the run notes. Notes survive context compaction; use them for decisions and findings."
    input_schema = {"type": "object", "properties": {"text": {"type": "string", "minLength": 1}}, "required": ["text"], "additionalProperties": False}
    category = "plan"
    read_only = True

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        _append_note(ctx, input["text"])
        return ToolResult.ok("Noted.")


class Finish(Tool):
    name = "finish"
    description = (
        "Declare the job complete. This is refused until every gate passes; the refusal lists what is missing. "
        "Give a short summary of what was produced and where."
    )
    input_schema = {
        "type": "object",
        "properties": {"summary": {"type": "string", "minLength": 1}, "status": {"type": "string", "enum": ["done", "blocked"]}},
        "required": ["summary"],
        "additionalProperties": False,
    }
    category = "plan"
    read_only = True

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        status = input.get("status", "done")
        if status == "done" and ctx.gates is not None:
            problems = ctx.gates.check_finish(ctx.state, ctx)
            if problems:
                return ToolResult.error("Cannot finish yet:\n- " + "\n- ".join(problems))
        ctx.state.add_evidence("finished", status)
        ctx.state.evidence["final_summary"] = input["summary"]
        _append_note(ctx, f"finish ({status}): {input['summary']}")
        return ToolResult.ok(f"Finish accepted ({status}). Stop now; do not call more tools.")


def _append_note(ctx: ToolContext, text: str) -> None:
    ctx.state.notes.append(text)
    with (ctx.harness_dir / "notes.md").open("a", encoding="utf-8") as fh:
        fh.write(f"- {time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")


def make() -> list[Tool]:
    return [TodoWrite(), SetPhase(), NotesAppend(), Finish()]
