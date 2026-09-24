# ディスク上の状態

[English](../04_state_on_disk.md) · [索引](README.md) · [用語集](GLOSSARY.md)

この文書では、ハーネスが `.harness/` の下に書くすべてのファイル、そこから実行をどう再開するか、そして操作者がそれらを通して動いている実行をどう操るかを扱う。

原則。会話は使い捨てである。コンテキストはコンパクションされ、切り詰められ、あるいはクラッシュで失われうる。本当の状態はファイルにあり、ループはそこから再構築される。

## `.harness/` ディレクトリ

ワークスペースの中に作られる。`.harness/` の下にはどのツールも書けない。保護パスである（`safety/guards.py` の `PROTECTED_RELATIVE`）。

| パス | 書く側 | 内容 |
|---|---|---|
| `transcript.jsonl` | `persistence/transcript.py` | すべてのメッセージ。1行に1つの JSON レコード、追記のみ |
| `events.jsonl` | `core/events.py` の `EventBus` | すべてのループイベント（`tool_start`、`compact`、`run_end`、…） |
| `state.json` | `core/loop.py` の `_save_state` | `STATE_KEYS` にある `RunState` のキー。各ツールターンのあとと実行の終わりに原子的に書き直される |
| `notes.md` | `tools/packs/planning.py` の `_append_note` | `notes_append`、`set_phase`、`finish` からのタイムスタンプ付きの行 |
| `todo.json` | `tools/packs/planning.py` の `TodoWrite` | TODO の一覧。`todo_write` のたびに置き換えられる |
| `tool-results/<call_id>.txt` | `tools/results.py` の `persist` | サイズ上限を超えたツール結果の全文 |
| `checkpoints/` | `persistence/checkpoints.py` | `index.json` と、変更される前のすべてのファイルのコピー |
| `tasks/<task_id>.out` | `tools/packs/tasks.py` | `run_background` コマンドの出力 |
| `control.json` | `persistence/control.py` | 操作者のフラグ。`cancel`、`pause`、`inbox` |

`STATE_KEYS` は `("turn", "phase", "phases_seen", "evidence", "files_written", "files_read", "todos", "notes", "compactions", "dynamic_tools")` である。

## トランスクリプト

`Transcript(path, session_id)` がレコードを追記する。メッセージのレコードは次のようになる。

```json
{"type": "message", "id": "m_...", "parent_id": "m_...", "role": "assistant",
 "content": [{"type": "text", "text": "..."}], "usage": null, "stop_reason": null,
 "meta": false, "kind": "normal", "ts": 1726650000.0, "session": "abc123"}
```

1. `append()` は `parent_id` に最後に書いた id を入れるため、ファイルは鎖になる。
2. `meta(kind, **data)` は `run_start` と `run_end` のレコードを書く。メッセージを再構築するときは飛ばされる。
3. `boundary(message)` は `"kind": "compact_boundary"` を持つメッセージレコードを書き、コンパクション直前の最後のメッセージを親にする。
4. `load_all()` は最後のレコードから `parent_id` をたどって戻るため、捨てられた分岐は落ちる。
5. `load_live()` は最後の `compact_boundary` 以降の鎖を取り、それに `normalize_for_api` を走らせる。それより前のすべては、境界メッセージの中の要約で代表される。
6. クラッシュで途切れた最後の行は無視する。`MAX_READ_BYTES`（200 MB）を超えるファイルは拒否する。

## 再開と孤児の修復

`Loop.resume(runtime)` はトランスクリプトを持つランタイムを必要とする。`load_live()` を呼び、新しい `RunState` を作り、`_load_state` で `state.json` から埋める。証跡のキー `finished` と `stop_requested` は読み込み時に落とされるため、実行は作業を続けられる。

続いて `run(None)` が何を送るかを決める。

- メッセージが無い: `Terminal("completed", 0, "nothing to resume")`
- 最後のメッセージがツール呼び出しの無いアシスタントの返答: `"Continue from where you left off."` をメタのユーザーメッセージとして追記する
- それ以外: そのまま続ける

孤児は `core/messages.py` の `normalize_for_api` が修復する。モデルがツールを要求した直後のクラッシュは、`tool_result` の無い `tool_use` を残す。`repair_orphans` は、そのアシスタントメッセージの直後に、`"Tool call was interrupted before it produced a result."` という合成のエラー結果を加える。続いて `drop_empty_assistant` が thinking か空白だけを持つアシスタントメッセージを取り除き、`merge_adjacent_user` が隣り合うユーザーターンをまとめ、先頭にあるユーザー以外のメッセージは落とされる。同じ関数がすべてのモデル呼び出しの前に走る。

```python
from harnessing_loop.core.loop import Loop

loop = Loop.resume(runtime)   # runtime built by a profile with transcript: true
result = loop.run(None)
```

