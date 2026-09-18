"""Container backend. One container per call, destroyed afterwards.

Flags and why:
    --rm                        nothing survives the call except the workspace
    --network none              no network at all (a proxy is the only way in)
    --read-only + --tmpfs /tmp  the image cannot be modified
    -v workspace:/work          the only writable host path
    --user 1000:1000            not root inside the container
    --cap-drop ALL              no kernel capabilities
    --security-opt no-new-privileges
    --memory / --cpus / --pids-limit
    -e only from the policy     the host environment never enters

Requires the `docker` command on the host. Works on any engine that
accepts the same flags.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..core.errors import SandboxUnavailable
from .base import SandboxResult
from .policy import SandboxPolicy


class DockerSandbox:
    name = "docker"

    def __init__(self, workspace: Path, policy: SandboxPolicy | None = None, *, docker_bin: str = "docker"):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.policy = policy or SandboxPolicy()
        self.docker = shutil.which(docker_bin) or docker_bin

    def isolates_secrets(self) -> bool:
        return True

    @staticmethod
    def available(docker_bin: str = "docker") -> bool:
        exe = shutil.which(docker_bin)
        if not exe:
            return False
        try:
            r = subprocess.run([exe, "info"], capture_output=True, text=True, timeout=20)
            return r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _base_cmd(self, cwd: Path | None) -> list[str]:
        p = self.policy
        wd = p.workdir
        if cwd:
            rel = Path(cwd).resolve().relative_to(self.workspace).as_posix()
            wd = f"{p.workdir}/{rel}" if rel != "." else p.workdir
        cmd = [
            self.docker, "run", "--rm", "-i",
            "--network", "none" if not p.network else "bridge",
            "--read-only", "--tmpfs", "/tmp:rw,size=512m",
            "-v", f"{self.workspace}:{p.workdir}",
            "-w", wd,
            "--user", p.user,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--memory", f"{p.memory_mb}m",
            "--cpus", str(p.cpus),
            "--pids-limit", str(p.pids),
        ]
        for k, v in self.policy.container_env().items():
            cmd += ["-e", f"{k}={v}"]
        cmd += ["-e", f"WORKSPACE={p.workdir}", "-e", f"PYTHONPATH={p.workdir}"]
        cmd.append(p.image)
        return cmd

    def _exec(self, inner: list[str], cwd: Path | None, timeout: float | None, stdin: str | None = None) -> SandboxResult:
        if not self.available(self.docker):
            raise SandboxUnavailable("docker is not available on this host")
        limit = timeout or self.policy.timeout_s
        t0 = time.time()
        cmd = self._base_cmd(cwd) + inner
        try:
            proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=limit + 15)
            return SandboxResult(proc.stdout, proc.stderr, proc.returncode, duration_s=time.time() - t0, backend=self.name)
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            return SandboxResult(out, f"[timed out after {limit:.0f}s]", 124, timed_out=True, duration_s=time.time() - t0, backend=self.name)

    def run(self, argv: list[str], *, cwd: Path | None = None, timeout: float | None = None, stdin: str | None = None) -> SandboxResult:
        return self._exec(list(argv), cwd, timeout, stdin)

    def run_shell(self, command: str, *, cwd: Path | None = None, timeout: float | None = None) -> SandboxResult:
        return self._exec(["/bin/sh", "-c", f"timeout {int(timeout or self.policy.timeout_s)} sh -c {_sq(command)}"], cwd, timeout)

    def run_python(self, code: str, *, cwd: Path | None = None, timeout: float | None = None, stdin: str | None = None) -> SandboxResult:
        return self._exec(["python", "-I", "-c", code], cwd, timeout, stdin)

    def path_inside(self, rel: str) -> str:
        return f"{self.policy.workdir}/{rel}"


def _sq(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


if sys.platform == "win32":  # pragma: no cover - documentation only
    # Docker Desktop mounts Windows paths; the workspace path is passed as-is.
    pass
