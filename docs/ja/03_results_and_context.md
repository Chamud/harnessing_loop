# ツール結果とトークン計算

[English](../03_results_and_context.md) · [索引](README.md) · [用語集](GLOSSARY.md)

この文書では、ツール結果がモデルに見えるまでにどう上限を課され、秘匿化され、数えられるか、そしてハーネスがモデルの読んだものをどう追跡するかを扱う。

## サイズ制御

`tools/results.py`。2段ある。1つは呼び出しごと、もう1つはメッセージごとである。

### 呼び出しごと: `size_control`

`size_control(text, tool=..., call_id=..., workspace=..., config=...)` は、秘匿化のあと、パイプラインの `finish()` の中で走る。

1. 空、または空白だけのテキストは `EMPTY_MARKER`、すなわち文字列 `(no output)` になる。空のブロックに悪く反応するモデルがある。
2. 上限は `cap_for(tool, config)` である。ツールの `max_result_chars` を `config.tool_result_max_chars`（既定 50,000）で切り詰めた値。`None` は設定値を使う意味。`0` は決して退避しない意味で、`read_file` のようにツール自身が上限を課す場合である。
3. テキストが上限より長ければ、`persist()` が `.harness/tool-results/<call_id>.txt` に書き出し、モデルは代わりに `large_result_text(...)` を受け取る。

置き換えのテキストは次のようになる。

```
<persisted_output>
Output too large (120,000 chars). Full output saved to: .harness/tool-results/tu_abc.txt

Preview (first 2,000 chars):
...
</persisted_output>
```

プレビューの長さは `config.tool_result_preview_chars`（既定 2,000）である。`persist` は既存のファイルを上書きしないため、トランスクリプトを再生しても元の出力が残る。

### メッセージごと: `apply_message_budget`

ループは、1回のアシスタントターンから出た結果の束全体に対して `apply_message_budget(results, workspace=..., config=...)` を呼ぶ。合計の長さが `config.tool_results_budget_per_message`（既定 200,000）を超えると、大きいものから順に1つずつ退避し、合計が収まるまで続ける。すでに `<persisted_output>` で始まる結果は飛ばす。

### マーカー

| 定数 | 値 | 使う側 |
|---|---|---|
| `EMPTY_MARKER` | `(no output)` | `size_control` |
| `CLEARED_MARKER` | `[old tool result cleared]` | 古い結果を破棄するときの `context/microcompact.py` |

## ファイル状態キャッシュ

`tools/file_state.py` の `FileStateCache`。モデルが何を読んだかを、内容と mtime とともに、正規化した絶対パスをキーにして記録する。キャッシュは `max_entries`（既定 100）件を保持し、古い項目から先に落ちる。`Runtime.__post_init__` は与えられなかったときに1つ作り、`Runtime.child()` はサブエージェントに `clone()` を渡す。

`write_file.validate` と `edit_file.validate` から呼ばれる `check_before_edit(path)` が強制する規則。

| 状況 | 結果 |
|---|---|
| ファイルが存在しない | 許可。ファイルの作成に事前の読み取りは要らない |
| 一度も読まれていない、または `partial=True` で読まれた | `File has not been read in full yet. Read it first, then edit.` |
| mtime が読み取り時より新しく、内容が異なる | `File has been modified since it was read. Read it again before editing.` |
| mtime が新しく、内容は同一 | 許可。触られたが変わっていない |

`read_file` は読み取りのたびに `record(path, text, partial=...)` を呼ぶ。`offset` か `limit` を伴う読み取り、または `MAX_READ_CHARS` で切り詰められた読み取りは partial である。`write_file` と `edit_file` は書き込みのあと新しい内容で `record` を呼ぶため、続けての編集は再度の読み取りなしに許可される。

## トークン推定

`context/tokens.py`。トークナイザは無い。2つの情報源を組み合わせる。

1. `usage` を持つ最も新しいアシスタントメッセージ。その `input_tokens + cache_read_tokens + cache_write_tokens + output_tokens` は、そのメッセージまでのすべてについて正確である。
2. それより後のメッセージは `message_tokens` で推定する。テキスト、thinking、ツール入力、ツール結果に `estimate_tokens` を合計し、ツールブロックごとに 10 を加え、画像を `IMAGE_TOKENS`（1,500）として数える。

`estimate_tokens(text)` は `len(text) / CHARS_PER_TOKEN * PAD + 1` であり、`CHARS_PER_TOKEN = 4`、`PAD = 4/3` である。この余裕によって推定は多めに出るため、API でリクエストが失敗するのではなく、コンパクションが早めに走る。

`conversation_tokens(messages, system_tokens, tool_tokens)` は、`Loop._tokens` が `should_compact` と `is_blocking` に渡す数である。まだ usage を持つアシスタントメッセージが無いときは、すべてのメッセージと、システムおよびツールスキーマのトークンを推定する方に退く。

## 秘匿化

`safety/redact.py` の `Redactor(workspace)`。パイプラインは、サイズ制御の前にすべてのツール結果へこれを適用する。インスタンスを呼ぶと `paths`、続いて `secrets` が走る。

- `paths`: ワークスペースの絶対パスを `<workspace>` に、ホームディレクトリを `~` に置き換える。モデルは相対パスで考え、トランスクリプトは可搬なままになる。
- `secrets`: クラウドのアクセスキー ID、bearer トークン、秘密鍵ブロック、そしてキーに `SECRET`、`TOKEN`、`PASSWORD`、`PASSWD`、`API_KEY`、`ACCESS_KEY` を含む `KEY=value` の組を伏せる。キーと値の一致は `KEY=<redacted>` になり、それ以外は `<redacted>` になる。

追加のパターンは `Redactor(workspace, extra_patterns=[...])` として渡せる。

## 例

```python
from harnessing_loop.core.state import RunConfig
from harnessing_loop.tools.base import Tool
from harnessing_loop.tools.results import size_control

cfg = RunConfig(tool_result_max_chars=100, tool_result_preview_chars=20)
t = Tool()
t.name = "x"
out = size_control("a" * 500, tool=t, call_id="c1", workspace=workspace, config=cfg)
assert out.startswith("<persisted_output>") and "c1.txt" in out
assert size_control("   ", tool=Tool(), call_id="c", workspace=workspace, config=RunConfig()) == "(no output)"
```

`workspace` は任意の `Path` である。ファイルは `workspace / ".harness" / "tool-results" / "c1.txt"` に置かれる。

## 実行する

```
python -m pytest tests/test_tools.py tests/test_compact.py -q
```

次: [docs/ja/04_state_on_disk.md](04_state_on_disk.md)
