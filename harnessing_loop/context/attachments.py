"""Per-turn reminders appended to the tool-result message.

Only deltas are sent: a tool that became available, a background task that
finished, a todo list that has gone stale. Nothing is repeated every turn,
because repeated text costs tokens and trains the model to ignore it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AttachmentState:
    announced_tools: set[str] = field(default_factory=set)
    announced_tasks: set[str] = field(default_factory=set)
    last_todo_reminder_turn: int = 0
    last_date: str = ""
    inbox_seen: int = 0


TODO_REMINDER_EVERY = 10


def build_attachments(runtime: Any, state: Any, attach: AttachmentState, *, tasks: dict[str, Any] | None = None, inbox: list[str] | None = None) -> str:
    notes: list[str] = []

    # newly available tools (loaded deferred or defined at runtime)
    reg = runtime.registry
    available = set(reg.loaded_deferred) | set(reg.dynamic)
    new_tools = sorted(available - attach.announced_tools)
    if new_tools:
        notes.append("Tools now available: " + ", ".join(new_tools) + ".")
        attach.announced_tools |= set(new_tools)

    # finished background tasks
    if tasks:
        for tid, t in tasks.items():
            if getattr(t, "status", "running") != "running" and tid not in attach.announced_tasks:
                notes.append(f"Background task {tid} finished with status {t.status}. Read it with task_output.")
                attach.announced_tasks.add(tid)

    # stale todo list
    pending = [t for t in state.todos if t.get("status") != "completed"]
    if pending and state.turn - attach.last_todo_reminder_turn >= TODO_REMINDER_EVERY:
        notes.append(f"Todo list has {len(pending)} open item(s). Update it if progress was made.")
        attach.last_todo_reminder_turn = state.turn

    # date change across a long run
    today = dt.date.today().isoformat()
    if attach.last_date and attach.last_date != today:
        notes.append(f"The date is now {today}.")
    attach.last_date = today

    # operator messages from the control file
    if inbox:
        fresh = inbox[attach.inbox_seen :]
        for msg in fresh:
            notes.append(f"Message from the operator: {msg}")
        attach.inbox_seen = len(inbox)

    if not notes:
        return ""
    return "<system_reminder>\n" + "\n".join(f"- {n}" for n in notes) + "\n</system_reminder>"
