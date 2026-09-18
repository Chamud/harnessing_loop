from __future__ import annotations

import os
import sys

import pytest

from harnessing_loop.core.errors import ConfigError
from harnessing_loop.core.loop import Loop
from harnessing_loop.core.messages import ToolUseBlock
from harnessing_loop.llm.fake import tool
from harnessing_loop.sandbox.docker import DockerSandbox
from harnessing_loop.sandbox.local import LocalSandbox
from harnessing_loop.sandbox.policy import SandboxPolicy
from harnessing_loop.sandbox.startup_check import check_sandbox
from harnessing_loop.tools.pipeline import run_tool_call

from conftest import make_runtime


def test_local_env_is_scrubbed(ws, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak-me")
    monkeypatch.setenv("MY_API_TOKEN", "leak-me-too")
    sb = LocalSandbox(ws)
    r = sb.run_python("import os; print(','.join(sorted(k for k in os.environ if 'AWS' in k or 'TOKEN' in k)))")
    assert r.ok and r.stdout.strip() == ""


def test_local_env_allowlist_passthrough(ws, monkeypatch):
    monkeypatch.setenv("BUILD_FLAVOR", "blue")
    sb = LocalSandbox(ws, SandboxPolicy(env_allow=["BUILD_FLAVOR"], env={"INSIDE": "yes"}))
    r = sb.run_python("import os; print(os.environ['BUILD_FLAVOR'], os.environ['INSIDE'], os.environ['WORKSPACE'] != '')")
    assert r.stdout.split() == ["blue", "yes", "True"]


def test_cwd_jail(ws):
    sb = LocalSandbox(ws)
    r = sb.run_python("print(1)", cwd=ws.parent)
    assert r.exit_code == 126 and "outside the workspace" in r.stderr


def test_timeout(ws):
    sb = LocalSandbox(ws, SandboxPolicy(timeout_s=1))
    r = sb.run_python("import time; time.sleep(5)")
    assert r.timed_out and r.exit_code == 124


def test_shell_runs(ws):
    sb = LocalSandbox(ws)
    r = sb.run_shell("echo hi")
    assert r.ok and "hi" in r.stdout


def test_startup_check_refuses_unsafe_local(ws):
    sb = LocalSandbox(ws)
    with pytest.raises(ConfigError):
        check_sandbox(sb, has_exec_tools=True, allow_unsafe_local=False)
    assert check_sandbox(sb, has_exec_tools=False, allow_unsafe_local=False) == []
    assert check_sandbox(sb, has_exec_tools=True, allow_unsafe_local=True)


def test_profile_without_sandbox_but_exec_tools_fails(tmp_path):
    with pytest.raises(ConfigError):
        make_runtime(tmp_path, tool_packs=["files", "exec"])


def test_docker_command_flags(ws):
    sb = DockerSandbox(ws, SandboxPolicy(memory_mb=512, cpus=1, pids=64, env={"A": "1"}))
    cmd = sb._base_cmd(None)
    joined = " ".join(cmd)
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", "--memory 512m", "--cpus 1", "--pids-limit 64", "-e A=1", "--user 1000:1000"):
        assert flag in joined
    assert "AWS" not in joined and cmd[-1] == "python:3.12-slim"


def test_exec_tool_through_loop_cannot_see_secrets(tmp_path, sandboxed, monkeypatch):
    monkeypatch.setenv("SUPER_SECRET", "x")
    rt, _ = make_runtime(tmp_path, [], **sandboxed)
    r = run_tool_call(ToolUseBlock("t", "run_python", {"code": "import os; print('SUPER_SECRET' in os.environ)"}), Loop(rt).ctx)
    assert not r.is_error and "False" in r.content


def test_shell_output_formatting_and_error(tmp_path, sandboxed):
    rt, _ = make_runtime(tmp_path, [], **sandboxed)
    r = run_tool_call(ToolUseBlock("t", "shell", {"command": f"{sys.executable} -c \"import sys; sys.exit(3)\""}), Loop(rt).ctx)
    assert r.is_error and "exit code 3" in r.content


def test_long_sleep_rejected(tmp_path, sandboxed):
    rt, _ = make_runtime(tmp_path, [], **sandboxed)
    r = run_tool_call(ToolUseBlock("t", "shell", {"command": "sleep 1000"}), Loop(rt).ctx)
    assert r.is_error and "run_background" in r.content
