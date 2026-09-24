# 進捗: フェーズ、ゲート、証跡、スタック検出

[English](../08_progress.md) · [索引](README.md) · [用語集](GLOSSARY.md)

この文書は、ハーネスが実行が前に進んでいるかどうかを判断する方法を扱う。ツールが記録する証跡、入場ゲートを持つ前進のみのフェーズ、`finish` ゲート、フェーズ推定、そしてスタック検出である。

## 原則

モデルは自分が何をしたかを述べる。ハーネスは証跡から判断する。アシスタントのテキストは何の重みも持たない。数えるのは `RunState.evidence` のキーであり、そのキーを書けるのはツールだけである。

## 証跡

`harnessing_loop/core/state.py` の `RunState` は `evidence: dict[str, Any]` を持つ。`add_evidence(key, value=True)` がキーを立て、`turns_without_evidence` をリセットする。

ツールは `harnessing_loop/tools/base.py` の `ToolResult.ok` にキーワード引数として証跡を報告する。

```python
from harnessing_loop.tools.base import ToolResult

return ToolResult.ok(f"Wrote {n:,} chars to {rel}", file_written=rel)
```

`harnessing_loop/tools/pipeline.py` の `run_tool_call` の手順8が `result.evidence` を状態に統合する。エラーでない結果のときだけである。同梱のパックが記録するものは次のとおり。

| パック | ツール | 証跡キー |
|---|---|---|
| files | `write_file`、`edit_file` | `file_written` = 相対パス |
| exec | `shell` | `shell_ran` = True |
| exec | `run_python` | `python_ran` = True |
| planning | `set_phase` | `phase` = フェーズ名 |
| planning | `finish` | `finished` = ステータス、加えて `final_summary` |

`finished` は実行を終わらせる。ツールのターンごとにループがこれを確認し、`Terminal("completed", ...)` を返す。

## フェーズ

プロファイルは `phases:` を順に並べる。`harnessing_loop/tools/packs/planning.py` の `set_phase` は次のように動く。

1. `validate` が `gates.phases` にない名前を拒否し、正しい順序を示す。
2. `call` が `Gates.index` でインデックスを比較する。小さいインデックスは拒否される。"Cannot move back from X to Y. Phases move forward only."
3. `gates.check_entry(phase, state, ctx)` が入場の要件を走らせる。問題があれば "Cannot enter Y yet:" として1行ずつ示して拒否する。
4. 成功すると `state.phase` を設定し、`phases_seen` に追記し、`PHASE` を発行し、ノートを書く。

## ゲート

`harnessing_loop/progress/gates.py` の `Gates` は `phases`、`entry`（フェーズから要件への対応）、`finish` を持つ。要件は `(state, ctx) -> str | None` という呼び出し可能なものであればよい。返すのは問題の説明か、満たされているときは `None` である。例外を投げた要件は問題として報告される。壊れたゲートは通さない。

| 要件 | 通る条件 |
|---|---|
| `evidence(key)` | `state.evidence[key]` が真と評価される |
| `file_exists(rel)` | パスが存在し、ディレクトリであるか、空でない |
| `fresh_file(rel, newer_than)` | `rel` が存在し、glob に一致するどのファイルよりも新しい |
| `json_field(rel, key, expected)` | その JSON ファイルの、ドット区切りの `key` が `expected` と等しい |
| `todos_done` | `completed` 以外の状態にある TODO 項目が1つもない |

どのファクトリも任意の `message` を受け取る。独自の要件は素の関数であり、プロファイルの `extra_gates` フィールド経由で渡す。`harnessing_loop/profiles/base.py` の `Profile.build` がそれを `Gates` に統合する。

```python
from harnessing_loop.profiles.base import load_profile

def has_three_outputs(state, ctx):
    n = len(list((state.workspace / "out").glob("*.json")))
    return None if n >= 3 else f"expected 3 outputs under out/, found {n}"

profile = load_profile("template_app").with_overrides(extra_gates={"finish": [has_three_outputs]})
runtime = profile.build("./work")
```

`check_finish` は、`state.phase` が最後のフェーズでないときにも問題を加える。

