"""Model clients. One protocol, several backends, one fake for tests.

モデルクライアント。1 つのプロトコル、複数のバックエンド、そしてテスト用の
フェイクモデルが 1 つ。
"""

from .base import ModelClient, ModelEvent, ModelRequest, ModelResponse
from .fake import FakeModel, scripted

__all__ = ["ModelClient", "ModelEvent", "ModelRequest", "ModelResponse", "FakeModel", "scripted"]


def make_client(model: str, **kwargs):
    """Pick a client from a model string.

    "fake" or "fake:..." -> FakeModel (empty script unless given)
    "bedrock:<model-id>"  -> Bedrock client
    anything else         -> direct API client

    モデル文字列からクライアントを選ぶ。

    `fake` または `fake:...` はフェイクモデル（台本を渡さなければ空）、
    `bedrock:` で始まる文字列はクラウドのクライアント、それ以外は直接 API
    クライアントになる。
    """
    if model == "fake" or model.startswith("fake:"):
        return FakeModel(kwargs.get("script") or [])
    if model.startswith("bedrock:"):
        from .bedrock import BedrockClient

        return BedrockClient(model.split(":", 1)[1], **kwargs)
    from .anthropic import AnthropicClient

    return AnthropicClient(model, **kwargs)
