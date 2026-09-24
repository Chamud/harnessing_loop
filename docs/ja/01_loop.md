# ループ

[English](../01_loop.md) · [索引](README.md) · [用語集](GLOSSARY.md)

この文書では、`harnessing_loop/core/loop.py` の1回の反復、ループが終了するときの理由の語彙、ループが読む `Runtime`、そしてループが書き出すイベントとコストのオブジェクトを扱う。

## 1回の反復を順に

`Loop._iteration` は次の手順を実行し、`Continue` か `Terminal` のいずれかを返す。

| 手順 | 何が起きるか | 場所 |
|---|---|---|
| 1 | `state.turn` を進め、チェックポイントのスナップショットを取る | `_iteration` |
| 2 | `.harness/control.json` を読む。中止なら `Aborted` を投げ、一時停止なら待ち、受信箱を回収する | `_control` |
| 3 | トークンを見積もり、古いツール結果をマイクロコンパクションし、上限が近ければ全体をコンパクションする | `_manage_context` |
| 4 | `ModelRequest` を組み立て、モデルをストリーミングし、`ModelResponse` を回収する | `_call_model` |
| 5 | 使用量をコストトラッカーに加算し、アシスタントメッセージを追加し、`assistant_message` を発行する | `_iteration` |
| 6 | ツール呼び出しがなく `stop_reason == "max_tokens"` のとき、出力上限からの復帰 | `_iteration` |
| 7 | ツール呼び出しがない場合、停止フック、finish 要求、そして `Terminal` | `_on_model_stop` |
| 8 | ツール呼び出しがある場合、ディスパッチ、メッセージ単位のサイズ予算、スタック検出、添付、結果の追加 | `_iteration` |
| 9 | 証跡からフェーズを推定し、`.harness/state.json` を保存する | `_iteration` |
| 10 | 終了判定。finish の受理、フックによる停止、スタックまたは予算、`max_turns`、いずれでもなければ `Continue("tool_use")` | `_iteration` |

手順4で `PromptTooLong` が出た場合は、強制的に1回コンパクションして `Continue("compact_retry")` を返す。2回目は `Terminal("prompt_too_long")` になる。それ以外の `ModelError` は `Terminal("model_error")` である。

## ループを回す合図

ループが続くのは、アシスタントメッセージに `tool_use` ブロックが含まれるときである。ループが読むのは `msg.tool_uses()` であり、`response.stop_reason` ではない。停止理由を参照するのは `max_tokens` の場合だけである。これにより、停止理由の報告方法が異なる提供元をまたいでも、また発行したブロックから停止理由を導くフェイクモデルに対しても、ループは正しく動く。

## 終了理由の語彙

`core/state.py` が2つの遷移型を定義する。テストは `reason` の文字列を検査する。

`Terminal(reason, turns, message, final_text)`。`bool(terminal)` が真になるのは `completed` のときだけである。

| 理由 | 意味 |
|---|---|
| `completed` | ターンの終わり、または `finish` が受理された |
| `max_turns` | `config.max_turns` に達した |
| `aborted` | 制御ファイルに中止フラグがある |
| `blocking_limit` | コンテキストが満杯で、コンパクションが無効か失敗している |
| `prompt_too_long` | 強制コンパクションの後でもリクエストが大きすぎる |
| `hook_prevented` | フックが `stop` を返した |
| `model_error` | 転送の失敗、または出力上限への繰り返しの到達 |
| `budget_exceeded` | `max_cost_usd` を超えた |
| `stuck` | 同一のツール呼び出しの繰り返し、または証跡が長く出ていない |
| `incomplete` | `require_finish` が設定されているのに、モデルが `finish` を呼ばずに2回終わった |

`Continue(reason)` の理由は次のとおり。`tool_use`、`output_limit_recovery`、`stop_hook_blocking`、`compact_retry`、`budget_continuation`。

## 出力上限からの復帰

応答が `max_tokens` で打ち切られ、ツール呼び出しを含まないとき、ループはメタのユーザーメッセージ（`OUTPUT_LIMIT_TEXT`）を追加して、要約なしで続けるようモデルに求め、`Continue("output_limit_recovery")` を返す。これを `config.max_output_recovery_attempts` 回（既定は3）まで行い、その後は `Terminal("model_error", ..., "output limit hit repeatedly")` を返す。

## Runtime と RunConfig

`core/runtime.py` は `Runtime` を定義する。これは1回の実行に必要なものすべてを保持するデータクラスである。必須は `workspace`、`config`、`model`、`registry`、`system_prompt`、`permissions`、`hooks`。任意は `sandbox`、`file_state`、`redactor`、`events`、`gates`、`checkpoints`、`transcript`、`memory`、`skills`、`deps`、`dynamic_prompt`、`require_finish`、`profile_name`、`cache_ttl`、`extra`、`depth`。`__post_init__` は、与えられていなければ `.harness/events.jsonl` に記録する `EventBus` と `FileStateCache` を作る。`child()` は、より小さいレジストリ、ゲートなし、トランスクリプトなし、複製したファイル状態キャッシュを持つサブエージェント用のランタイムを組み立てる。

`RunConfig` は `Profile.build` で一度だけ作られる frozen なデータクラスである。実行の途中で設定を読むものはないため、ターンをまたいで挙動が変わることはない。

## イベントとコスト

`core/events.py`。`EventBus.emit(type, **data)` は、ログのパスが設定されていれば JSONL の1行を書き、それから購読者へ配る。壊れた購読者は無視される。イベント型の定数には `RUN_START`、`TURN_START`、`TEXT_DELTA`、`ASSISTANT_MESSAGE`、`TOOL_START`、`TOOL_END`、`COMPACT`、`COST`、`STUCK`、`RUN_END` がある。`print_subscriber` は最小限の端末向け表示器である。

`core/cost.py`。`CostTracker.add(model, usage)` はモデルごとに `Usage` を積み上げ、モデル名の部分文字列を鍵とする表から価格を求める。未知のモデルはコスト0である。`snapshot()` は、モデル呼び出しのたびに `cost` イベントとして発行される。

## 最小のオフライン例

```python
from harnessing_loop.core.events import print_subscriber
from harnessing_loop.core.loop import Loop
from harnessing_loop.llm.fake import scripted, tool
from harnessing_loop.profiles.base import Profile

profile = Profile.from_dict({
    "name": "demo",
    "system_prompt": "You are a test agent.",
    "tool_packs": ["files", "planning"],
    "permissions": {"mode": "accept_edits"},
    "limits": {"max_turns": 10},
})
model = scripted(
    [tool("write_file", path="hello.txt", content="hi\n")],
    [tool("read_file", path="hello.txt")],
    "The file says hi.",
)
rt = profile.build("./work", model=model)
rt.events.subscribe(print_subscriber)
loop = Loop(rt)
result = loop.run("Write hello.txt, then read it back.")
print(result.reason, result.turns)          # completed 3
print([t.reason for t in loop.transitions])  # ['tool_use', 'tool_use', 'completed']
```

`FakeModel` は台本化された各ターンを順に再生し、その後は永遠に `"done"` と答える。使用量はテキストの長さから導かれるため、コストとトークンの見積もりは扱える数値を持つ。

## 実行する

```
python -m pytest tests/test_loop.py -q
```

次: [docs/ja/02_tools.md](02_tools.md)
