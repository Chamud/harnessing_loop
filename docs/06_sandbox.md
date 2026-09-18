# The sandbox

This doc covers where model-written code runs, what each backend isolates, and how tools are routed between the harness process and the sandbox.

## Two trust zones

The harness process holds credentials and calls the model. The sandbox runs whatever the model wrote and sees only the workspace, an explicit environment, and no network unless a policy allows it. A secret that never enters the sandbox cannot be exfiltrated by code running there.

| Runs in the harness | Runs in the sandbox |
|---|---|
| file tools (`read_file`, `write_file`, `edit_file`, ...) | `shell`, `run_python` (`tools/packs/exec.py`) |
| planning tools, memory, skills | `run_background` (`tools/packs/tasks.py`) |
| `web_fetch`, `web_search` (`tools/packs/web.py`) | model-defined tools (`tools/packs/meta.py` `DynamicTool`) |

## The `Sandbox` protocol

`sandbox/base.py`. Every backend has `name`, `workspace`, and:

```python
run(argv, *, cwd=None, timeout=120.0, stdin=None) -> SandboxResult
run_shell(command, *, cwd=None, timeout=120.0) -> SandboxResult
run_python(code, *, cwd=None, timeout=120.0, stdin=None) -> SandboxResult
path_inside(rel) -> str          # workspace-relative path as seen inside the sandbox
isolates_secrets() -> bool       # True if the host environment cannot leak in
```

`SandboxResult` carries `stdout`, `stderr`, `exit_code`, `timed_out`, `duration_s`, `backend`; `ok` means exit 0 and no timeout. `format_output` in `exec.py` renders it for the model with a header like `[exit code 0, 0.3s, sandbox=local]`.

## `SandboxPolicy`

`sandbox/policy.py`. The policy is data; backends enforce what they can.

```python
SandboxPolicy(env_allow=[], env={}, network=False, allowed_domains=[],
              deny_read=[], deny_write=[], timeout_s=120.0, memory_mb=2048,
              cpus=2.0, pids=256, image="python:3.12-slim", user="1000:1000", workdir="/work")
```

The environment is an allowlist, never a blocklist:

- `BASE_ENV_ALLOW`: `PATH`, `LANG`, `LC_ALL`, `TZ`, `PYTHONIOENCODING`, `PYTHONUTF8`
- `POSIX_ENV_ALLOW`: `HOME`, `USER`, `TMPDIR`, `SHELL`, `TERM`
- `WINDOWS_ENV_ALLOW`: `SYSTEMROOT`, `COMSPEC`, `PATHEXT`, `TEMP`, `TMP`, and the other variables a process needs to start there
- `env_allow`: extra host variables the profile passes through
- `env`: explicit values set inside the sandbox

`host_env()` builds the local subprocess environment from these, defaulting `PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1`, `MPLBACKEND=Agg`. `container_env()` uses those defaults plus `HOME=/tmp` and `env` only; the host environment never enters a container.

## Backends

### `LocalSandbox` (`sandbox/local.py`)

Guarantees: the environment comes from the allowlist, so credentials in the harness process do not reach the child; the working directory must be inside the workspace (exit 126 otherwise); a timeout kills the process (exit 124); Python runs with `-I`; `WORKSPACE` and `PYTHONPATH` point at the workspace.

Cannot guarantee: reads outside the workspace, since the child runs as the same OS user; network, since there is no packet filter. `isolates_secrets()` returns `False`. For trusted inputs on your own machine.

### `DockerSandbox` (`sandbox/docker.py`)

One container per call, destroyed afterwards.

| Flag | Reason |
|---|---|
| `--rm` | nothing survives the call except the workspace |
| `--network none` | no network; `bridge` only when `policy.network` is true |
| `--read-only --tmpfs /tmp` | the image cannot be modified |
| `-v <workspace>:/work` | the only writable host path |
| `--user 1000:1000` | not root inside the container |
| `--cap-drop ALL` | no kernel capabilities |
| `--security-opt no-new-privileges` | no privilege escalation |
| `--memory`, `--cpus`, `--pids-limit` | resource limits from the policy |
| `-e` only from `container_env()` | the host environment never enters |

`DockerSandbox.available()` runs `docker info`; `_exec` raises `SandboxUnavailable` when it fails.

### `RemoteSandbox` (`sandbox/remote.py`)

An adapter for a hosted sandbox. One HTTPS call per run:

