"""Model clients. One protocol, several backends, one fake for tests."""

from .base import ModelClient, ModelEvent, ModelRequest, ModelResponse
from .fake import FakeModel, scripted

__all__ = ["ModelClient", "ModelEvent", "ModelRequest", "ModelResponse", "FakeModel", "scripted"]


def make_client(model: str, **kwargs):
    """Pick a client from a model string.

    "fake" or "fake:..." -> FakeModel (empty script unless given)
    "bedrock:<model-id>"  -> Bedrock client
    anything else         -> direct API client
    """
    if model == "fake" or model.startswith("fake:"):
        return FakeModel(kwargs.get("script") or [])
    if model.startswith("bedrock:"):
        from .bedrock import BedrockClient

        return BedrockClient(model.split(":", 1)[1], **kwargs)
    from .anthropic import AnthropicClient

    return AnthropicClient(model, **kwargs)
