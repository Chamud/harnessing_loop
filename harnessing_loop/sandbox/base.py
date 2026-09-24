from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    duration_s: float = 0.0
    backend: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class Sandbox(Protocol):
    name: str
    workspace: Path

    def run(self, argv: list[str], *, cwd: Path | None = None, timeout: float = 120.0, stdin: str | None = None) -> SandboxResult:
        """Run a program. `argv[0]` is resolved inside the sandbox.
        プログラムを実行する。`argv[0]` はサンドボックスの内側で解決される。
        """
        ...

    def run_shell(self, command: str, *, cwd: Path | None = None, timeout: float = 120.0) -> SandboxResult:
        """Run a shell command line.
        シェルのコマンドラインを実行する。
        """
        ...

    def run_python(self, code: str, *, cwd: Path | None = None, timeout: float = 120.0, stdin: str | None = None) -> SandboxResult:
        """Run a Python snippet.
        Python の断片を実行する。
        """
        ...

    def path_inside(self, rel: str) -> str:
        """The path of a workspace-relative file as seen from inside the sandbox.
        ワークスペース相対のファイルのパスを、サンドボックスの内側から見た形で返す。
        """
        ...

    def isolates_secrets(self) -> bool:
        """True if the host environment cannot leak into the sandbox.
        ホストの環境がサンドボックスに漏れ出せない場合に True。
        """
        ...
