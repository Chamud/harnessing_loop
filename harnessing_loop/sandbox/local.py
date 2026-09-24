"""Local subprocess backend.

What it guarantees:
- the environment is built from an allowlist, so credentials in the harness
  process do not reach the child
- the working directory is inside the workspace
- a timeout kills the process tree

What it cannot guarantee:
- reads outside the workspace: the child runs as the same OS user
- network: there is no packet filter here

So this backend is for trusted inputs on your own machine. The startup
check refuses it for exec tools unless `allow_unsafe_local` is set.

ローカルのサブプロセスによるバックエンド。

保証すること:
- 環境は許可リストから組み立てられるため、ハーネスのプロセスにある認証情報は子
  プロセスに届かない
- 作業ディレクトリはワークスペースの内側にある
- タイムアウトはプロセスツリーを終了させる

保証できないこと:
- ワークスペースの外の読み取り。子プロセスは同じ OS ユーザで動く
- ネットワーク。ここにパケットフィルタはない

したがってこのバックエンドは、自分のマシンで信頼できる入力を扱うためのものである。
起動時の検査は、`allow_unsafe_local` が設定されていない限り exec ツールでの利用を
拒否する。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from .base import SandboxResult
from .policy import SandboxPolicy


class LocalSandbox:
    name = "local"

    def __init__(self, workspace: Path, policy: SandboxPolicy | None = None):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.policy = policy or SandboxPolicy()

    def isolates_secrets(self) -> bool:
        # 環境変数の除去は行われるが、ファイルシステムは隔離されない
        return False  # env is scrubbed, but the filesystem is not isolated

    def _cwd(self, cwd: Path | None) -> Path:
        target = (Path(cwd) if cwd else self.workspace).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError:
            raise PermissionError(f"cwd {target} is outside the workspace") from None
        return target

    def _env(self) -> dict[str, str]:
        env = self.policy.host_env()
        env["WORKSPACE"] = str(self.workspace)
        env["PYTHONPATH"] = str(self.workspace)
        return env

    def run(self, argv: list[str], *, cwd: Path | None = None, timeout: float | None = None, stdin: str | None = None) -> SandboxResult:
        return self._exec(argv, shell=False, cwd=cwd, timeout=timeout, stdin=stdin)

    def run_shell(self, command: str, *, cwd: Path | None = None, timeout: float | None = None) -> SandboxResult:
        if sys.platform == "win32":
            # The command line goes to cmd.exe unchanged: it keeps the exit code of the
            # last program and understands && and ||, close to sh semantics.
            # コマンドラインはそのまま cmd.exe に渡る。最後のプログラムの終了コードを
            # 保ち、&& と || を解釈するため、sh の意味づけに近い。
            return self._exec(command, shell=True, cwd=cwd, timeout=timeout)
        return self._exec(["/bin/sh", "-c", command], shell=False, cwd=cwd, timeout=timeout)

    def run_python(self, code: str, *, cwd: Path | None = None, timeout: float | None = None, stdin: str | None = None) -> SandboxResult:
        return self._exec([sys.executable, "-I", "-c", code], shell=False, cwd=cwd, timeout=timeout, stdin=stdin)

    def path_inside(self, rel: str) -> str:
        """Host path for a workspace-relative file, as the sandboxed program sees it.
        ワークスペース相対のファイルのホスト側パス。サンドボックス内のプログラムが
        見る形。
        """
        return str(self.workspace / rel)

    def _exec(self, argv: list[str] | str, *, shell: bool, cwd: Path | None, timeout: float | None, stdin: str | None = None) -> SandboxResult:
        t0 = time.time()
        try:
            wd = self._cwd(cwd)
        except PermissionError as exc:
            return SandboxResult("", str(exc), 126, backend=self.name)
        limit = timeout or self.policy.timeout_s
        try:
            proc = subprocess.run(
                argv,
                cwd=str(wd),
                env=self._env(),
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=limit,
                shell=shell,
            )
            return SandboxResult(proc.stdout, proc.stderr, proc.returncode, duration_s=time.time() - t0, backend=self.name)
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return SandboxResult(out, err + f"\n[timed out after {limit:.0f}s]", 124, timed_out=True, duration_s=time.time() - t0, backend=self.name)
        except FileNotFoundError as exc:
            return SandboxResult("", f"program not found: {exc}", 127, duration_s=time.time() - t0, backend=self.name)
