# 用語集と翻訳方針

[English](../../README.md) · [日本語ドキュメント索引](README.md)

このリポジトリの日本語訳はすべてこの表に従う。訳語を増やすときはここに追記してから使う。

## 文体

| 方針 | 内容 |
|---|---|
| 文体 | である調。原文の英語が短い断定文で書かれているため、日本語もそれに合わせる。です・ます調は使わない |
| 句読点 | 。と 、を使う |
| 識別子 | コード上の名前、パス、設定キー、YAML のキーと値は訳さない。バッククォートで囲む（`read_only`、`dont_ask`、`tool_packs`） |
| 原文 | コード内では英語のコメントを残し、そのあとに日本語を置く。英語を置き換えない |
| 訳さないもの | `Tool.description`、`input_schema`、`system_prompt` などモデルに送られる文字列。これらはモデルの挙動とトークン数を変えるため英語のまま |

## 中核の用語

| 英語 | 日本語 | 備考 |
|---|---|---|
| harness | ハーネス | 信頼されたプロセス本体。認証情報を保持し、モデルを呼ぶ側 |
| loop | ループ | `core/loop.py` の反復機構 |
| turn | ターン | ループ1周 |
| run | 実行 | 1本のジョブ全体。「1回の実行」 |
| exit reason / terminal reason | 終了理由 | `completed`、`max_turns` など |
| workspace | ワークスペース | エージェントが見える唯一のフォルダ |
| profile | プロファイル | 1つの YAML ファイル |
| runtime | ランタイム | `Profile.build()` が組み立てた実行体 |
| fail closed | 安全側に倒す | 「既定値は安全側に倒れる」 |

## ツール

| 英語 | 日本語 | 備考 |
|---|---|---|
| tool | ツール | |
| tool pack | ツールパック | `files`、`exec` など |
| registry | レジストリ | |
| dispatcher | ディスパッチャ | |
| pipeline | パイプライン | 1回のツール呼び出しが通る経路 |
| predicate | 述語 | `is_read_only` など |
| read-only | 読み取り専用 | |
| concurrency-safe | 並行実行安全 | |
| destructive | 破壊的 | |
| deferred tool | 遅延ツール | 名前だけ送り、`tool_search` で読み込む |
| dynamic tool | 動的ツール | `define_tool` がモデルに作らせるツール |
| subagent | サブエージェント | |
| background task | バックグラウンドタスク | |
| tool result | ツール結果 | |
| size control | サイズ制御 | 結果が大きいときの切り詰めとディスク退避 |

## 安全性

| 英語 | 日本語 | 備考 |
|---|---|---|
| permission | 権限 | |
| permission mode | 権限モード | `default`、`accept_edits`、`plan`、`bypass`、`dont_ask` |
| allow / deny / ask rule | allow / deny / ask ルール | ルール名は設定値なので英語のまま |
| content-specific | 内容依存 | パターン付きルール |
| blanket | 包括的 | パターンなしルール |
| bypass-immune | バイパス不可 | どのモードでも無効化できない検査 |
| guard | ガード | |
| protected path | 保護パス | |
| hook | フック | |
| pre-tool hook | ツール実行前フック | `pre_tool_use` |
| post-tool hook | ツール実行後フック | `post_tool_use` |
| redaction | 秘匿化 | シークレットの伏せ字化 |
| sandbox | サンドボックス | |
| trust zone | 信頼境界 | |
| env scrubbing | 環境変数の除去 | |
| backend | バックエンド | `local`、`docker`、`remote` |

## コンテキスト

| 英語 | 日本語 | 備考 |
|---|---|---|
| context window | コンテキストウィンドウ | |
| token | トークン | |
| token accounting | トークン計算 | |
| compaction | コンパクション | 会話全体の要約。訳さず機構名として扱う |
| microcompaction | マイクロコンパクション | 古いツール結果だけの破棄 |
| rebuild | 再構築 | 要約から会話を組み直す |
| attachment | 添付 | |
| system prompt | システムプロンプト | |
| prompt cache | プロンプトキャッシュ | |
| cache prefix | キャッシュプレフィックス | |

## 永続化と進捗

