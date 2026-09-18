"""One tool call, start to finish.

    lookup -> schema -> validate -> pre hooks -> permission -> call
           -> post hooks -> redact -> size control -> evidence

Every exit from this pipeline is a ToolResultBlock. Nothing raises.
"""

from __future__ import annotations

import time
import traceback
from typing import Any

from ..core import events as ev
from ..core.errors import tool_error_text
from ..core.messages import ToolResultBlock, ToolUseBlock
from ..safety.hooks import Events
from ..safety.permissions import Decision, decide
from .base import ToolContext, ToolResult, validate_schema
from .results import size_control


def _err(call_id: str, message: str) -> ToolResultBlock:
    return ToolResultBlock(call_id, tool_error_text(message), is_error=True)


def run_tool_call(call: ToolUseBlock, ctx: ToolContext) -> ToolResultBlock:
    registry = ctx.registry
    bus = ctx.events
    t0 = time.time()
    if bus:
        bus.emit(ev.TOOL_START, name=call.name, id=call.id, input=call.input)

    def finish(block: ToolResultBlock, *, tool: Any = None) -> ToolResultBlock:
        if ctx.redactor:
            block = ToolResultBlock(block.tool_use_id, ctx.redactor(block.content), block.is_error)
        if tool is not None:
            block = ToolResultBlock(
                block.tool_use_id,
                size_control(block.content, tool=tool, call_id=call.id, workspace=ctx.workspace, config=ctx.config),
                block.is_error,
            )
        if bus:
            bus.emit(
                ev.TOOL_END,
                name=call.name,
                id=call.id,
                is_error=block.is_error,
                preview=block.content[:400],
                seconds=round(time.time() - t0, 3),
            )
        return block

    # 1. lookup
    tool = registry.get(call.name) if registry else None
    if tool is None:
        return finish(_err(call.id, f"No such tool: {call.name}. Available: {', '.join(registry.names()) if registry else 'none'}"))
    if registry.is_deferred(call.name):
        return finish(_err(call.id, f"Tool {call.name} is not loaded. Call tool_search with query \"select:{call.name}\" first, then retry."))

    # 2. schema
    input: dict[str, Any] = dict(call.input or {})
    if "_raw" in input and len(input) == 1:
        return finish(_err(call.id, "Tool input was not valid JSON."), tool=tool)
    schema_err = validate_schema(tool.input_schema, input)
    if schema_err:
        return finish(_err(call.id, f"Invalid input for {call.name}: {schema_err}"), tool=tool)

    # 3. tool validation
    try:
        v = tool.validate(input, ctx)
    except Exception as exc:  # noqa: BLE001
        v = f"validation crashed: {exc}"
    if v:
        return finish(_err(call.id, v), tool=tool)

    # 4. pre hooks
    extra_context: list[str] = []
    hook_decision: str | None = None
    if ctx.hooks and ctx.hooks.has(Events.PRE_TOOL_USE):
        merged = ctx.hooks.run(Events.PRE_TOOL_USE, {"tool": call.name, "input": input, "state": ctx.state.snapshot()})
        if merged.stop:
            ctx.state.evidence["stop_requested"] = merged.reason or "stopped by hook"
            if bus:
                bus.emit(ev.HOOK_BLOCKED, tool=call.name, reason=merged.reason, stop=True)
            return finish(_err(call.id, f"Run stopped by hook: {merged.reason or 'no reason given'}"), tool=tool)
        if merged.updated_input:
            input = dict(merged.updated_input)
            schema_err = validate_schema(tool.input_schema, input)
            if schema_err:
                return finish(_err(call.id, f"Hook rewrote input into an invalid shape: {schema_err}"), tool=tool)
        if merged.context_text:
            extra_context.append(merged.context_text)
        hook_decision = merged.decision
        if hook_decision == "deny":
            if bus:
                bus.emit(ev.HOOK_BLOCKED, tool=call.name, reason=merged.reason)
            return finish(_err(call.id, f"Blocked by hook: {merged.reason or 'no reason given'}"), tool=tool)

    # 5. permission
    decision = decide(tool, input, ctx.permissions, hook_allow=hook_decision == "allow") if ctx.permissions else Decision.allow("no permission context")
    if decision.behavior != "allow":
        if bus:
            bus.emit(ev.PERMISSION_DENIED, tool=call.name, reason=decision.reason)
        return finish(_err(call.id, f"Permission denied for {call.name}: {decision.reason}"), tool=tool)
    if decision.updated_input:
        input = dict(decision.updated_input)

    # 6. call
    try:
        result = tool.call(input, ctx)
        if not isinstance(result, ToolResult):
            result = ToolResult.ok(str(result))
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc(limit=3)
        result = ToolResult.error(f"{call.name} crashed: {exc}\n{tb}")

    # 7. post hooks
    if ctx.hooks:
        event = Events.POST_TOOL_FAILURE if result.is_error else Events.POST_TOOL_USE
        if ctx.hooks.has(event):
            merged = ctx.hooks.run(event, {"tool": call.name, "input": input, "result": result.content[:4000], "is_error": result.is_error})
            if merged.context_text:
                extra_context.append(merged.context_text)
            if merged.stop:
                ctx.state.evidence["stop_requested"] = merged.reason or "stopped by hook"

    # 8. evidence
    if result.evidence and not result.is_error:
        for k, val in result.evidence.items():
            ctx.state.add_evidence(k, val)

    content = result.content
    if extra_context:
        content = content + "\n\n" + "\n\n".join(f"<hook_context>{c}</hook_context>" for c in extra_context)
    return finish(ToolResultBlock(call.id, content, result.is_error), tool=tool)
