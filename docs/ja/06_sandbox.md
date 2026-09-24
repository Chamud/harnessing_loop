# サンドボックス

[English](../06_sandbox.md) · [索引](README.md) · [用語集](GLOSSARY.md)

この文書では、モデルが書いたコードがどこで走るか、各バックエンドが何を隔離するか、そしてツールがハーネスプロセスとサンドボックスのどちらに振り分けられるかを扱う。

## 2つの信頼境界

ハーネスプロセスは認証情報を保持し、モデルを呼ぶ。サンドボックスはモデルが書いたものを実行し、見えるのはワークスペースと明示された環境だけで、ポリシーが許可しない限りネットワークもない。サンドボックスに一度も入らないシークレットは、そこで動くコードに持ち出されることがない。

| ハーネスで動くもの | サンドボックスで動くもの |
|---|---|
| ファイル系ツール（`read_file`、`write_file`、`edit_file` など） | `shell`、`run_python`（`tools/packs/exec.py`） |
| プランニング系ツール、メモリ、スキル | `run_background`（`tools/packs/tasks.py`） |
| `web_fetch`、`web_search`（`tools/packs/web.py`） | モデル定義ツール（`tools/packs/meta.py` の `DynamicTool`） |

## `Sandbox` プロトコル

`sandbox/base.py`。どのバックエンドも `name`、`workspace` と次を備える。

```python
run(argv, *, cwd=None, timeout=120.0, stdin=None) -> SandboxResult
run_shell(command, *, cwd=None, timeout=120.0) -> SandboxResult
run_python(code, *, cwd=None, timeout=120.0, stdin=None) -> SandboxResult
path_inside(rel) -> str          # workspace-relative path as seen inside the sandbox
isolates_secrets() -> bool       # True if the host environment cannot leak in
```

`SandboxResult` は `stdout`、`stderr`、`exit_code`、`timed_out`、`duration_s`、`backend` を持つ。`ok` は終了コード 0 かつタイムアウトなしを意味する。`exec.py` の `format_output` が、`[exit code 0, 0.3s, sandbox=local]` のようなヘッダを付けてモデル向けに整形する。

## `SandboxPolicy`

`sandbox/policy.py`。ポリシーはデータであり、各バックエンドが可能な範囲で強制する。

```python
SandboxPolicy(env_allow=[], env={}, network=False, allowed_domains=[],
              deny_read=[], deny_write=[], timeout_s=120.0, memory_mb=2048,
              cpus=2.0, pids=256, image="python:3.12-slim", user="1000:1000", workdir="/work")
```

環境変数は常に許可リストであり、拒否リストではない。

- `BASE_ENV_ALLOW`: `PATH`、`LANG`、`LC_ALL`、`TZ`、`PYTHONIOENCODING`、`PYTHONUTF8`
- `POSIX_ENV_ALLOW`: `HOME`、`USER`、`TMPDIR`、`SHELL`、`TERM`
- `WINDOWS_ENV_ALLOW`: `SYSTEMROOT`、`COMSPEC`、`PATHEXT`、`TEMP`、`TMP`、およびプロセスがそこで起動するために必要なその他の変数
- `env_allow`: プロファイルが通すホスト側の追加変数
- `env`: サンドボックス内で設定される明示的な値

`host_env()` はこれらからローカルのサブプロセス環境を組み立て、`PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`、`MPLBACKEND=Agg` を既定値とする。`container_env()` はその既定値に `HOME=/tmp` と `env` だけを加える。ホストの環境変数はコンテナに一切入らない。

## バックエンド

### `LocalSandbox`（`sandbox/local.py`）

保証すること。環境変数は許可リストから作られるため、ハーネスプロセス内の認証情報は子プロセスに届かない。作業ディレクトリはワークスペース内でなければならない（そうでなければ終了コード 126）。タイムアウトはプロセスを殺す（終了コード 124）。Python は `-I` 付きで動く。`WORKSPACE` と `PYTHONPATH` はワークスペースを指す。

保証できないこと。ワークスペース外の読み取り。子プロセスは同じ OS ユーザーで動くためである。ネットワーク。パケットフィルタがないためである。`isolates_secrets()` は `False` を返す。自分の機械で信頼できる入力を扱う場合向けである。

### `DockerSandbox`（`sandbox/docker.py`）

1回の呼び出しごとにコンテナを1つ作り、終わったら破棄する。

| フラグ | 理由 |
|---|---|
| `--rm` | ワークスペース以外、呼び出しの後には何も残らない |
| `--network none` | ネットワークなし。`policy.network` が true のときだけ `bridge` |
| `--read-only --tmpfs /tmp` | イメージを書き換えられない |
| `-v <workspace>:/work` | 書き込める唯一のホストパス |
| `--user 1000:1000` | コンテナ内で root ではない |
| `--cap-drop ALL` | カーネルケーパビリティなし |
| `--security-opt no-new-privileges` | 権限昇格なし |
| `--memory`、`--cpus`、`--pids-limit` | ポリシーによる資源制限 |
| `-e` は `container_env()` のものだけ | ホストの環境変数は一切入らない |

