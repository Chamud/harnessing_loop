# 権限とフック

[English](../05_permissions_and_hooks.md) · [索引](README.md) · [用語集](GLOSSARY.md)

この文書では、ツール呼び出しがどのように許可され、拒否され、あるいは人間に回されるか、そしてフックが権限を広げずに実行を導く仕組みを扱う。

## モード

プロファイルの `permissions.mode`。`safety/permissions.py` の `MODES` のいずれかである。

| モード | 挙動 |
|---|---|
| `default` | 読み取り専用ツールは実行される。それ以外は allow ルールか、ask ハンドラからの承諾が必要 |
| `accept_edits` | `default` と同じで、加えてすべてのパスがワークスペース内にあるとき `edit` 系ツールが実行される |
| `plan` | 読み取り専用ツールのみ。それ以外はすべて拒否される |
| `bypass` | 下記のバイパス不可の検査を除き、すべて実行される |
| `dont_ask` | `default` と同じだが、バイパス不可でない「ask」はすべて deny になる。無人実行向け |

## ルール

`safety/rules.py`。`allow`、`deny`、`ask` の下にある文字列1つが1つのルールである。

```yaml
permissions:
  mode: default
  allow: ["shell(pytest *)", "read_file"]
  deny:  ["shell(git *)", "shell(rm -rf *)"]
  ask:   ["shell(npm publish*)"]
```

- `shell`（または `shell(*)`）は、そのツールのあらゆる呼び出しに一致する。
- `shell(git *)` は、パターンが判定対象に一致したときに一致する。`git *` は裸の `git` にも一致する。
- 実際のパターンを持つルールは `content_specific` である。一致判定は大文字小文字を区別する `fnmatch` である。

判定対象は `Tool.permission_subjects(input)` から来る。ファイル系ツールではパス、`shell` と `run_background` では `tools/packs/exec.py` の `split_subcommands(command)` であり、これは `&&`、`||`、`;`、`|`、改行で分割する。判定対象のどれか1つが一致すればルールは一致するため、`ls && git push` は `shell(git *)` に捕まる。これは同時に、`shell(pytest *)` が `pytest -q && rm -rf /` を許してしまうことも意味する。危険な接頭辞は、最初に検査される `deny` に入れる。

包括的な deny は、ツールをレジストリから完全に取り除く（`Registry.apply_deny_rules`）。

## 判定順序

`safety/permissions.py` の `decide(tool, input, ctx, *, hook_allow=False)`。最初に一致したものが勝つ。

1. そのツールに対する deny ルール: deny
2. 内容依存の ask ルール: ask（バイパス不可）
3. 保護パスのガード: ask（バイパス不可）
4. ツール自身の `check_permissions`: passthrough でなければ、その答え
5. モード `bypass`: allow
6. 包括的な ask ルール: ask
7. allow ルール: allow
8. モードごとの既定（読み取り専用、ワークスペース内の編集）: allow または deny
9. passthrough: ask
10. モード `dont_ask` は ask を deny に変える

手順10は `_ask` の内部にあるため、手順2から9までのすべての ask に効く。すべての判定は `ctx.log` に記録される。

## バイパス不可の検査

これらは `Decision(..., immune=True)` を返し、どのモードでも効き続ける。

- deny ルール（手順1）
- 内容依存の ask ルール（手順2）
- 保護パス（手順3）。`safety/guards.py` にある。`PROTECTED_RELATIVE`: ワークスペース内の `.harness/`、`.git/`、`.hg/`、`.svn/`、`.env`、`.envrc`、`profile.yaml`、`hooks.yaml`。`PROTECTED_HOME`: `.ssh/` や `.gnupg/` のような認証情報のディレクトリと、シェルの起動ファイル。読み取り専用でない呼び出しだけが検査される。`PermissionContext.allow_unsafe_paths` でこの検査を切れる。

これらのファイルを編集することが、エージェントが自分の権限を広げる手口である。

## ask ハンドラ

`AskHandler = Callable[[str, dict, str], bool]` であり、`handler(tool_name, input, reason)` として呼ばれる。CLI がこれを接続するのは stdin が端末のときだけである。ハンドラがなければ、ask は理由 `(no ask handler; unattended run)` を伴う deny になる。例外を投げるハンドラは deny である。`dont_ask` モードでは、バイパス不可の ask だけがハンドラに届く。

## 危険な allow ルール

`shell(python *)` のような allow ルールは、任意のコード実行を許すことになる。インタプリタを通せば何でも実行できるためである。ルールを書いたのが操作者であり、本当の境界がサンドボックスであるなら、これは受け入れられる。したがって既定では、そうしたルールはそのまま残る。

