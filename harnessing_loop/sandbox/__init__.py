"""Sandbox: where model-written code runs.

Two trust zones. The harness process holds credentials and calls the model.
The sandbox runs whatever the model wrote and sees only its workspace, an
explicit environment, and no network unless a policy allows a domain.

サンドボックス: モデルが書いたコードが動く場所。

信頼境界は2つある。ハーネスのプロセスは認証情報を保持し、モデルを呼ぶ。サンド
ボックスはモデルが書いたものをそのまま実行し、見えるのは自分のワークスペースと
明示された環境だけである。ポリシーがドメインを許可しない限りネットワークはない。
"""

from .base import Sandbox, SandboxResult
from .policy import SandboxPolicy
from .local import LocalSandbox
from .docker import DockerSandbox
from .remote import RemoteSandbox
from .startup_check import check_sandbox, make_sandbox

__all__ = [
    "Sandbox",
    "SandboxResult",
    "SandboxPolicy",
    "LocalSandbox",
    "DockerSandbox",
    "RemoteSandbox",
    "check_sandbox",
    "make_sandbox",
]
