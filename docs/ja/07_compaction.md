# コンテキスト管理

[English](../07_compaction.md) · [索引](README.md) · [用語集](GLOSSARY.md)

この文書は、ループが長い実行をコンテキストウィンドウの内側に収める方法を扱う。マイクロコンパクション、完全なコンパクション、そのあとの再構築、プロンプトキャッシュの配置、そしてシステムプロンプトに入るものとターンごとのリマインダに入るものの違いである。

## 3つのしきい値

モデル呼び出しの前に毎回、`harnessing_loop/core/loop.py` の `Loop._manage_context` が次のリクエストを `Loop._tokens()` で見積もり（システムブロック + ツールスキーマ + `conversation_tokens`。後者は最後の正確な使用量報告から始め、それ以降のメッセージを3分の1だけ上乗せして数える）、3本の線と比較する。

| しきい値 | 既定値 | 動作 |
|---|---|---|
| `microcompact_min_tokens` | 40,000 | 古いツール結果を破棄する |
| `context_window_tokens - compact_buffer_tokens` | 200,000 - 13,000 | 完全なコンパクション |
| `context_window_tokens - blocking_buffer_tokens` | 200,000 - 3,000 | コンパクションが無効か、失敗し続けるなら `blocking_limit` |

これらの値は `harnessing_loop/core/state.py` の `RunConfig` のフィールドである。比較そのものは `harnessing_loop/context/compact.py` の `should_compact` と `is_blocking` である。プロファイルは `context:` の下で設定する。ウィンドウは `limits.context_window_tokens` である。

```yaml
context:
  compact: true
  compact_buffer_tokens: 13000
  blocking_buffer_tokens: 3000
  compact_max_output_tokens: 20000
  compact_max_failures: 3
  microcompact_keep_recent: 5
  microcompact_min_tokens: 40000
```

## マイクロコンパクション

`harnessing_loop/context/microcompact.py` の `microcompact(messages, keep_recent, compactable)` は、古いツール結果の内容を `CLEARED_MARKER`（`harnessing_loop/tools/results.py` の `"[old tool result cleared]"`）に置き換える。

1. 対象になるのは `COMPACTABLE` に入っているツールの結果だけである。`read_file`、`shell`、`run_python`、`grep_files`、`glob_files`、`list_dir`、`web_fetch`、`web_search`、`edit_file`、`write_file`、`task_output`。planning ツールの結果は残る。
2. すでに破棄済みのもの、すでにディスクに退避済みのもの（`<persisted_output>`）は飛ばす。
3. 候補のうち最後の `keep_recent` 件は残し、それより古いものはすべて破棄する。
4. `ToolUseBlock` と `ToolResultBlock` の対はそのままの位置に残るため、API が要求する構造は壊れない。
5. 関数は新しいリストを返す。入力のメッセージは変更しない。

## 完全なコンパクション

`harnessing_loop/context/compact.py` の `compact(model, messages, config, keep_tail=True)`。

1. `split_tail(messages, keep_tokens=TAIL_KEEP_TOKENS)` が見積もり 8,000 トークン分まで後ろから辿り、ツール結果でもハーネスからのメッセージでもないユーザーメッセージのところで切る。末尾はそのまま残し、前半を要約する。
2. `build_summary_prompt(head)` が前半を `render_for_summary` で描画し（結果は `MAX_RESULT_CHARS_IN_PROMPT` = 1,500 で切る）、`SUMMARY_SECTIONS` にある番号付きの節を求める。タスクと意図、重要な事実と決定、ファイル、エラーと修正、検証、未了の作業、現在の手順、次の行動である。
3. `summarize` は `tools=[]`、`thinking="off"`、`max_output_tokens=compact_max_output_tokens` で `ModelRequest` を送る。前置きは "Reply with TEXT ONLY. Do not call tools." と言う。
4. `parse_summary` は `<summary>` タグの間のテキストを取る。タグがなければ返答全体を取る。要約が空なら `RuntimeError("empty summary")` を投げる。

## 再構築

