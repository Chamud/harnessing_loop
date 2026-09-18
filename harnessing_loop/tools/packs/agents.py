"""Subagents: a fresh context for a bounded task.

The child gets its own message history, a subset of tools, the same sandbox
and permissions, and a copy of the file state cache. Only its final text
comes back as one tool result. Subagents cannot spawn subagents beyond the
configured depth, and never see `finish` or `define_tool`.
"""

from __future__ import annotations

from typing import Any

from ..base import Tool, ToolContext, ToolResult

CHILD_EXCLUDED = {"subagent", "finish", "define_tool", "set_phase"}
MAX_DEPTH = 2


class Subagent(Tool):
    name = "subagent"
    description = (
        "Run a focused sub-task in a fresh context and get back only its final answer. "
        "Good for wide searches or side investigations whose details would clutter your context."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "minLength": 3, "description": "3 to 8 words"},
            "prompt": {"type": "string", "minLength": 1, "description": "Everything the subagent needs; it shares none of your context"},
            "tools": {"type": "array", "items": {"type": "string"}, "description": "Tool names to allow; default: read-only tools"},
            "max_turns": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["description", "prompt"],
        "additionalProperties": False,
    }
    category = "agent"
    max_result_chars = 100_000

    def check_permissions(self, input: dict[str, Any], ctx: Any):
        from ...safety.permissions import Decision

        return Decision.allow("subagent: every child tool call is checked on its own")

    def validate(self, input: dict[str, Any], ctx: ToolContext) -> str | None:
        if ctx.depth >= MAX_DEPTH:
            return f"Subagent depth limit ({MAX_DEPTH}) reached."
        if ctx.extra.get("runtime") is None:
            return "Subagents need the runtime in the tool context."
        return None

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ...core.loop import Loop  # local import: the loop imports tools

        runtime = ctx.extra["runtime"]
        wanted = input.get("tools")
        if wanted:
            names = [n for n in wanted if n in ctx.registry and n not in CHILD_EXCLUDED]
        else:
            names = [t.name for t in ctx.registry.tools() if t.read_only and t.name not in CHILD_EXCLUDED]
        child_rt = runtime.child(
            registry=ctx.registry.subset(names),
            system_prompt=runtime.system_prompt + "\n\nYou are a subagent. Do the task and answer with your findings only.",
            max_turns=int(input.get("max_turns", 30)),
        )
        loop = Loop(child_rt)
        result = loop.run(input["prompt"])
        text = result.final_text.strip() or "(subagent produced no text)"
        header = f"[subagent: {input['description']} | {result.reason} in {result.turns} turns]\n"
        if result.reason not in ("completed",):
            return ToolResult(content=header + text, is_error=True)
        return ToolResult.ok(header + text, subagent_ran=True)


def make() -> list[Tool]:
    return [Subagent()]
