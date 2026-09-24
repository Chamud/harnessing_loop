# ツール

[English](../02_tools.md) · [索引](README.md) · [用語集](GLOSSARY.md)

この文書では、`harnessing_loop/tools/base.py` の `Tool` プロトコル、呼び出しのパイプライン、ディスパッチャ、レジストリ、パック、そしてツールの追加方法を扱う。

## Tool クラス

ツールとは、名前、説明、JSON スキーマ、いくつかの述語、そして `call` を持つクラスである。クラス属性は次のとおり。

| 属性 | 既定値 | 意味 |
|---|---|---|
| `name`、`description` | `""` | モデルに送られる |
| `input_schema` | 空のオブジェクトスキーマ | `call` の前に検証される |
| `category` | `"other"` | `read`、`edit`、`exec`、`plan`、`web`、`agent`、`meta`、`other` |
| `read_only` | `False` | ほとんどのモードで確認なしに実行してよい |
| `concurrency_safe` | `False` | 隣接する呼び出しと並行に実行してよい |
| `destructive` | `False` | 参考情報のフラグ |
| `max_result_chars` | `None` | `None` は config を使う。`0` はディスクへ退避しない |
| `defer` | `False` | `tool_search` が読み込むまでスキーマを送らない |

既定値は安全側に倒れる。ツールは自ら宣言するまで、読み取り専用でも並行実行安全でもない。上書きできるメソッドは `is_read_only`、`is_concurrency_safe`、`permission_subjects`、`paths`、`validate`、`check_permissions`、`call`。

## ToolResult と例外を投げない規則

`ToolResult.ok(content, data=None, **evidence)` は成功を組み立てる。キーワード引数は呼び出しの後に `RunState.evidence` へ統合される。`ToolResult.error(message)` はメッセージを `<tool_error>...</tool_error>` で包み、`is_error` を立てる。

ツールはループに例外を投げない。モデルが読むべきものはすべて `ToolResult.error(...)` で返す。抜け出した例外はパイプラインが捕まえ、短いトレースバックを伴うエラー結果になる。

## ToolContext

`ToolContext` は、ツールが触れてよいものすべてである。`workspace`、`state`、`config`、`sandbox`、`file_state`、`events`、`registry`、`permissions`、`hooks`、`redactor`、`model`、`gates`、`checkpoints`、`deps`、`tasks`、`extra`、`depth`。補助として `harness_dir`、`resolve(path)`（ワークスペース基準。逸脱の検査はしない）、`inside_workspace(path)` がある。

## `@tool` デコレータ

```python
from harnessing_loop.tools.base import ToolResult, tool

@tool(
    "line_count",
    "Count lines in a workspace file.",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
    read_only=True,
    category="read",
    subjects=lambda i: [i.get("path", "")],
    paths=lambda i: [i.get("path", "")],
)
def line_count(input, ctx):
    p = ctx.resolve(input["path"])
    if not ctx.inside_workspace(p):
        return ToolResult.error("path is outside the workspace")
    if not p.exists():
        return ToolResult.error(f"no such file: {input['path']}")
    return str(len(p.read_text(encoding="utf-8").splitlines()))
```

デコレータは `Tool` のインスタンスを返す。素の文字列を返した場合は `ToolResult.ok(str)` になる。`concurrency_safe` を与えなかった場合、既定は `read_only` と同じになる。

## validate_schema

`validate_schema(schema, value)` は小さな部分集合だけを検査する。`type`（単一またはリスト。`bool` は `integer` でも `number` でもない）、`enum`、`required`、`additionalProperties: false`、入れ子の `properties`、`items`、`minLength`、`maxLength`、`minimum`、`maximum`。エラー文字列か `None` を返す。

## 呼び出しごとのパイプライン

`tools/pipeline.py` の `run_tool_call(call, ctx)`。どの出口も `ToolResultBlock` である。

1. レジストリを引く。未知の名前や遅延中の名前は、対処方法を示すエラーを返す。
2. 入力のスキーマ検証。
3. `tool.validate(input, ctx)`。ここでの異常終了もエラー結果になる。
4. ツール実行前フック。フックは実行の停止、拒否、入力の書き換え、文脈の追加ができる。
5. `safety/permissions.py` による権限判定。
6. `tool.call`。例外はエラー結果になる。
7. ツール実行後フック（`POST_TOOL_USE` または `POST_TOOL_FAILURE`）。
8. 成功時、証跡を状態へ統合する。

出ていく途中で `finish()` が秘匿化を適用し、次にサイズ制御を行い、そして `tool_end` を発行する。

## ディスパッチ

`tools/dispatch.py` の `run_tool_calls`。`partition` は、連続する並行実行安全な呼び出しを1つのバッチにまとめる。安全な呼び出しが2つ以上あるバッチは、`config.max_tool_concurrency` を上限とするスレッドプールで動く。それ以外の呼び出しはすべて単独で、順に動く。結果は元の呼び出し順で戻る。

## レジストリと遅延ツール

`tools/registry.py`。`schemas()` は名前順に並べたスキーマを出力し、まだ読み込まれていない遅延ツールを飛ばす。`deferred_stub_text()` はそれらをシステムプロンプト向けに列挙する。`tool_search` は `load_deferred(names)` を呼ぶ。`apply_deny_rules` は包括的な deny のあるツールを取り除き、モデルにそれが見えないようにする。

## パック

`tools/packs/__init__.py` の `make_pack(name)`。

| パック | ツール | 備考 |
|---|---|---|
| `files` | `read_file`、`write_file`、`edit_file`、`list_dir`、`glob_files`、`grep_files` | プロセス内。パスを検査する |
| `exec` | `shell`、`run_python` | サンドボックスが必要 |
| `planning` | `todo_write`、`set_phase`、`notes_append`、`finish` | `finish` はゲートを通るまで拒む |
| `web` | `web_fetch`、`web_search` | ドメイン許可リスト |
| `agents` | `subagent` | 子ループ。既定では読み取り専用ツールのみ |
| `tasks` | `run_background`、`task_output`、`task_stop` | サンドボックスが必要 |
| `meta` | `tool_search`、`define_tool` | モデルが作ったツールは `tools/` の下に残る |

## 権限判定の対象とパス

`permission_subjects(input)` は、ルールのパターンと照合される文字列を返す。`shell` はその部分コマンドを返すため、`ls && git push` の中の `git push` に `shell(git *)` が一致する。ファイル系ツールはパスを返し、`web_fetch` はホスト名を返す。`paths(input)` はガード用のファイルシステム上のパスを返す。保護パスは、バイパスモードでも生き残る ask を引き起こす。

## プロファイルにツールを追加する

方法は2つある。`extra_tools` でインスタンスを付ける。

```python
from harnessing_loop.profiles.base import Profile

profile = Profile.from_dict({"name": "demo", "tool_packs": ["files"], "permissions": {"mode": "accept_edits"}})
profile = profile.with_overrides(extra_tools=[line_count])
rt = profile.build("./work", model="fake")
```

あるいはパックを追加する。`tools/packs/` の下のモジュールに `make() -> list[Tool]` を書き、`PACKS` に登録し、`tool_packs` の下でその名前を指定する。

## 実行する

```
python -m pytest tests/test_tools.py -q
```

次: [docs/ja/03_results_and_context.md](03_results_and_context.md)
