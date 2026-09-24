# アプリケーションを作る

[English](../10_application.md) · [索引](README.md) · [用語集](GLOSSARY.md)

ランタイムがどのようにサービスになるか。ジョブ、ワーカープロセス、制御、イベント配信、そして自分の領域に合わせて差し替える部分。

## 全体の形

```
クライアント ──POST /jobs──▶  server/app.py  ──▶  jobs.sqlite (queued)
                              │
                     server/scheduler.py     FIFO 順に取得、max_active 以下
                              │
                     server/worker.py        1ジョブ1プロセス
                              │
                   Profile.build → Loop.run  ジョブのワークスペースに .harness/ を書く
                              │
クライアント ◀─GET /jobs/{id}/events─  サーバーが .harness/events.jsonl を追尾
クライアント ──POST /jobs/{id}/pause─▶ .harness/control.json を書く
```

ライブラリが `server/` を import することはない。サービスが実行から必要とするものは、すべてすでにディスクにある。イベントログ、トランスクリプト、制御ファイル、状態ファイルである。サーバーはそれらのファイルを読み書きする。動いているループへの参照は持たない。

## 動かす

```
python -m server.app --root ./jobs --port 8765 --max-active 3
```

`http://127.0.0.1:8765/` を開く。プロファイルを指定してプロンプトを投入する。イベントストリームを見る。一時停止、再開、中止、あるいは動いているエージェントへのメッセージ送信ができる。

スクリプトからなら次のようにする。

```python
import json, urllib.request
req = urllib.request.Request(
    "http://127.0.0.1:8765/jobs",
    data=json.dumps({"profile": "coder", "prompt": "add tests for cli.py", "model": "fake"}).encode(),
    headers={"Content-Type": "application/json"},
)
print(json.load(urllib.request.urlopen(req)))
```

## 部品

| ファイル | 役割 | 押さえておくこと |
|---|---|---|
| `server/jobs.py` | `JobDB`、SQLite のテーブル1つ | `claim_next()` は原子的（`BEGIN IMMEDIATE`）なので、複数のスケジューラが1つのデータベースを共有できる。状態は `queued`、`running`、`paused`、`done`、`failed`、`cancelled`。 |
| `server/scheduler.py` | `Scheduler` スレッド | FIFO 順にジョブを取得し、`python -m server.worker <id> <db>` を起動し、結果を記録せずにプロセスが終わったときやハートビートが途絶えたときはジョブを failed にする。 |
| `server/worker.py` | `run_job()` | ジョブのプロファイルからランタイムを組み立て、ハートビートと一時停止・再開の反映のためにイベントを購読し、終了理由、最終テキスト、ターン数、コストを記録する。クラッシュはトレースバック付きで `failed` として記録される。 |
| `server/app.py` | HTTP API と SSE | 標準ライブラリだけを使う。`GET /jobs/{id}/events` はワークスペースのイベントログを追尾し、ジョブが終端状態になると `end` イベントで終わる。制御操作は制御ファイルを書き、ループが次のターンでそれを拾う。 |
| `server/page.html` | 1ページ | ジョブを一覧し、1つのジョブのイベントを配信し、制御ボタンを出す。 |

## ジョブのワークスペース

リクエストがワークスペースを指定しない限り、各ジョブは `<root>/<job_id>/` を得る。その中で実行は `.harness/` を作り、次を置く。

```
transcript.jsonl   会話。再開に使える
events.jsonl       UI が配信するもの
state.json         フェーズ、証跡、ファイル、TODO
control.json       中止 / 一時停止 / 受信箱
notes.md, todo.json
tool-results/      退避した大きな出力
checkpoints/       編集前のファイルの控え
worker.log         ワーカープロセスの標準出力と標準エラー
```

終わったジョブや失敗したジョブを続けるには、`"mode": "resume"` と同じ `workspace` を付けてジョブを投入する。ワーカーは `Loop.resume` を呼び、最後のコンパクション境界からトランスクリプトと保存された状態を読み込む。

## 自分のものにする

アプリケーションとは、プロファイルと検証スクリプトである。`examples/app_demo/` のデモが、5つのファイルでその型全体を示している。

1. **`profile.yaml`**。フェーズ、入場ゲート、完了ゲート、権限、サンドボックス、フック。すべてのキーが並んでいる `harnessing_loop/profiles/template_app.yaml` から始める。
2. **`prompt.md`**。方法を、モデルに通じる言葉で書く。フェーズ、出力パス、そして数値は記憶ではなくコードから出すという規則を明示する。
3. **検証スクリプト**（`workspace/verify.py`）。出力を再計算または検査し、`ok: true` を付けて `verify/verify.json` を書く独立したプログラム。完了ゲートはそのファイルに対して `fresh_file` と `json_field` を使う。エージェント自身の主張を信じる検証スクリプトはゲートではない。
4. 権限パターンに収めきれない規則のための**フック**。デモは10行のスクリプトで破壊的なシェルコマンドを止めている。
5. フェーズ推定のための**証跡**。`phase_evidence` が `file_written` のような証跡キーをフェーズに対応付けるので、モデルが宣言を忘れても、起きたことから進捗が推定される。

YAML で表せない独自のゲートは、`(state, ctx) -> str | None` という素の呼び出し可能オブジェクトであり、`Profile.extra_gates` で付けるか、`Gates` を自分で組み立てる。独自のツールは `Tool` のサブクラスで、`Profile.extra_tools` で付けるか、`harnessing_loop/tools/packs/__init__.py` に新しいパックを登録する。

## 運用する

- **隔離**。自分以外の誰かから来るジョブを走らせる前に、`sandbox.backend` を `docker` に切り替える。プロファイルが `allow_unsafe_local: true` と言わない限り、起動時検査は local バックエンドでの exec ツールを拒否する。
- **コスト**。`limits.max_cost_usd` が暴走したジョブを止める。最終コストはジョブの行が持つ。
- **並行度**。`--max-active` がワーカープロセスの数を抑える。ワーカーは1つずつが完全なプロセスなので、メモリもそれに比例して増える。
- **復旧**。実行中に殺されたワーカーは、結果のない `tool_use` で終わるトランスクリプトを残す。再開はそれを合成したエラー結果で修復して続ける。
- **可観測性**。イベントログが唯一の情報源である。ジョブの状況を知る必要があるものは、ページ、テスト、自作のダッシュボードを含め、すべてそれを追尾する。

## 実行する

```
python -m pytest -q
python examples/app_demo/run.py
python -m server.app --root ./jobs
```

次は `evals/run.py`。プロンプトやゲートを変える前に、タスク集合に対してプロファイルを測る。