CLI では `hloop resume --profile coder --workspace ./work`。

## ファイルチェックポイントと巻き戻し

`Checkpoints(workspace, max_entries=200)` は `.harness/checkpoints/index.json` と、`<seq>_<filename>` という名前のコピーを保持する。

- `snapshot(turn)`: ループが各反復の始めに呼ぶ。ターン番号を記録する。
- `backup(path)`: `write_file` と `edit_file` が書く前に呼ぶ。まだ存在しないファイルは `"backup": null` として記録される。
- `rewind(turn)`: `turn` 以降に触られたすべてのファイルを、その範囲で最も早いバックアップに復元し、存在しなかったファイルを削除し、対応する索引の項目を落とす。

```python
from harnessing_loop.persistence.checkpoints import Checkpoints

restored = Checkpoints(runtime.workspace).rewind(turn=12)   # list of restored paths
```

## 制御ファイル

`.harness/control.json` は各反復の始めに読まれ、ツール実行中にもう一度読まれる。

```json
{"cancel": false, "pause": false, "inbox": ["message for the agent"]}
```

- `cancel`: `Aborted` を投げる。実行は理由 `aborted` で終わる。ツール実行中は、それ以降のツール呼び出しを止める。
- `pause`: フラグが消えるか `cancel` が立つまで、ループは1秒刻みで眠る。
- `inbox`: 各項目は次のシステムリマインダの中で `Message from the operator: ...` として一度だけ届く。

`set_flag(workspace, **flags)` は読み、統合し、原子的に書く（一時ファイルと `os.replace`）。`inbox` の値は追記され、他のキーは置き換えられる。

```python
from harnessing_loop.persistence.control import set_flag

set_flag(workspace, pause=True)
set_flag(workspace, inbox="Stop after the tests pass.")
set_flag(workspace, pause=False)
```

`cli.py` からの同じ操作。

```
hloop control --workspace ./work --cancel
hloop control --workspace ./work --pause
hloop control --workspace ./work --resume
hloop control --workspace ./work --send "Stop after the tests pass."
```

## ノートと TODO

モデル自身の備忘であり、`tools/packs/planning.py` がディスクへ写す。

- `notes_append` は `- <timestamp> <text>` を `notes.md` と `state.notes` に書く。`set_phase` と `finish` も1行を追記する。
- `todo_write` は `todo.json` を `{"content": ..., "status": "pending" | "in_progress" | "completed"}` の一覧で置き換える。

コンパクションのあと、`context/rebuild.py` が両方のファイルを境界メッセージへ読み込むため、決定を生んだメッセージが消えても決定は残る。読み出し側は `persistence/notes.py` にあり、`read_notes(workspace, max_chars=20_000)` と `read_todos(workspace)` である。

## メモリ

`MemoryDir`（`persistence/memory.py`）は、個々のワークスペースの外にある長期の状態である。プロファイルは `memory: {dir: ~/.harnessing_loop/memory}` と設定する。相対のディレクトリはワークスペースを基準に解決される。

1ファイルに1つの事実、それに索引である `MEMORY.md` を加える。

```
---
name: prefers-tabs
description: User prefers tabs over spaces in Python
type: user
---

Use tabs.
```

`type` は `user`、`feedback`、`project`、`reference` のいずれかである。`write(name, description, type, body)` は name を slug 化し、ファイルを書き、索引を更新する。索引は1項目1行で、ファイルへの markdown リンクに続けてその説明を置き、`MAX_INDEX_LINES`（200）と `MAX_INDEX_BYTES`（25,000）で上限を課す。

`relevant(query, k=5)` は、クエリと `name` および `description` の間のキーワードの重なりで項目を採点し、同点なら新しいものを先にする。プロファイルは代わりに `selector(query, entries, k) -> list[str]` を差し込める。`recall_text(query)` は選ばれた各本文を `<memory name="..." type="...">` で包む。`Loop.run` は最初のプロンプトでこれを呼び、結果を `<system_reminder>` の中に付ける。

```python
from pathlib import Path
from harnessing_loop.persistence.memory import MemoryDir

mem = MemoryDir(Path("~/.harnessing_loop/memory").expanduser())
mem.write("deploy-url", "Staging deploy dashboard link", "reference", "https://example.invalid/deploy")
print(mem.recall_text("deploy dashboard"))
```

## 実行する

```
python -m pytest tests/test_resume.py tests/test_memory_skills_meta.py -q
```

`test_resume.py` は親の鎖、境界の読み込み、途切れた行、孤児の修復、状態の復元を扱う。`test_memory_skills_meta.py` はメモリの書き込み、索引、プロンプトへの想起を扱う。

次: [docs/ja/05_permissions_and_hooks.md](05_permissions_and_hooks.md)