```
POST {base_url}/run
{"argv": [...], "cwd": "relative/dir", "timeout": 120, "stdin": "...", "workspace_id": "..."}
-> {"stdout": "...", "stderr": "...", "exit_code": 0, "timed_out": false}
```

Files are synced by the provider's own mechanism; the adapter only runs programs. Replace `_post` to fit a provider. It raises `SandboxUnavailable` without a `base_url`.

## Startup check

`sandbox/startup_check.py`. `make_sandbox(backend, workspace, policy, **kwargs)` builds a backend. `check_sandbox(sandbox, has_exec_tools=..., allow_unsafe_local=...)` runs in `Profile.build` before the first turn:

- exec tools on `local` without `allow_unsafe_local: true`: `ConfigError`
- exec tools on `local` with it: a warning that filesystem and network are not isolated
- `docker` with no running engine: `SandboxUnavailable`
- exec tools with no `sandbox:` section at all: `ConfigError`

## Profile keys

From `profiles/template_app.yaml`:

```yaml
sandbox:
  backend: local                  # local (dev only) | docker | remote
  allow_unsafe_local: true
  network: false
  allowed_domains: []             # honoured by backends with a proxy
  env_allow: []                   # extra host variables to pass through, never secrets
  env: {}                         # explicit values set inside the sandbox
  timeout_s: 300
  memory_mb: 2048
  cpus: 2
  pids: 256
  image: python:3.12-slim
```

Backend-specific keys pass through as constructor arguments: `base_url`, `token`, `workspace_id` for `remote`; `docker_bin` for `docker`.

## Tool routing

- `shell` calls `ctx.sandbox.run_shell`; `run_python` calls `ctx.sandbox.run_python`. Both fail validation without a sandbox. `shell` also rejects `sleep` of 100 seconds or more and points at `run_background`.
- `run_background` starts a thread that calls `ctx.sandbox.run_shell` and writes the result to `.harness/tasks/<id>.out`. `task_output` returns only the bytes the model has not seen.
- `define_tool` stores code under `tools/<name>.py` in the workspace. Calling the resulting `DynamicTool` runs `ctx.sandbox.run_python` with a wrapper that loads the file through `runpy.run_path(sandbox.path_inside(...))` and passes the input as JSON on stdin. A model-made tool can do exactly what `run_python` can do and nothing more.
- `web_fetch` runs in the harness and never executes what it fetches. `validate` accepts only `http` and `https`, checks the hostname against `web.allowed_domains` from the profile (`fnmatch`, `"*"` for any), and refuses private, link-local, loopback, reserved, multicast, and unresolvable addresses via `_is_private`. That blocks reaching internal services or cloud metadata endpoints through the agent. HTML is reduced to text and capped at `MAX_FETCH_CHARS` (40,000).

## Comparison

| | `local` | `docker` | `remote` |
|---|---|---|---|
| Env isolation | allowlist only | allowlist only, host env never enters | provider's |
| Filesystem isolation | none, same OS user | workspace mount only, read-only image | provider's |
| Network | host network, no filter | none unless `network: true` | provider's |
| Resource limits | timeout only | memory, cpus, pids, timeout | timeout in the request |
| `isolates_secrets()` | `False` | `True` | `True` |
| Intended use | development, trusted inputs | deployment on one host | deployment on a hosted runner |

## Proof of env scrubbing

Adapted from `tests/test_sandbox.py`:

```python
import os
from pathlib import Path

from harnessing_loop.sandbox.local import LocalSandbox
from harnessing_loop.sandbox.policy import SandboxPolicy

os.environ["MY_API_TOKEN"] = "leak-me"

sb = LocalSandbox(Path("./work"))
r = sb.run_python("import os; print('MY_API_TOKEN' in os.environ)")
print(r.ok, r.stdout.strip())        # True False

sb = LocalSandbox(Path("./work"), SandboxPolicy(env_allow=["MY_API_TOKEN"]))
r = sb.run_python("import os; print(os.environ['MY_API_TOKEN'])")
print(r.stdout.strip())              # leak-me, only because the policy said so
```

## Run this

```
python -m pytest tests/test_sandbox.py -q
```

It covers env scrubbing, allowlist passthrough, the cwd jail, timeouts, the startup check, the docker flag set, and an exec tool through the full loop that cannot see a host secret.

Next: `docs/07_compaction.md`