`DockerSandbox.available()` は `docker info` を実行する。失敗すると `_exec` が `SandboxUnavailable` を投げる。

### `RemoteSandbox`（`sandbox/remote.py`）

ホスト型サンドボックスのためのアダプタである。1回の実行につき HTTPS 呼び出しが1回。

```
POST {base_url}/run
{"argv": [...], "cwd": "relative/dir", "timeout": 120, "stdin": "...", "workspace_id": "..."}
-> {"stdout": "...", "stderr": "...", "exit_code": 0, "timed_out": false}
```

ファイルの同期は提供側の仕組みに任せる。アダプタはプログラムを走らせるだけである。提供側に合わせるには `_post` を差し替える。`base_url` がなければ `SandboxUnavailable` を投げる。

## 起動時検査

`sandbox/startup_check.py`。`make_sandbox(backend, workspace, policy, **kwargs)` がバックエンドを組み立てる。`check_sandbox(sandbox, has_exec_tools=..., allow_unsafe_local=...)` は、最初のターンの前に `Profile.build` の中で走る。

- `allow_unsafe_local: true` なしで `local` に exec ツール: `ConfigError`
- それを付けて `local` に exec ツール: ファイルシステムとネットワークが隔離されていないという警告
- エンジンが動いていない `docker`: `SandboxUnavailable`
- `sandbox:` の節がまったくない状態で exec ツール: `ConfigError`

## プロファイルのキー

`profiles/template_app.yaml` より。

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

バックエンド固有のキーは、コンストラクタ引数としてそのまま渡される。`remote` には `base_url`、`token`、`workspace_id`、`docker` には `docker_bin`。

## ツールの振り分け

- `shell` は `ctx.sandbox.run_shell` を呼び、`run_python` は `ctx.sandbox.run_python` を呼ぶ。どちらもサンドボックスがなければ検証で失敗する。`shell` は 100 秒以上の `sleep` も拒否し、`run_background` を案内する。
- `run_background` は `ctx.sandbox.run_shell` を呼ぶスレッドを起動し、結果を `.harness/tasks/<id>.out` に書く。`task_output` はモデルがまだ見ていないバイトだけを返す。
- `define_tool` はコードをワークスペースの `tools/<name>.py` に保存する。できあがった `DynamicTool` を呼ぶと、`runpy.run_path(sandbox.path_inside(...))` でそのファイルを読み込むラッパとともに `ctx.sandbox.run_python` が走り、入力は stdin に JSON で渡される。モデルが作ったツールにできるのは `run_python` にできることと正確に同じであり、それ以上ではない。
- `web_fetch` はハーネス内で動き、取得したものを実行することはない。`validate` は `http` と `https` だけを受け付け、ホスト名をプロファイルの `web.allowed_domains` と照合し（`fnmatch`、任意は `"*"`）、`_is_private` によってプライベート、リンクローカル、ループバック、予約済み、マルチキャスト、名前解決できないアドレスを拒否する。これにより、エージェントを経由して内部サービスやクラウドのメタデータエンドポイントに到達することを防ぐ。HTML はテキストに落とし、`MAX_FETCH_CHARS`（40,000）で打ち切る。

## 比較

| | `local` | `docker` | `remote` |
|---|---|---|---|
| 環境変数の隔離 | 許可リストのみ | 許可リストのみ、ホストの環境変数は一切入らない | 提供側による |
| ファイルシステムの隔離 | なし、同じ OS ユーザー | ワークスペースのマウントのみ、イメージは読み取り専用 | 提供側による |
| ネットワーク | ホストのネットワーク、フィルタなし | `network: true` でなければなし | 提供側による |
| 資源制限 | タイムアウトのみ | メモリ、cpus、pids、タイムアウト | リクエスト内のタイムアウト |
| `isolates_secrets()` | `False` | `True` | `True` |
| 想定用途 | 開発、信頼できる入力 | 1台のホストへの配置 | ホスト型ランナーへの配置 |

## 環境変数の除去の証明

`tests/test_sandbox.py` を元にした例。

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

## 実行する

```
python -m pytest tests/test_sandbox.py -q
```

環境変数の除去、許可リストの通過、cwd の牢、タイムアウト、起動時検査、docker のフラグ一式、そしてホストのシークレットを見ることができない exec ツールをループ全体で通す検査を扱う。

次: [docs/ja/07_compaction.md](07_compaction.md)
