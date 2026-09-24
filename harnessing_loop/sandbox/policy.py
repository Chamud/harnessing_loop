"""What a sandbox may see and do.

The policy is data. Backends enforce as much of it as they can and report
what they cannot through `Sandbox.isolates_secrets()` and the startup check.

サンドボックスが見てよいもの、してよいこと。

ポリシーはデータである。バックエンドは可能な範囲でそれを強制し、できない部分は
`Sandbox.isolates_secrets()` と起動時の検査を通じて報告する。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

# Variables a subprocess needs to start at all. Nothing here carries secrets.
# サブプロセスが起動するために最低限必要な変数。ここにシークレットは含まれない。
BASE_ENV_ALLOW = ("PATH", "LANG", "LC_ALL", "TZ", "PYTHONIOENCODING", "PYTHONUTF8")
WINDOWS_ENV_ALLOW = ("SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP", "WINDIR", "SYSTEMDRIVE", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH", "PROGRAMFILES", "PROGRAMDATA")
POSIX_ENV_ALLOW = ("HOME", "USER", "TMPDIR", "SHELL", "TERM")


@dataclass
class SandboxPolicy:
    env_allow: list[str] = field(default_factory=list)  # extra host variables to pass through
    # env_allow は通過させるホスト側の追加変数
    env: dict[str, str] = field(default_factory=dict)  # explicit values set inside the sandbox
    # env はサンドボックスの内側で明示的に設定する値
    network: bool = False
    allowed_domains: list[str] = field(default_factory=list)  # only honoured by backends with a proxy
    # allowed_domains はプロキシを持つバックエンドでのみ尊重される
    deny_read: list[str] = field(default_factory=list)
    deny_write: list[str] = field(default_factory=list)
    timeout_s: float = 120.0
    memory_mb: int = 2048
    cpus: float = 2.0
    pids: int = 256
    image: str = "python:3.12-slim"
    user: str = "1000:1000"
    workdir: str = "/work"

    def host_env(self) -> dict[str, str]:
        """Build the environment for a local subprocess. Allowlist only.
        ローカルのサブプロセス用の環境を組み立てる。許可リストのみ。
        """
        allow = set(BASE_ENV_ALLOW) | set(self.env_allow)
        allow |= set(WINDOWS_ENV_ALLOW) if sys.platform == "win32" else set(POSIX_ENV_ALLOW)
        env = {k: v for k, v in os.environ.items() if k.upper() in {a.upper() for a in allow}}
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("MPLBACKEND", "Agg")
        env.update(self.env)
        return env

    def container_env(self) -> dict[str, str]:
        env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "MPLBACKEND": "Agg", "HOME": "/tmp"}
        env.update(self.env)
        return env