## finish ツール

`finish` は `gates.check_finish` を走らせ、エラー結果として拒否する。

```
Cannot finish yet:
- verify/verify.json does not exist; run the verifier
- open todo items: write the report
- Phase is 'build'; finish requires the last phase 'done'.
```

`status: blocked` を付けた `finish` はゲートを飛ばし、`finished = "blocked"` を記録する。ループはそれでも `completed` で終わり、要約が最終テキストになる。

## require_finish

`require_finish: true` のとき、`harnessing_loop/core/loop.py` の `Loop._on_model_stop` は素のターン終了を受け付けない。`finished` がなく、`finish` がレジストリにある場合、`FINISH_NUDGE_TEXT` を meta のユーザーメッセージとして付け足し、続行する。これは最大2回までである。3度目の素の停止は `Terminal("incomplete", ...)` で終わる。`require_finish` がない場合、素の停止は `Terminal("completed", ..., "end of turn")` である。

## フェーズ推定

`harnessing_loop/progress/evidence.py` の `infer_phase(state, phases, phase_evidence)` が、ツールのターンごとに走る。プロファイルの `phase_evidence:` はフェーズ名を証跡キーに対応づける。キーが存在するもののうち最も先のフェーズが現在のフェーズになる。前にしか進まない。推定が扱うのは `set_phase` なしで進んだ作業だけである。そうした移動は `inferred=True` を付けて `PHASE` を発行する。

## YAML でのゲート指定

`harnessing_loop/profiles/base.py` の `_build_req` は次を受け取る。

| 形式 | 例 |
|---|---|
| 文字列 | `todos_done`、`evidence:verified`、`file_exists:out` |
| 1キーの辞書 | `{evidence: verified}`、`{file_exists: out}`、`{todos_done: true}` |
| 引数付きの辞書 | `{fresh_file: {path: verify/verify.json, newer_than: "out/**/*", message: ...}}` |
| 引数付きの辞書 | `{json_field: {path: verify/verify.json, key: ok, expected: true, message: ...}}` |

それ以外は `ConfigError` を投げる。`harnessing_loop/profiles/template_app.yaml` より。

```yaml
phases: [plan, build, verify, done]
phase_evidence:
  build: file_written
  verify: verified

gates:
  entry:
    verify:
      - file_exists: out
  finish:
    - fresh_file: {path: verify/verify.json, newer_than: "out/**/*"}
    - json_field: {path: verify/verify.json, key: ok, expected: true}
    - todos_done

require_finish: true
```

同梱のツールは `verified` を出さない。アプリケーション側の検証ツールが `ToolResult.ok(..., verified=True)` でこれを加える。

## スタック検出

`harnessing_loop/progress/stuck.py` の `StuckDetector` は、ツールのターンごとに1回 `observe(calls, state, cost_usd)` として呼ばれ、`(nudge, stop_reason)` を返す。

| 兆候 | `limits:` 下のキー | 挙動 |
|---|---|---|
| 同一の入力による同じツール呼び出しが `repeat_limit` 回連続する | `stuck_repeat_limit`（3） | まず促し、再発で停止 |
| 新しい証跡がないまま `no_evidence_turns` ターン経過する | `stuck_no_evidence_turns`（12） | 促してリセットし、再発で停止 |
| コストが予算を超える | `max_cost_usd` | ただちに停止 |

促しは `<system_reminder>` の中に入ってツール結果のメッセージに載り、`STUCK` イベントを伴う。停止は、理由が予算に触れているときは `Terminal("budget_exceeded", ...)` になり、それ以外は `Terminal("stuck", ...)` になる。

## 実行する

```
pytest tests/test_progress.py -v
```

`test_gate_builtins` は各要件を動かす。`test_set_phase_forward_only_and_entry_gate` は拒否を確認する。`test_finish_requires_last_phase_and_fresh_verifier` はフェーズ制のジョブを走らせ、受理される `finish` の前に2種類の拒否テキストが出ることを確認する。

次: [docs/ja/09_scaling_tools.md](09_scaling_tools.md)