| 英語 | 日本語 | 備考 |
|---|---|---|
| transcript | トランスクリプト | 再開に使う会話ログ |
| checkpoint | チェックポイント | |
| control file | 制御ファイル | `control.json` |
| notes | ノート | `notes_append` の書き込み先 |
| memory | メモリ | 実行をまたいで残る記憶 |
| skill | スキル | `SKILL.md` |
| phase | フェーズ | 前にしか進まない |
| gate | ゲート | 完了を認める条件 |
| entry gate / finish gate | 入場ゲート / 完了ゲート | |
| evidence | 証跡 | ディスク上の事実。モデルの主張ではない |
| stuck detection | スタック検出 | |
| verifier | 検証スクリプト | `verify.py` のような独立した検算 |

## モデルクライアント

| 英語 | 日本語 | 備考 |
|---|---|---|
| model client | モデルクライアント | |
| fake model | フェイクモデル | 台本どおりに応答する試験用の代役 |
| retry policy | 再試行方針 | |
| idle timeout | 無通信タイムアウト | |
| usage | 使用量 | トークン使用量 |
| cost | コスト | |

## アプリケーション

| 英語 | 日本語 | 備考 |
|---|---|---|
| job | ジョブ | |
| job queue | ジョブキュー | |
| scheduler | スケジューラ | |
| worker | ワーカー | 1ジョブ1プロセス |
| event bus | イベントバス | |
| event stream | イベントストリーム | |
| heartbeat | ハートビート | |
| concurrency | 並行度 | 同時に走らせる数。`max_active` など |
| tail（ログを） | 追尾 | イベントログを追いかけて配信する |
| stale（heartbeat） | 途絶えた | |
| recovery | 復旧 | |
| observability | 可観測性 | |
| plain callable | 素の呼び出し可能オブジェクト | |
| FIFO / SSE / SQLite / HTTP API | 訳さない | 略語はそのまま |

## 見出しの定訳

同じ英語の見出しが複数の文書に現れるため、訳を固定する。

| 英語の見出し | 日本語 |
|---|---|
| Run this | 実行する |
| Run it | 動かす |
| Operating it | 運用する |
| Making it yours | 自分のものにする |

## 追加の用語

翻訳の作業中に必要になった語。上の表に載っていないものはここを見る。

### 一般

| 英語 | 日本語 | 備考 |
|---|---|---|
| reason vocabulary | 終了理由の語彙 | |
| iteration | 反復 | ループの1周は「ターン」 |
| mechanism | 機構 | |
| contract | 規約 | ツールやサンドボックスの取り決め |
| protocol | プロトコル | `Sandbox` のような Python の Protocol |
| dataclass | データクラス | `frozen` は訳さない |
| schema | スキーマ | |
| decorator | デコレータ | `@tool` |
| keyword arguments | キーワード引数 | |
| traceback | トレースバック | |
| wire | 送信形式 | `# ---- wire ----`。モデルに送る形 |
| boilerplate | 定型コード | |
| isolation | 隔離 | |
| atomically | 原子的に | `os.replace` による書き込み |
| index | 索引 | `MEMORY.md`、チェックポイントの索引 |
| entry | 項目 | メモリやキャッシュの1件 |
| marker | マーカー | `EMPTY_MARKER` など |
| threshold | しきい値 | |
| cap / bound | 上限 | `max_result_chars` など |
| provider | 提供元 | モデルの提供元。サービス提供者の意味では「プロバイダ」 |
| built-in | 同梱の | 同梱のパックやスキル |
| portable | 可搬 | トランスクリプトの可搬性 |
| lifecycle / membership / properties | ライフサイクル / 所属 / 性質 | 節区切りコメントの定訳 |

### ツール

| 英語 | 日本語 | 備考 |
|---|---|---|
| pack | パック | 「ツールパック」の短縮 |
| batch | バッチ | `partition` がまとめた一群 |
| thread pool | スレッドプール | |
| dispatch | ディスパッチ | |
| subject / permission subject | 判定対象 | 権限ルールのパターンと照合される文字列 |
| informational flag | 参考情報のフラグ | `destructive` |
| spill / persist to disk | ディスクへ退避 | |
| side query | 副問い合わせ | `ctx.model` を使う問い合わせ |
| replay | 再生 | |
| model-defined tool | モデル定義ツール | `define_tool` が作るもの。実体としては動的ツール |
| no-raise rule | 例外を投げない規則 | |
| file state cache | ファイル状態キャッシュ | `FileStateCache` |
| preview | プレビュー | `tool_result_preview_chars` |

