"""Cloud-hosted client.

Same request and event contract as the direct client. Uses the provider's
SDK with the cloud transport, so credentials come from the standard
environment variables or the shared credentials file of that cloud.

クラウド上で動くクライアント。

リクエストとイベントの契約は直接クライアントと同じである。プロバイダの SDK を
クラウド向けの転送で使うため、認証情報はそのクラウドの標準的な環境変数か、共有の
認証情報ファイルから渡される。
"""

from __future__ import annotations

from typing import Any

from ..core.errors import ModelError
from .anthropic import AnthropicClient
from .retry import RetryPolicy


class BedrockClient(AnthropicClient):
    def __init__(self, model: str, *, region: str | None = None, cache_ttl: str | None = None, retry: RetryPolicy = RetryPolicy(), **_: Any):
        super().__init__(model, cache_ttl=cache_ttl, retry=retry)
        self.region = region

    def _sdk(self):
        if self._client is None:
            try:
                from anthropic import AnthropicBedrock  # type: ignore
            except ImportError as exc:  # pragma: no cover
                raise ModelError("install the 'bedrock' extra to use the cloud client") from exc
            kwargs = {"aws_region": self.region} if self.region else {}
            self._client = AnthropicBedrock(**kwargs)
        return self._client
