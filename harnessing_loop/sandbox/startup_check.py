"""Refuse unsafe combinations before the first turn.

A profile with exec tools on the local backend is a development setup, not
a deployment. It only starts when the run config says `allow_unsafe_local`.

最初のターンより前に危険な組み合わせを拒否する。

local バックエンドで exec ツールを使うプロファイルは開発用の構成であり、配備用の
構成ではない。実行設定が `allow_unsafe_local` を指定したときにだけ起動する。
"""

from __future__ import annotations

from pathlib import Path

from ..core.errors import ConfigError, SandboxUnavailable
from .docker import DockerSandbox
from .local import LocalSandbox
from .policy import SandboxPolicy
from .remote import RemoteSandbox


def make_sandbox(backend: str, workspace: Path, policy: SandboxPolicy | None = None, **kwargs):
    if backend == "local":
        return LocalSandbox(workspace, policy)
    if backend == "docker":
        return DockerSandbox(workspace, policy, **kwargs)
    if backend == "remote":
        return RemoteSandbox(workspace, policy, **kwargs)
    raise ConfigError(f"unknown sandbox backend {backend!r}")


def check_sandbox(sandbox, *, has_exec_tools: bool, allow_unsafe_local: bool) -> list[str]:
    """Return warnings. Raise ConfigError for combinations that must not start.
    警告を返す。起動してはならない組み合わせには ConfigError を投げる。
    """
    warnings: list[str] = []
    if not has_exec_tools:
        return warnings
    if sandbox.name == "local":
        if not allow_unsafe_local:
            raise ConfigError(
                "exec tools on the local sandbox are for development only. "
                "Set allow_unsafe_local: true in the profile, or use the docker or remote backend."
            )
        warnings.append("local sandbox: environment is scrubbed but the filesystem and network are not isolated")
    if sandbox.name == "docker" and not DockerSandbox.available():
        raise SandboxUnavailable("profile requires the docker sandbox but docker is not running")
    return warnings
