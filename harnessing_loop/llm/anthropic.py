"""Direct API client.

Requires the optional `anthropic` package. The client is created lazily so
importing this module without the package installed is harmless.

直接 API クライアント。

省略可能な `anthropic` パッケージを必要とする。クライアントは遅延生成されるので、
パッケージが未インストールのままこのモジュールを import しても害はない。
"""

from __future__ import annotations

import time
from typing import Any, Iterator

from ..core.errors import ModelError
from ..core.messages import Message, TextBlock, ThinkingBlock, ToolUseBlock, Usage
from .base import DoneEvent, ModelEvent, ModelRequest, ModelResponse
from .cache_layout import messages_with_cache, tool_schemas_with_cache
from .retry import RetryPolicy, with_idle_timeout, with_retry


class AnthropicClient:
    def __init__(self, model: str, *, cache_ttl: str | None = None, retry: RetryPolicy = RetryPolicy(), client: Any = None, **_: Any):
        self.model = model
        self.cache_ttl = cache_ttl
        self.retry = retry
        self._client = client
        self.on_retry = None  # set by the loop to emit retry events
        # 再試行イベントを発行するためにループが設定する

    def _sdk(self):
        if self._client is None:
            try:
                import anthropic  # type: ignore
            except ImportError as exc:  # pragma: no cover
                raise ModelError("install the 'anthropic' extra to use the direct API client") from exc
            self._client = anthropic.Anthropic()
        return self._client

    def _params(self, request: ModelRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_output_tokens,
            "system": request.system,
            "messages": messages_with_cache(request.messages, self.cache_ttl),
        }
        if request.tools:
            params["tools"] = tool_schemas_with_cache(request.tools, self.cache_ttl)
        if request.thinking == "adaptive":
            params["thinking"] = {"type": "adaptive"}
        elif request.thinking == "budget":
            budget = min(request.thinking_budget_tokens, request.max_output_tokens - 1)
            params["thinking"] = {"type": "enabled", "budget_tokens": budget}
        elif request.temperature is not None:
            params["temperature"] = request.temperature
        if request.stop_sequences:
            params["stop_sequences"] = request.stop_sequences
        if request.metadata:
            params["metadata"] = {"user_id": str(request.metadata.get("user_id", ""))[:256]}
        return params

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]:
        params = self._params(request)

        def run() -> Iterator[ModelEvent]:
            return list(with_idle_timeout(self._stream_once(params), self.retry.idle_timeout))

        events = with_retry(run, policy=self.retry, on_retry=self.on_retry)
        yield from events

    def _stream_once(self, params: dict[str, Any]) -> Iterator[ModelEvent]:
        sdk = self._sdk()
        blocks: list[Any] = []
        current_text: list[str] = []
        current_thinking: list[str] = []
        current_sig: str | None = None
        current_tool: dict[str, Any] | None = None
        tool_json: list[str] = []
        usage = Usage()
        stop_reason = "end_turn"
        started = time.time()
        with sdk.messages.stream(**params) as stream:
            for ev in stream:
                t = getattr(ev, "type", "")
                if t == "message_start":
                    u = getattr(ev.message, "usage", None)
                    if u:
                        usage.input_tokens = getattr(u, "input_tokens", 0) or 0
                        usage.cache_read_tokens = getattr(u, "cache_read_input_tokens", 0) or 0
                        usage.cache_write_tokens = getattr(u, "cache_creation_input_tokens", 0) or 0
                elif t == "content_block_start":
                    cb = ev.content_block
                    if cb.type == "tool_use":
                        current_tool = {"id": cb.id, "name": cb.name}
                        tool_json = []
                    elif cb.type == "thinking":
                        current_thinking = []
                        current_sig = None
                    else:
                        current_text = []
                elif t == "content_block_delta":
                    d = ev.delta
                    if d.type == "text_delta":
                        current_text.append(d.text)
                        yield ModelEvent(kind="text", text=d.text)
                    elif d.type == "thinking_delta":
                        current_thinking.append(d.thinking)
                        yield ModelEvent(kind="thinking", text=d.thinking)
                    elif d.type == "signature_delta":
                        current_sig = d.signature
                    elif d.type == "input_json_delta":
                        tool_json.append(d.partial_json)
                elif t == "content_block_stop":
                    if current_tool is not None:
                        import json

                        raw = "".join(tool_json) or "{}"
                        try:
                            inp = json.loads(raw)
                        except json.JSONDecodeError:
                            inp = {"_raw": raw}
                        block = ToolUseBlock(current_tool["id"], current_tool["name"], inp)
                        blocks.append(block)
                        yield ModelEvent(kind="tool_use", block=block)
                        current_tool = None
                    elif current_thinking:
                        blocks.append(ThinkingBlock("".join(current_thinking), current_sig))
                        current_thinking = []
                    elif current_text:
                        blocks.append(TextBlock("".join(current_text)))
                        current_text = []
                elif t == "message_delta":
                    stop_reason = getattr(ev.delta, "stop_reason", None) or stop_reason
                    u = getattr(ev, "usage", None)
                    if u:
                        usage.output_tokens = getattr(u, "output_tokens", 0) or 0
        if not blocks and time.time() - started > 0:
            # A stream that produced nothing is treated as a transport failure.
            # 何も生成しなかったストリームは転送の失敗として扱う。
            raise ModelError("empty stream", retryable=True)
        msg = Message(role="assistant", content=blocks, usage=usage, stop_reason=stop_reason)
        yield DoneEvent(ModelResponse(message=msg, usage=usage, stop_reason=stop_reason, model=self.model))
