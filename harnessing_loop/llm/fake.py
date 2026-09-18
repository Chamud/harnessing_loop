"""A scripted model for tests, examples, and offline runs.

Two ways to script it:

1. A list of turns. Each turn is a string (plain text reply) or a list of
   blocks (text and tool_use). The fake replays them in order and then
   answers "done" with end_turn forever.

       FakeModel([
           [tool("read_file", path="a.txt")],
           "The file says hello.",
       ])

2. A callable `fn(request) -> turn` for tests that must react to what the
   loop sent, such as checking a tool result before answering.

Usage is synthesized from text length so token accounting has numbers to
work with. Set `usage_per_call` to force a specific input size and drive
compaction in tests.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator

from ..core.messages import Message, TextBlock, ToolUseBlock, Usage, new_id
from .base import DoneEvent, ModelEvent, ModelRequest, ModelResponse

Turn = str | list[Any] | dict[str, Any]


def tool(name: str, **input: Any) -> ToolUseBlock:
    return ToolUseBlock(id=new_id("tu_"), name=name, input=input)


def scripted(*turns: Turn) -> "FakeModel":
    return FakeModel(list(turns))


class FakeModel:
    model = "fake"

    def __init__(
        self,
        script: list[Turn] | Callable[[ModelRequest], Turn],
        *,
        usage_per_call: Usage | None = None,
        stream_chunks: int = 3,
    ):
        self._script = script
        self._i = 0
        self.requests: list[ModelRequest] = []
        self.usage_per_call = usage_per_call
        self.stream_chunks = max(1, stream_chunks)
        self.fail_next: Exception | None = None

    # ---- scripting helpers ------------------------------------------------
    def _next_turn(self, request: ModelRequest) -> Turn:
        if callable(self._script):
            return self._script(request)
        if self._i < len(self._script):
            turn = self._script[self._i]
            self._i += 1
            return turn
        return "done"

    def _turn_to_message(self, turn: Turn) -> tuple[Message, str]:
        if isinstance(turn, dict):
            # {"text": ..., "tools": [...], "stop_reason": ...}
            blocks: list[Any] = []
            if turn.get("text"):
                blocks.append(TextBlock(turn["text"]))
            blocks.extend(turn.get("tools", []))
            stop = turn.get("stop_reason") or ("tool_use" if turn.get("tools") else "end_turn")
            return Message(role="assistant", content=blocks), stop
        if isinstance(turn, str):
            return Message(role="assistant", content=[TextBlock(turn)]), "end_turn"
        blocks = [TextBlock(b) if isinstance(b, str) else b for b in turn]
        stop = "tool_use" if any(isinstance(b, ToolUseBlock) for b in blocks) else "end_turn"
        return Message(role="assistant", content=blocks), stop

    # ---- protocol ----------------------------------------------------------
    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]:
        self.requests.append(request)
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc
        turn = self._next_turn(request)
        message, stop = self._turn_to_message(turn)
        for block in message.content:
            if isinstance(block, TextBlock):
                text = block.text
                step = max(1, len(text) // self.stream_chunks)
                for i in range(0, len(text), step):
                    yield ModelEvent(kind="text", text=text[i : i + step])
            elif isinstance(block, ToolUseBlock):
                yield ModelEvent(kind="tool_use", block=block)
        if self.usage_per_call is not None:
            usage = self.usage_per_call
        else:
            in_chars = sum(len(str(m.content)) for m in request.messages) + sum(
                len(str(b)) for b in request.system
            )
            usage = Usage(input_tokens=in_chars // 4, output_tokens=max(1, len(message.text()) // 4))
        message.usage = usage
        message.stop_reason = stop
        yield DoneEvent(ModelResponse(message=message, usage=usage, stop_reason=stop, model=self.model))