### 安全性

| 英語 | 日本語 | 備考 |
|---|---|---|
| ask handler | ask ハンドラ | `AskHandler`。`ask` は設定値なので英語 |
| stop hook | 停止フック | `stop` イベント |
| immunity | バイパス不可性 | 「bypass-immune」の名詞形 |
| allowlist / blocklist | 許可リスト / 拒否リスト | |
| domain allowlist | ドメイン許可リスト | `web.allowed_domains` |
| merge precedence | 併合の優先順位 | 複数フックの結果を束ねる順 |
| additional context | 文脈の追加 | フックの `additional_context` |
| stripping | 剥ぎ取り | `strip_dangerous_allow_rules` |
| code-execution prefix | コード実行接頭辞 | `CODE_EXEC_PREFIXES` |
| hosted sandbox | ホスト型サンドボックス | `remote` バックエンド |
| resource limits | 資源制限 | `--memory`、`--cpus` など |
| kernel capabilities | カーネルケーパビリティ | `--cap-drop ALL` |
| privilege escalation | 権限昇格 | `no-new-privileges` |
| link-local / loopback / multicast | リンクローカル / ループバック / マルチキャスト | |
| mask | 伏せる | 秘匿化の動作 |
| private key block | 秘密鍵ブロック | |

### コンテキスト

| 英語 | 日本語 | 備考 |
|---|---|---|
| token estimation | トークン推定 | 「トークン計算」(accounting) とは区別する |
| padding | 余裕 | 推定を多めに出すための上乗せ |
| blocking line | ブロッキング線 | `blocking_buffer_tokens` の境界 |
| circuit breaker | サーキットブレーカー | `compact_max_failures` |
| tail / head | 末尾 / 前半 | `split_tail` |
| summary | 要約 | コンパクションの成果物 |
| boundary message | 境界メッセージ | `compact_boundary` |
| instruction file | 指示ファイル | `AGENT.md` |
| reminder | リマインダ | 毎ターン添える注意書き |
| system reminder | システムリマインダ | `<system_reminder>` |
| cached segment | キャッシュ区間 | |
| cache marker | キャッシュマーカー | `cache_control` |
| static / dynamic system block | 静的 / 動的なシステムブロック | |

### 永続化と進捗

| 英語 | 日本語 | 備考 |
|---|---|---|
| operator | 操作者 | 実行を操る人 |
| operator message | 操作者メッセージ | `control.json` の `inbox` |
| inbox | 受信箱 | |
| rewind | 巻き戻し | `Checkpoints.rewind` |
| orphan | 孤児 | `repair_orphans` |
| repairs | 修復処理 | |
| synthetic | 合成の | harness が作ったエラー結果 |
| chain | 鎖 | `parent_id` のつながり |
| fork | 分岐 | 捨てられた枝 |
| bookkeeping | 備忘 | モデル自身のための記録 |
| recall / memory recall | 想起 / メモリ想起 | `recall_text` |
| frontmatter | フロントマター | `SKILL.md` の先頭 |
| todo / todo list | TODO / TODO の一覧 | 大文字のまま |
| open todo items | 未了の TODO 項目 | |
| pending work | 未了の作業 | |
| requirement | 要件 | ゲートの要件 |
| entry requirements | 入場の要件 | |
| factory | ファクトリ | ゲート要件を作る関数 |
| nudge | 促し | 停止の前に出すメタのユーザメッセージ |
| signal | 兆候 | スタック検出の3つの兆候 |
| behaviour | 挙動 | |
| budget | 予算 | `max_cost_usd` |

### モデルクライアント

| 英語 | 日本語 | 備考 |
|---|---|---|
| backoff | バックオフ | |
| jitter | ゆらぎ | |
| streaming / stream | ストリーム | |
| incremental output | 逐次出力 | |
| script（FakeModel の） | 台本 | |
| cost tracker | コストトラッカー | `CostTracker` |
| subscriber | 購読者 | イベントバスの購読者 |
| terminal renderer | 端末向け表示器 | `print_subscriber` |
