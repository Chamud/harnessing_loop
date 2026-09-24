# ツールセットを広げる

[English](../09_scaling_tools.md) · [索引](README.md) · [用語集](GLOSSARY.md)

この文書では、プロファイルが多くの能力を備えながら、そのすべてを毎ターン支払わずに済ませる6つの機構を扱う。遅延ツール、サブエージェント、バックグラウンドタスク、モデル定義ツール、スキル、そしてメモリ想起である。

| 機構 | 節約するもの | 使う場面 |
|---|---|---|
| 遅延ツール | 毎ターンのスキーマのトークン | 一部の実行でしか必要にならないツール |
| サブエージェント | 親のコンテキスト | 広い探索、脇道の調査 |
| バックグラウンドタスク | ターンの時間 | 数分かかるコマンド |
| モデル定義ツール | 繰り返される `run_python` の定型コード | 何度も呼ぶ計算 |
| スキル | システムプロンプトの大きさ | 一部のタスクでだけ必要な長い手順 |
| メモリ想起 | 実行ごとに事実を説明し直す手間 | 実行をまたぐ好みとプロジェクトの事実 |

## 遅延ツール

```yaml
defer_tools: [web_fetch, web_search, define_tool, task_stop, run_background]
```

`Profile.build` はそれぞれに `tool.defer = True` を設定する。`harnessing_loop/tools/registry.py` の `Registry.schemas()` はそれらをリクエストから外す。`Registry.deferred_stub_text()` は、`search_hint` か説明の最初の一文を使い、静的なシステムプロンプトに1行ずつ並べる。

`harnessing_loop/tools/packs/meta.py` の `tool_search` がそれらを読み込む。

- `select:web_fetch,web_search` は名前で読み込み、未知の名前を報告する。
- `fetch page` は各遅延ツールに点数を付け（名前に含まれる語1つにつき2点、説明またはヒントに含まれる語1つにつき1点）、上位 `max_results` 件、既定では5件を読み込む。

読み込まれていないツールを呼ぶと、`harnessing_loop/tools/pipeline.py` の `run_tool_call` がエラーを返す。`Tool web_fetch is not loaded. Call tool_search with query "select:web_fetch" first, then retry.`

コスト。ツールのスキーマ一覧はリクエストの最初のキャッシュ区間である。ツールを読み込むとそれが変わるため、プレフィックス全体が一度だけ再びキャッシュされる。

## サブエージェント

`harnessing_loop/tools/packs/agents.py` の `Subagent` は、まっさらなメッセージ履歴で子ループを走らせ、その最終テキストだけを返す。

1. 入力。`description`、`prompt`、任意の `tools` と `max_turns`（既定は30）。プロンプトがすべてを運ばなければならない。子はコンテキストを共有しない。
2. ツール。`tools` に挙げた名前、または既定では `read_only` が true のすべてのツール。`CHILD_EXCLUDED`（`subagent`、`finish`、`define_tool`、`set_phase`）は決して渡さない。
3. `harnessing_loop/core/runtime.py` の `Runtime.child(registry=..., system_prompt=..., max_turns=...)` が子を組み立てる。ワークスペース、モデル、サンドボックス、権限、フックは同じ。ファイル状態キャッシュは複製。ゲートとトランスクリプトはなし、`require_finish=False`、`depth + 1`。
4. `MAX_DEPTH` は2。それを超えると `validate` が拒否する。子のツール呼び出しは1つずつ権限判定される。
5. 結果は1つのツール結果である。`[subagent: <description> | <reason> in <n> turns]` と子の最終テキスト。`completed` で終わらなかった子はエラー結果として返る。証跡は `subagent_ran`。

## バックグラウンドタスク

`harnessing_loop/tools/packs/tasks.py`。サンドボックスが必要である。

- `run_background(command, timeout)` はスレッドを起動して `sandbox.run_shell` を呼び、コマンドが終わったときに出力を `.harness/tasks/<id>.out` へ書き、状態を `completed`、`failed`、`timed_out` のいずれかにする。権限の対象はサブコマンドである。
- `task_output(id, wait)` はタスクの `offset` より後のバイトだけを返し、そのあとで `offset` を進める。`wait: true` は最大120秒まで待つ。
- `task_stop(id)` は `stop_requested` を立てる。制限。プロセスは殺さない。タスクは次のタイムアウト検査で終わり、出力ファイルはコマンドが終わったときにしか現れない。

