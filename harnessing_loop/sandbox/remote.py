"""Hosted sandbox adapter.

Contract (JSON over HTTPS, one call per run):

    POST {base_url}/run
    { "argv": [...], "cwd": "relative/dir", "timeout": 120,
      "stdin": "...", "workspace_id": "..." }
    -> { "stdout": "...", "stderr": "...", "exit_code": 0, "timed_out": false }

Files are synced by the provider's own mechanism; this adapter only runs
programs. Replace `_post` to fit a specific provider.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from ..core.errors import SandboxUnavailable
from .base import SandboxResult
from .policy import SandboxPolicy


class RemoteSandbox:
    name = "remote"

    def __init__(self, workspace: Path, policy: SandboxPolicy | None = None, *, base_url: str = "", token: str = "", workspace_id: str = ""):
        self.workspace = Path(workspace).resolve()
        self.policy = policy or SandboxPolicy()
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.workspace_id = workspace_id or self.workspace.name
        if not self.base_url:
            raise SandboxUnavailable("remote sandbox needs base_url")

    def isolates_secrets(self) -> bool:
        return True

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/run",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=payload.get("timeout", 120) + 30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _exec(self, argv: list[str], cwd: Path | None, timeout: float | None, stdin: str | None = None) -> SandboxResult:
        rel = Path(cwd).resolve().relative_to(self.workspace).as_posix() if cwd else "."
        t0 = time.time()
        data = self._post({
            "argv": argv,
            "cwd": rel,
            "timeout": timeout or self.policy.timeout_s,
            "stdin": stdin,
            "workspace_id": self.workspace_id,
        })
        return SandboxResult(
            data.get("stdout", ""), data.get("stderr", ""), int(data.get("exit_code", 1)),
            timed_out=bool(data.get("timed_out", False)), duration_s=time.time() - t0, backend=self.name,
        )

    def run(self, argv: list[str], *, cwd: Path | None = None, timeout: float | None = None, stdin: str | None = None) -> SandboxResult:
        return self._exec(list(argv), cwd, timeout, stdin)

    def run_shell(self, command: str, *, cwd: Path | None = None, timeout: float | None = None) -> SandboxResult:
        return self._exec(["/bin/sh", "-c", command], cwd, timeout)

    def run_python(self, code: str, *, cwd: Path | None = None, timeout: float | None = None, stdin: str | None = None) -> SandboxResult:
        return self._exec(["python", "-I", "-c", code], cwd, timeout, stdin)

    def path_inside(self, rel: str) -> str:
        return f"{self.policy.workdir}/{rel}"