完全には信頼できない出所からルールが来る場合は、`permissions.strip_dangerous_allows: true` を設定する。すると `Profile.build` が `safety/guards.py` の `strip_dangerous_allow_rules` を実行する。パターンが `CODE_EXEC_PREFIXES` のコード実行接頭辞（`python`、`bash`、`sudo`、`curl`、`npx`、`pip install` など）であり、かつ `prefix`、`prefix *`、`prefix*`、`prefix:*` のいずれかの形をしている allow ルールは捨てられる。`run_python` のようにパターンを持たない裸のルールは残る。捨てられたルールは `runtime.extra["dropped_allow_rules"]` に並ぶ。

## フック

`safety/hooks.py`。イベントと、実際に送られるペイロードのキー。

| イベント | ペイロード | できること |
|---|---|---|
| `session_start` | `workspace`、`profile` | 文脈の追加 |
| `user_prompt` | `prompt` | 文脈の追加、停止 |
| `pre_tool_use` | `tool`、`input`、`state` | allow、deny、ask、入力の書き換え、文脈の追加、停止 |
| `post_tool_use` | `tool`、`input`、`result`、`is_error` | 文脈の追加、停止 |
| `post_tool_failure` | 上と同じ | 文脈の追加、停止 |
| `stop` | `final_text`、`state`、`reentry` | 停止、停止の阻止 |
| `pre_compact` | `messages` | 観測 |
| `post_compact` | `summary` | 観測 |
| `session_end` | `reason`、`turns` | 観測 |

2種類ある。

- `python`: `HookRegistry.on(event, fn, matcher=None, name="")`。`fn(payload)` は `HookResult`、dict、`None`、bool（allow か deny）、文字列（文脈）のいずれかを返す。
- `command`: `HookRegistry.command(event, argv, matcher=None, timeout=60.0, name="")`。ペイロードは stdin に JSON で届く。

`matcher` はペイロードの `tool` に対する `fnmatch` パターンである。`None` はすべてに一致する。

コマンドの出力。終了コード `2` は阻止で、stderr が理由になる。終了コード `0` で stdout に JSON があれば、次のように解釈される。

```json
{"decision": "allow" | "deny" | "ask", "reason": "...",
 "updated_input": {...}, "additional_context": "...",
 "stop": false, "block_stop": false}
```

JSON でない stdout は `additional_context` になる。それ以外の非ゼロ終了は無視される。タイムアウト、起動の失敗、例外を投げた Python フックは、いずれも deny として数える。

`HookRegistry.run` における併合の優先順位。1つでも deny があれば deny、次に ask、次に allow。`stop` と `block_stop` は OR で併合される。文脈は連結される。空でない `updated_input` の最後のものが勝つ。

### フックと権限が出会う場所

`tools/pipeline.py` は `pre_tool_use` を、スキーマ検証の後、`decide` の前に実行する。書き換えられた入力はもう一度検証される。フックの deny は、呼び出しを `Blocked by hook` で終える。フックの allow は `decide(..., hook_allow=True)` になり、`replace_handler` によって自動承認する ask ハンドラに差し替える。通常の ask は解決される。deny ルールは依然として拒否し、バイパス不可の ask は元のハンドラに回る。フック実行後の文脈は、`<hook_context>...</hook_context>` として結果に付け足される。

### 停止フック

`Loop._on_model_stop` は、モデルがツール呼び出しなしでターンを終えたときに停止フックを実行する。`stop: true` は実行を `hook_prevented` として終える。`block_stop: true` は `state.stop_hook_active` を立て、理由をユーザーメッセージとして戻し、続行する。次の stop イベントは `reentry: true` を伴う。2回目の `block_stop` は警告にとどまるため、フックがモデルを永久に回し続けることはできない。

### フックの宣言

プロファイルの YAML では次のように書く（`command` は文字列かリスト。`matcher`、`timeout`、`name` は任意）。

```yaml
hooks:
  - {event: pre_tool_use, matcher: "shell", command: ["python", "hooks/check.py"], timeout: 30}
```

Python では、`build` の前でも後でもよい。

```python
from harnessing_loop.safety.hooks import Events
from harnessing_loop.profiles.base import load_profile

profile = load_profile("coder")
profile.python_hooks.append((Events.PRE_TOOL_USE, lambda p: {"decision": "deny", "reason": "not today"}, "write_*"))
runtime = profile.build("./work")

runtime.hooks.on(Events.POST_TOOL_USE, lambda p: {"additional_context": "remember to cite"})
```

`python_hooks` の要素は `(event, fn, matcher)` のタプルである。

## 実行する

```
python -m pytest tests/test_permissions.py tests/test_hooks.py -q
```

`test_permissions.py` はルール、モード、bypass 下でのバイパス不可性、剥ぎ取りを扱う。`test_hooks.py` は deny、matcher、入力の書き換え、終了コード 2、併合の優先順位、フックの allow と deny ルールの優先関係を扱う。

次: [docs/ja/06_sandbox.md](06_sandbox.md)