## モデル定義ツール

`harnessing_loop/tools/packs/meta.py` の `define_tool`。`validate` が次を強制する。

- 名前が `^[a-z][a-z0-9_]{2,40}$` に一致し、`RESERVED`（`finish`、`subagent`、`define_tool`、`tool_search`、`shell`、`run_python`、`read_file`、`write_file`、`edit_file`）に含まれず、組み込みを隠さないこと
- `code` に `def run(` が含まれること
- `input_schema` が `type: object` を持つこと
- サンドボックスが設定されていること

規約は `run(input: dict) -> str | dict` である。

```python
def run(input):
    return {"double": input["n"] * 2}
```

`call` はワークスペース配下に `tools/<name>.py` と `tools/<name>.json` を書き、`dynamic=True` の `DynamicTool` を登録し、`state.dynamic_tools` に追加し、`tool_defined` を報告する。同じワークスペースでの次の実行では、`Profile.build` から呼ばれる `load_dynamic_tools(registry, workspace)` が、対応する `.py` を持つすべての `tools/*.json` を登録する。

`DynamicTool.call` はそのコードをハーネスプロセスに取り込まない。`runpy` でファイルを読み込み、入力を JSON として標準入力から渡し、戻り値を印字するラッパーを使って `sandbox.run_python` を呼ぶ。パイプラインが先に入力スキーマを検証する。権限の対象は `dynamic:<name>` である。

これが安全である理由。モデルが作ったツールにできるのは `run_python` にできることだけで、それ以上はない。`RunState` に触ることも、モデルを呼ぶことも、レジストリや権限に手を伸ばすことも、サンドボックスから出ることもできない。

## スキル

`harnessing_loop/skills/loader.py`。配置は `<skills_dir>/<name>/SKILL.md` である。

```markdown
---
name: verify
description: Check the output against the request before declaring the job done.
when_to_use: before calling finish, and after any change that touched more than one file
---

# Verify
1. Re-read the original request. ...
```

```yaml
skills:
  enabled: true
  bundled: true      # include harnessing_loop/skills/bundled/
  dirs: [skills]     # relative to the workspace
```

`SkillSet.listing_text()` は名前、説明、`when_to_use` だけを静的なシステムプロンプトに入れる。`skill` ツール（`SkillTool`）は本体の全文を `<skill name="...">` に包んで返し、`skill_used` を記録する。同梱の `verify` スキルは上に示したものである。モデルは `write_file` で `skills/<name>/SKILL.md` に自分のスキルを書ける。`discover()` は `skill` 呼び出しごとに走査するため、書いた直後から使えて、次の実行では一覧にも載る。

## メモリ想起

プロファイルに `memory: {dir: ...}` があると、`harnessing_loop/core/loop.py` の `Loop.run` が `runtime.memory.recall_text(prompt)` を一度呼び、その結果を最初のユーザーメッセージの `<system_reminder>` 内、"Relevant memory:" の下に追加する。`harnessing_loop/persistence/memory.py` の `MemoryDir` は、`name`、`description`、`type` のフロントマターを持つ `<slug>.md` 1つに事実1つを保ち、加えて `MEMORY.md` の索引を置く。想起は名前と説明に対するキーワード採点で、上位5件、6,000文字が上限。`selector` で置き換えられる。

## 実行する

```
pytest tests/test_memory_skills_meta.py tests/test_tools.py -v
```

`test_deferred_tool_hint_and_tool_search` が遅延を扱う。`test_subagent_returns_child_final_text` は1つのフェイクモデルで親と子を走らせる。`test_define_tool_then_call_it` は動的ツールを定義し、呼び、読み直す。`test_skills_listing_and_tool` と `test_memory_recall_injected_into_prompt` がスキルとメモリを扱う。

次は [10_application.md](10_application.md)。