`harnessing_loop/context/rebuild.py` の `build_post_compact` は `[boundary] + tail` を返す。boundary は `meta=True, kind="compact_boundary"` を持つ1つのユーザーメッセージで、次の順に内容を含む。

- 継続の指示と `<summary>` ブロック
- トランスクリプトのパス（あれば）
- `<notes>` の中の `.harness/notes.md` と `<todo>` の中の `.harness/todo.json`
- 現在のフェーズと、最後に書いたファイル20件
- 直近で読んだファイル最大 `MAX_FILES` = 5 件。ディスクから読み直して `<file path="...">` の中に入れる。1件あたり 20,000 文字、合計 120,000 文字が上限

ここに出したファイルはファイル状態のキャッシュに再記録される。それ以外のエントリは忘れられるため、モデルは編集の前にもう一度ファイルを読まなければならない。

## 失敗とサーキットブレーカー

- `Loop._compact` が失敗するたびに `state.compact_failures` が増え、成功でリセットされる。`compact_max_failures` に達するとループは試行をやめ、ブロッキング線を越えた時点で `Terminal("blocking_limit", ...)` で終わる。
- `compact: false` の場合、実行はブロッキング線で `blocking_limit` として終わる。
- それでもプロバイダがリクエストを拒む場合、クライアントは `PromptTooLong`（`harnessing_loop/core/errors.py`）を投げる。ループは `force=True` で一度コンパクションし（会話全体、末尾を残さない）、`Continue("compact_retry")` を返す。2度目の `PromptTooLong` は `Terminal("prompt_too_long", ...)` で終わる。

## プロンプトキャッシュの配置

`harnessing_loop/llm/cache_layout.py` は、リクエストを `tools -> system static -> system dynamic -> messages` の順に並べ、3つのマーカーを置く。

| 関数 | マーカー |
|---|---|
| `tool_schemas_with_cache` | 名前順に並べたあとの最後のスキーマに置く |
| `system_blocks` | 静的なシステムブロックにのみ置く |
| `messages_with_cache` | 最後のメッセージの、thinking ではない最後のブロックに置く |

キャッシュプレフィックスを安定させる規則。スキーマは名前順に並べ、実行の途中で並べ替えない。静的なシステムテキストは一度だけ組み立て、以後変えない。ターンごとに変わるもの（日付、フェーズ、リマインダ）は動的ブロックかユーザーメッセージに入れる。

## システムプロンプトの組み立て

`harnessing_loop/context/system_prompt.py` の `assemble_static_prompt(profile_prompt, workspace=..., registry=..., skills=...)` が、プロファイルのプロンプト、指示ファイル、スキルの一覧、遅延ツールのスタブをつなぐ。

指示ファイルの名前は `AGENT.md` である。`load_instruction_files` は `~/.harnessing_loop/AGENT.md` を読み、続いてファイルシステムのルートからワークスペースまで、各ディレクトリから1件ずつ読む。近いファイルが後に来る。`@path` という行は、そのファイルからの相対で別の `.md` または `.txt` ファイルを取り込む。深さは `MAX_INCLUDE_DEPTH` = 5 まで。`WARN_CHARS` = 40,000 を超えるファイルは警告になる。

`dynamic_prompt(runtime, state)` が日付、ワークスペース名、サンドボックスのバックエンド、フェーズを加える。`Runtime.dynamic_prompt` はこれを置き換える。

## ターンごとの添付

`harnessing_loop/context/attachments.py` の `build_attachments` は、ツール結果のメッセージに `<system_reminder>` ブロックを1つ付け足す。付けるのは差分があるときだけである。新たに読み込まれたか定義されたツール、終了したバックグラウンドタスク、未了の TODO リスト（`TODO_REMINDER_EVERY` = 10 ターンごと）、日付の変化、新しい操作者メッセージ。`AttachmentState` が何を通知済みかを覚えている。

## 実行する

```
pytest tests/test_compact.py -v
```

`test_full_compaction_in_loop_reinjects_notes` は boundary がノートを運ぶことを確認する。`test_compaction_circuit_breaker` は `blocking_limit` で終わる。

次: [docs/ja/08_progress.md](08_progress.md)
