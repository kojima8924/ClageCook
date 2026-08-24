# Changelog

このプロジェクトの主な変更を記録します。形式は
[Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) を参考にし、バージョン番号は
[Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

### Changed (breaking)

0.2.0は後方互換性を前提としない初期リリースです。以下はbackendの保存schema・API契約・
環境変数名の破壊的変更で、移行手順を各項目に記載します。

- **会話JSONを `schema_version: 2` へ上げた。** 変更点は2つ。
  (a) turnの `event_log`（SSEイベントの丸ごとコピー）を廃止した。
  (b) turnの `cancelled` / `failed` / `interrupted` を保存せず、`status` からの派生値にした。
  **移行**: 手作業は不要。`schema_version: 1` のファイルは読み込み時に自動で2へ引き上げ、
  `event_log` は読み捨て、`status` が無いturnは旧派生booleanから復元する。次回保存時に
  新形式で書き戻される。**未知の `schema_version`（3以降など）は黙って読まず拒否する**
  （`conversation_schema_unsupported`）。データを退避したい場合は事前に
  `GET /api/conversations/{id}/export?format=json` で保存しておくこと。
- **SSEのリプレイを保存turnのフィールドから再構成するようにした。** `event_log` を優先していた
  従来は、再生成が `answers` / `synthesis` / `insights` だけを更新して `event_log` を書き換えないため、
  再生成後に同じ `request_id` を再送すると**再生成前の古い回答がリプレイされ**、
  `GET /api/conversations/{id}` の内容と食い違っていた。**移行**: クライアント側の対応は不要。
  ただしリプレイは実行時SSEの逐語再現ではなく正規化された列（`phase` を含まない、`insights` /
  `synthesis` を必ず含む）になり、保存turnからのリプレイでは実行時の `Last-Event-ID` を適用せず
  必ず先頭から流す（終端の `done` を取りこぼさないため）。各イベントはsource単位で冪等。
- **HTTPエラーの `detail` を `{code, message, ...}` に統一した。** 従来は構造化オブジェクトと
  生の日本語文字列が混在しており、構造化側はクライアントで生JSONとして表示され、文字列側は
  機械判別できなかった。**移行**: クライアントは `detail is String ? detail : jsonEncode(detail)`
  のような分岐をやめ、`detail["message"]` を表示し `detail["code"]` で分岐すること。
  復旧手順が異なる2種類の409に別コードを与えた
  （`request_id_conflict_conversation` = 会話IDを付け直す /
  `request_id_conflict_fingerprint` = 新しいrequest_idを振り直す）。
  コード一覧は `docs/API_ERRORS.md`。
- **一覧系レスポンスを `{"items": [...]}` 封筒へ統一した。** `GET /api/conversations` は裸の配列を
  やめ、`{items, corrupt_count, corrupt}` を返す。`POST /api/search` は
  **`POST /api/conversations/search` へ移動**し、`results` キーを `items` へ改名した。
  **移行**: クライアントはURLとキー名を差し替えること。
- **エクスポートの形式指定をクエリへ移した。** `/export.md` と `/export.zip` を廃止し、
  `GET /api/conversations/{id}/export?format=json|md|zip` に一本化した（既定は `json`）。
  **移行**: 旧URLは404になるのでクエリ形式へ差し替えること。
- **環境変数を `CLAGE_` 接頭辞へ統一した。** `HISTORY_TURNS` / `MAX_MESSAGE_CHARS` /
  `RATE_LIMIT_PER_MINUTE` のような汎用名はdocker-composeや共有シェル環境で他プロセスと
  衝突するため廃止した。**移行**: `backend/.env` の以下を改名する（値はそのまま）。

  | 旧名 | 新名 |
  | --- | --- |
  | `INCLUDE_MOCK_PROVIDERS` | `CLAGE_INCLUDE_MOCK_PROVIDERS` |
  | `HTTP_TIMEOUT_SEC` | `CLAGE_HTTP_TIMEOUT_SEC` |
  | `HTTP_RETRIES` | `CLAGE_HTTP_RETRIES` |
  | `MOCK_DELAY_SEC` | `CLAGE_MOCK_DELAY_SEC` |
  | `MAX_MESSAGE_CHARS` | `CLAGE_MAX_MESSAGE_CHARS` |
  | `MAX_PROVIDER_CALLS_PER_RUN` | `CLAGE_MAX_PROVIDER_CALLS_PER_RUN` |
  | `MAX_OUTPUT_TOKENS_PER_RUN` | `CLAGE_MAX_OUTPUT_TOKENS_PER_RUN` |
  | `MAX_INPUT_BYTES_PER_RUN` | `CLAGE_MAX_INPUT_BYTES_PER_RUN` |
  | `HISTORY_TURNS` | `CLAGE_HISTORY_TURNS` |
  | `HISTORY_MAX_CHARS` | `CLAGE_HISTORY_MAX_CHARS` |
  | `PEER_MAX_CHARS` | `CLAGE_PEER_MAX_CHARS` |
  | `MAX_CONCURRENT_RUNS` | `CLAGE_MAX_CONCURRENT_RUNS` |
  | `RATE_LIMIT_PER_MINUTE` | `CLAGE_RATE_LIMIT_PER_MINUTE` |
  | `SSE_PING_SEC` | `CLAGE_SSE_PING_SEC` |
  | `RUN_RETENTION_SEC` | `CLAGE_RUN_RETENTION_SEC` |
  | `SYNTHESIZER_PROVIDER` | `CLAGE_SYNTHESIZER_PROVIDER` |
  | `SYNTHESIZER_MODEL_{LOW,BALANCED,HIGH}` | `CLAGE_SYNTHESIZER_MODEL_{...}` |
  | `{CLAUDE,GEMINI,CHATGPT,GROK}_MODEL_{LOW,BALANCED,HIGH}` | `CLAGE_{...}_MODEL_{...}` |
  | `{CLAUDE,GEMINI,CHATGPT,GROK}_MAX_OUTPUT_TOKENS_{LOW,BALANCED,HIGH}` | `CLAGE_{...}_MAX_OUTPUT_TOKENS_{...}` |

  `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` / `XAI_API_KEY` /
  `ANTHROPIC_ADMIN_KEY` / `OPENAI_ADMIN_KEY` / `XAI_MANAGEMENT_KEY` / `XAI_TEAM_ID` は
  ベンダー標準名のため据え置き。旧名が残っていると起動時にwarningで新名を案内する。
- **`CLAGE_HTTP_TIMEOUT_SEC` の既定を180秒から600秒（上限1800秒）へ延ばした。** 自動再試行は
  既定0のままなので、タイムアウトは「課金済みで成果ゼロ」になる。HIGH tierのreasoning +
  Web検索には180秒では足りなかった。**移行**: 短いタイムアウトが必要なら明示的に設定すること。
- **アプリの端末内会話ストレージAPIを変更した。** `LocalConversationRepository.list()` /
  `search()` の戻り値を `List<LocalConversationSummary>` から
  `LocalConversationListing`（`items` と、読めなかった会話を表す `defects`）へ変えた。
  `quarantine()` / `rebuildManifestFromRecords()` を追加し、`LocalConversationSummary` から
  消費者のいなかった `createdAt` / `storageRevision` を削除した。
  **移行**: 保存recordの形式は変えていないので既存データはそのまま読める。呼び出し側は
  `(await repository.list()).items` へ差し替えること。
- **端末内会話1件のサイズ上限を16 MiBから2 MiBへ下げた。** SharedPreferencesは全体を
  メモリへ展開して保存のたびに書き直すため、16 MiBは現実的に扱えなかった。
  **移行**: 上限を超えた会話は保存時に失敗する。分岐（fork）で分割するか、
  ZIP/JSONエクスポートで退避すること。既存データの自動変換や削除は行わない。
- **Direct BYOKの保存turnから `resume_request.confirm_live_api` /
  `confirm_sensitive_data` を削除した。** 保存した同意をそのまま再送すると、
  再開経路で課金確認を迂回できてしまうため。再開は毎回 plan → 確認ダイアログ →
  実行を通す。**移行**: 既存recordに残っている両キーは読み捨てるだけで害はない。
- **Direct BYOKの秘密検出ルールをreference serverと同一にした。** ルール表の唯一の仕様を
  `docs/policy_rules.json` に置き、GitHubトークン、GitHub fine-grained token、AWSアクセス
  キーID、Bearer、Basic認証、`OPENAI_API_KEY=...` 形式の代入をDirect側にも追加した。
  伏せ字の粒度（secret_group）と判定versionも `local-patterns-v1` で揃えた。
  **移行**: 従来Direct BYOKで送信できていた文字列が block / confirm になる場合がある。
  誤検知は伏せ字置換またはファイルを分けて送ること。
- **アプリのAPI契約を `ClageApiClient` interfaceへ分離した。** `DirectByokClient` が具象HTTP
  クライアント `ApiClient` を継承するのをやめ、両者が同じinterfaceを実装する形にした。
  以前は契約へendpointを1つ足すと、Direct BYOKだけがそれを継承したまま
  `http://direct.invalid` へ会話本文を送ろうとし、名前解決の失敗に助けられていた。
  能力差は `supportsRunReconnect` / `supportsLocalStorageRepair` /
  `supportsBudgetReconciliation` で表現する。Direct BYOKの
  `releaseBudgetReconciliation()` は空のsnapshotを返す代わりに例外を投げる。
- **アプリのターン状態表現を `TurnStatus` 1つへ統一した。** `TurnRecord` が
  `status` / `cancelled` / `interrupted` / `failed` の4つで同じ事実を持っていたのをやめ、
  `status` を唯一の正、boolはそこからのgetterにした。「利用者が停止した」だけは通信断と
  意味が違うため `cancelledByUser` として独立に残す。Direct BYOKも `status` と
  `cancelled` だけを保存する。あわせて `PolicyScanResult.action` / `RunPlanParticipant.mode` /
  `AnswerRecord.completionStatus` / `SynthesisRecord.completionStatus` /
  `RegenerationAttempt.status` を enum 化した（未知の値は `PolicyAction.block` や
  `CompletionStatus.unknown` へ倒し、安全側の既定を保つ）。
  **移行**: 既存recordは読める。`status` を持たない旧recordだけ従来のbool群から復元する。

### Added

- ローカル秘密スキャナのルール表の単一仕様 `docs/policy_rules.json` を追加した。reference server
  (`backend/policy.py`) とDirect BYOK (`app/lib/services/policy_scanner.dart`) の双方に、
  rule_idの集合・並び・pattern・secret_group・判定versionと、共通sampleの検出位置・伏せ字結果が
  仕様と一致することを検証する回帰テストを置いた。片方だけ検出が弱くなる状態を検知する。
- 端末内会話の破損を伝えるアプリ内banner（読めなかった件数と理由、隔離、index再構築）を追加した。
- HTTPエラーコードの一覧と復旧手順を `docs/API_ERRORS.md` に表として追加した。
- 主要レスポンスにPydantic `response_model`(`backend/api_schemas.py`)を追加し、`/docs` のOpenAPIへ
  実際の契約が出るようにした。会話・ターン・添付のモデルは `extra="allow"` で、宣言外のキーを
  黙って落とさない。
- 読み取れない会話ファイルを `GET /api/conversations` の `corrupt_count` / `corrupt` で報告するようにした。
  破損ファイルは隔離ディレクトリへ移さず**その場に残す**(正本を勝手に動かさない)。
- 会話ファイルの `schema_version` を読み取り時に検証するようにした(従来はstorageだけが未検証で、
  attachments / finance / runtime_settings は既に検証していた)。
- 指定した環境変数が不正値・範囲外で実効値と食い違うとき、起動時にwarningで可視化するようにした。
  従来は黙って既定へ落とすかクランプしており、設定が効いていないことに気付けなかった。
- 公開ベータ向けのContributing guide、行動規範、Issue / Pull request template、Dependabot設定、
  AI支援開発の役割と検証方法を説明する文書を追加した。
- pytestがローカルのlive `.env` を継承しても、課金API、管理telemetry、Bearer、4社APIキーをimport前に
  無効化するtest-suite安全設定と回帰testを追加した。
- FlutterからAnthropic Messages、OpenAI Responses、Gemini Interactions、xAI Responsesへ直接接続する
  `DirectByokClient` / `DirectProviderClient` と、Direct BYOKを既定にするexecution modeを追加した。
- Direct BYOKと開発用reference serverを切り替える設定画面を追加した。APIキー、Provider別model override、
  統合役、AUTO / LOW / MEDIUM / HIGHの既定推論エフォートを端末で管理できる。
- APIキーをsecure storage、公開設定をSharedPreferencesへrevision付きで分離する `DirectSettingsStore` を追加した。
  保存済みkeyはUIへ読み戻さず、Provider単位または全社一括で削除できる。
- Direct専用namespace、immutable conversation record、manifest commit point、revision競合検出を備えた
  `SharedPreferencesLocalConversationRepository` を追加した。端末内の一覧、検索、rename、delete、memory、
  immutable fork、JSON exportを提供する。
- Direct会話の `conversation.json` と説明用 `README.txt` を含み、APIキーとmemory-only添付bytesを除外する
  ZIP exportを追加した。
- Directの並列回答、部分成功、DEBATE、BLIND、統合、Provider選択、Web tool、immutable回答/統合再生成を
  既存Flutter UIへ接続した。
- composerへ、品質を誘導しない比較・反証・発想・事実確認のprompt templateを追加した。
- chatと再生成が共有する `runs.py` のbackground run state machine、cancel handshake、会話lock pool、
  自動回収するrate limiterと、その単体回帰test。
- 再生成target、fingerprint、attempt遷移、確認判定を純粋関数として分離した `regeneration.py`。
- Flutterの会話選択・世代guardを担う `ConversationSelectionController` と、live run・SSE終端・idle監視を担う
  `LiveRunController` / `LiveStreamSession`。
- 組織管理telemetryへProvider別の実効window、予算windowとの一致状態、Anthropicの最終完全UTC bucketを追加し、
  Flutter利用状況画面でも不一致を常時表示。
- Android Direct実行中のprocess凍結を抑える `dataSync` foreground service、固定文言の実行中通知、
  Provider送信前の開始ack、Direct streamの20秒heartbeatを追加した。

### Changed

- repositoryの位置付けを「公開準備中のprivate」から、制限を明示した公開ベータ／AI支援開発ポートフォリオへ
  更新し、READMEと開発文書から個人環境固有のpathを除去した。
- CIへjob timeoutとtoolchain表示を追加し、pytest cacheを書かずに実行するよう統一した。feature branchは
  Pull request eventだけで検証し、main以外のpushとの二重実行を避ける。
- Provider実測トークン利用量台帳をターンごとの既定折りたたみaccordionへ変更した。設定画面から台帳だけを
  非表示にでき、OFFでもusageの取得・端末保存・エクスポートは継続する。
- モバイルcomposerの最上段を「参加AI」専用の行にし、品質 / DEBATE / effort / WEB / 統合 / BLINDは
  「詳細」で開閉する2段のstripへ移した。既定は畳んだ状態で、参加AIの選択と入力欄だけを常時表示する。
  各stripの見出し（品質・エフォート）と「詳細」buttonは固定し、中身だけを横スクロールさせる。
  APIキーが1本も無い場合は参加AIの位置に「APIキー未設定 — 設定する」を出し、設定画面へ直行できる。
- 参加Providerが空のまま送信した時のエラーを、候補ゼロ（APIキー未設定）と未選択で書き分けた。
  従来はキー未設定でも「参加するAIを1つ以上選んでください。」と表示し、選択UI自体が存在しない状態で
  利用者を詰まらせていた。あわせてAPIキー・認証・接続が原因のエラーbannerと回答card内の401表示へ
  設定画面を開く復帰導線を追加した。
- 設定画面（旧「接続とBYOK設定」）を初回導線順へ組み替えた。APIキー入力を最上部、実行方式の切替を最下部へ
  移し、保存buttonを画面下部の固定barへ出して未保存の有無を常時表示する。未保存のまま戻ろうとした場合は
  破棄確認を挟み、無警告で入力が消えないようにした。各Providerの発行ページを開くリンクと、
  キー欄の説明文（保存済み／未設定で切替）を追加した。
- 実API確認dialogの「実行して次回から表示しない」を、肯定buttonと同格のtext buttonから
  「次回からこの確認を表示しない」チェックボックス＋単一の実行buttonへ畳んだ。誤タップで安全弁が
  恒久OFFになるのを防ぐ。個人情報の警告はdialog末尾からタイトル直下へ昇格させ、マスク済み文面から
  該当箇所の抜粋（⟪REDACTED:...⟫ 付き）と誤検知の可能性を明示する。金額・回数の内訳は
  「内訳」見出し配下の小さめ表記へ落とした。
- ドロワー下部へ設定と利用状況への導線を追加し、キーボードのない端末ではCtrl/Cmd+Kの案内を出さないようにした。
  AppBarの実行方式表示を広幅・狭幅とも「DIRECT · 端末内保存」に統一した（従来は広幅のみ `DIRECT · LOCAL`）。
- 利用状況画面は、端末内モード（Direct BYOK）では中身の無いローカル予算・組織管理APIのcardを並べず、
  表示範囲の説明cardへ置き換えるようにした。状態chipの表記も「無効」から「未設定」「未接続」へ改めた。
- 回答card内のicon buttonのタップ領域をMaterial最小の48dpへ広げ、Provider名のブランド色を背景に対して
  コントラスト比4.5:1を満たす明度へ寄せた（ライトテーマでChatGPT / Grokが読みにくかった）。
  統合をスキップしたターンのchipは内部値の `none` / `SKIPPED` をやめ「統合なし」1つに整理した。
- 会議条件を開始時snapshotへ固定し、生成中の入力欄とoptionを次回用として編集可能にした。添付操作は生成中も
  lockし、送信buttonを同位置の停止buttonへ切り替えて現在runへの二重送信を防ぐ。次回下書きは自動queueしない。
- Provider設定をキー状態とmodel要約付きの折りたたみcardへ変更した。回答cardもProvider、model、実効effort、状態を
  1行headerへ集約し、本文、DEBATE前の最初の回答、immutable attempt監査履歴を段階的に開けるようにした。
  現在回答、最初の回答、保存済み各attemptの本文には個別のcopy操作を追加した。
- composerを、model・出力枠のLOW / BALANCED / HIGH、独立した設定値 / LOW / MEDIUM / HIGHの推論エフォート、
  検索なし / 検索ありへ分離した。AUTOは設定画面の既定エフォートとして保存し、質問本文を分類・書換せず
  model familyの固定policyで実効effortを決める。
- 1 call出力上限をProvider別へ拡張した。Backend既定はClaude / ChatGPT / Grokが
  4,096 / 8,192 / 16,384、Geminiが8,192 / 16,384 / 32,768（LOW / BALANCED / HIGH）。Directも
  3 tierを同じmodel・上限で実装し、両modeの会議全体上限を196,608 tokenとした。
- `reasoning_mode=auto|low|medium|high` をtierから独立したrequest identity、plan、SSE metadata、保存turnへ追加した。
  未知・非対応modelでは未確認のreasoning fieldを送らずProvider defaultを使う。
- Direct planは初回promptの質問、添付、memory、履歴、systemをUTF-8 byteで積算し、1 call 1 MiBを超える送信を
  Provider呼出前に拒否するようにした。生成結果に依存するDEBATE/統合入力は未知として明示する。
- Direct BYOKを製品の既定runtimeとし、PC backend、LAN、Tailscaleを必須構成から外した。Reference serverは
  SAFE MOCK、durable run、予算、telemetry、UI比較を行う開発用経路として維持した。
- Directのpartial/incomplete本文は表示・保存する一方、完了回答としてDEBATE・統合へ含めないようにした。
  同一HTTP要求の自動retryと途中回答の自動継続は行わない。
- 通常の「実APIを使用します」確認を既定ONの公開設定にした。dialogの「実行して次回から表示しない」または
  設定画面で通常警告だけをOFFにできるが、秘密候補のblockと個人情報候補の追加確認は常に維持する。
- Directの応答待ち上限をProvider・tier・実効effort・Web検索別の2〜15分へ変更した。Grok HIGHは最大15分、
  全要求のHTTP attemptは1回のままで自動retryしない。
- 公開上の製品名を `Clage Cook` へ統一し、README、設計・運用文書、OpenAPIタイトル、CLIヘルプ、
  Flutter package説明、User-Agent、内部alias/cache namespaceから製品名としての `OSS` 接尾辞を削除した。
- `CLAGE_LIVE_API_ENABLED=true` でAPIキーが0件の場合は暗黙のMockへ移行せず、参加Providerなしとして
  fail-closedにした。Mock混在は `INCLUDE_MOCK_PROVIDERS=true` の明示時だけ許可する。
- 対応Claude modelへ独立reasoning modeの `output_config.effort` とadaptive thinkingを反映し、Web検索toolを
  `web_search_20260318` へ更新した。非対応・未知modelはbasic版へ安全にfallbackする。
- Anthropic管理telemetryは任意offsetの予算windowを同一期間と見せず、UTC日次bucketの問い合わせ期間と
  `complete_through` を別metadataとして返す。cache keyも予算日の切替で失効する。
- request ID indexと、ConversationStore revision・予算日・price versionをkeyにしたBudgetGuardの
  actual-cost snapshot cacheを導入し、budget判定の重複full scanを削減した。local usage telemetryは
  引き続きrequestごとに全conversation JSONを走査する。
- durable SSE保存はanswer/synthesis checkpointと終端turn保存へ絞った。終端保存ではその時点までの
  非終端生成eventをjournalへ確定し、再生時の `error` / `done` はdurableなturn状態から再構成する。
- admin telemetryは既定OFF・read-only・別資格情報の境界を維持し、financeは未観測課金を0円解放しない
  fail-closed予算予約として保持した。管理値とlocal推定を請求書として混合しない。
- dispatch直前の会話・添付・runtime model snapshotからauthoritative planを再構築し、参加Provider/modelと
  統合Provider/modelをrun完了まで固定した。予算予約も同じplanと予算日でatomicに再検証する。
- 完了した再生成stateはdurable attemptから再生できるためregistryから直ちに除去し、conversation全体を含む
  `state.result` を共通の `RUN_RETENTION_SEC`（既定1時間）へ保持しないようにした。

### Fixed

- **Claudeのweb検索多用時に回答が途中で切れる問題(issue #20)**: `pause_turn`(サーバ側
  ツールループの一時停止)を受けたら、返ってきたcontentをassistantターンとして積んで
  継続リクエストを送り、最後まで生成するようにした(上限3回・全体timeoutの残り時間内)。
  各回のusageは合算して報告する。上限到達時はエラーにせず、それまでの本文を従来どおり
  partial/incompleteとして返す。継続中に `refusal` が返った場合は継続前の途中出力ごと破棄する。
- **端末内会話が1件の破損で全損に見える問題(issue #18・アプリ側)**: 一覧・検索がmanifestの全entryを
  読み、1件でも壊れていれば例外になり、それを起動処理が「実行環境を初期化できない」と受けて
  接続そのものを無効化していた。結果、健全な会話を開くこともエクスポートすることもできなかった。
  一覧・検索は破損をskipして `defects` として報告し、部分成功を成功として扱うようにした。
  特定の会話を開く経路(`read`)は従来どおりfail-closedのまま。壊れた会話はmanifestから外して
  隔離keyへ退避でき（中身は端末に残す）、残ったrecordからindexを組み直す明示操作も追加した。
  破損が残っている間は `compact()` が物理削除しない。
- **Direct BYOKで届かない再接続導線(issue #19)**: 保存turnの `status` 文字列で出し分けていた
  「実行へ再接続」ボタンと切断バナーの再接続を、実行方式の能力(`supportsRunReconnect`)で
  出し分けるようにした。Direct BYOKはrunの状態をプロセス内にしか持たないため表示しない。
- **添付付きターンの再生成が理由不明で失敗する問題(issue #19)**: Direct BYOKの添付本文は端末
  メモリだけに置くためアプリ再起動で失われるが、再生成計画が例外を投げるだけで理由も回避策も
  示していなかった。`allowed: false` と `block_reasons: ["attachment_snapshot_missing"]`、
  および制約と回避策を説明するwarningを返すようにした。
- **二重課金の穴**: 破損した会話JSONを黙って読み飛ばしていたため、その会話の `request_id` が
  起動時のrequest indexから落ち、`/api/chat` の「保存済みturnは再実行しない」防波堤を素通りして
  同じ `request_id` が再実行=再課金され得た。破損がある間は「実行済みかどうか判定できない」ことを
  409 `request_index_incomplete` で明示して止めるようにした(issue #18)。
- **再生成後のリプレイが古い回答を返す不整合**: 保存turnが答えを `answers`/`synthesis` と `event_log` の
  2箇所に持ち、再生成が後者を更新していなかったため。`event_log` を廃止して単一ソース化した。
- ファイルは存在するのに読み取れない会話を、404「会話が見つかりません」と偽らずに
  422 `conversation_corrupt` として区別するようにした。起動時の中断turn確定で読み飛ばした
  ファイルもwarningで報告する。
- 再生成のキャンセル経路で、予算清算とattemptの中断確定が `_complete_critical` に包まれておらず、
  二重キャンセル時にattemptが `dispatching` のまま残り予算予約が未清算になり得た。chat側と同じ形に
  揃えた(issue #22)。
- planに `max_output_tokens` が無いとき出力コストを0(=無料)と見積もっていた。欠落は
  「見積り不能」として `plan_max_output_tokens` をmissingへ計上し、`CLAGE_BUDGET_UNKNOWN_POLICY` の
  判断へ委ねるようにした(issue #22)。
- 完了した実行状態が、retention経過後もregistryへのアクセスが無い限り残り続けた。件数上限を
  追加し、古い完了stateから捨てるようにした(issue #22)。
- Claudeのeffort対応model一覧が `config.py` と `providers/anthropic.py` で二重管理されており、
  片方だけ更新すると「planはpinnedなeffortを表示するのに実送信では `output_config` が落ちる」
  状態になり得た。`backend/model_capabilities.py` の表を唯一のソースにした(issue #21-1)。
- `resume_request` が完了turnからだけ消えていた(orchestratorの戻り値でpending turnごと差し替わるため)。
  完了turnにも引き継ぐようにし、turnの任意フィールドが「いつ存在するか」をHANDOFF.mdの表にした。
- `CLAGE_BUDGET_UNKNOWN_POLICY` の不正値を帯域内の魔法値 `"invalid"` で表現するのをやめ、
  安全側の既定へ落としたうえで起動時に設定エラーとして止めるようにした。

- DEBATE round 2がpartial / failureになった場合も、送信済みcallの実測usageをround 1へ合算し、partial本文と
  監査metadataを保存するよう修正した。送信済みなのにusageが無い場合は照合済みにせず、予算予約を維持する。
- HTTP 529を含む全5xxをretry対象にし、数値/HTTP-dateの `Retry-After` を尊重した。60秒を超える指定では
  早すぎる再試行も長時間sleepも行わず、そのcallを失敗として返す。
- Claudeの `refusal` をallowlist済み固定分類で通知し、`pause_turn` をpartial/incompleteとして保持した。
  Geminiの `budget_exceeded` もincomplete reasonとして保持する。
- chatのProvider送信前に失敗・cancelされた予算予約を解放し、送信後のusage不明予約だけを照合待ちへ残すよう
  所有権を整理した。開始handshake中のcancelによるtask/予約の二重解放raceも解消した。
- 起動時に再生成attemptの `reserved` / `dispatching` / `running` を `interrupted` へ復旧し、再生成を
  background registry、再接続可能な同一ID、cancel endpointへ統合した。Provider実行中は会話lockを保持しない。
- 過去turnを別日に再生成した費用を元turnの日付へ誤帰属して日次budgetから漏らす問題を修正し、予約時の
  `budget_day`、attempt日時、turn日時の順でattemptごとに集計するよう変更した。
- branchにcopyされた同一turn/attemptのusage・費用を全会話集計で二重加算する問題と、
  重複request ID indexが親の正当なreplayを別branchへ誤結合する問題を修正した。
- settled予約へ実測換算額をdurable保存し、conversation削除後も当日のcommitから消えないようにした。
  応答modelを価格換算できない場合は予約上限を保持し、手動reconciliationも既知実測額または予約上限を
  `settled_after_manual_reconciliation` として残す。
- cached inputの包含関係をProvider別に正規化した。Anthropicの `input_tokens` はcache read/writeとは独立した
  uncached区分として使い、他Providerでは正規化済みinputからcached inputを差し引いて二重計上を防ぐ。
- reasoning課金をProvider response shape別に正規化した。Gemini Interactionsの `total_thought_tokens` だけを
  output外数として加算し、OpenAI/xAI Responsesのreasoning内包outputを二重加算しない。旧xAI Chat互換shapeの
  durable Grok usageは `total_tokens - input_tokens` をfallbackにしてcompletion外数を保持する。
- active予約のgross額と同じrequestで観測済みの実績を重ねず、差額だけを
  `active_reservation_top_up` としてcommittedへ加えるよう修正した。APIとFlutterは予約総額と追加拘束を別表示する。
- branch片側で再生成した際に既存回答を包むoriginal attemptを元turnと同じbilling identityへ戻し、未包装の
  copy元回答との二重費用・token計上をfinanceとlocal usage telemetryの双方で防いだ。実際の追加再生成attemptは
  独立identityのまま保持する。
- registryから完了stateを即時除去した後の保存済みterminal再生成replayを、rate limiterとactive conversation
  claimより前に解決し、外部呼出を行わない同一結果が新規実行向けの429/409で拒否される問題を修正した。
- 解放済み予算予約の無条件再利用、照合待ち/確定済みIDの再dispatch、run slot待機中の日跨ぎ、
  ownerの異なる同一予約を状態別遷移・dispatch直前refresh・durable `budget_reservation` で防止した。
- cancelを複数回送ったときcleanupへ再度 `CancelledError` を注入してconversation claimを残す問題を修正し、
  shutdown時もbackground runをcancel・回収してからdata directory lockを解放するようにした。
- Provider結果後のchat/再生成について、終端保存・budget settle・結果公開をcancelから保護し、先に確定した
  completed/failedをcancelledで上書きしないようにした。cancel APIは `terminal_outcome` を返し、Flutterも
  完了・失敗・停止を対応する確定表示とiconへ分ける。再生成Provider failure後のcleanup中のcancelもfailedを
  維持し、未確定usageの台帳settleと `cancelled=false` のdurable attempt中断記録を完走する。
- SSE bodyの完全沈黙を90秒で切断状態へ移し、keepalive commentでidle期限を更新するようにした。
  Content-Type、event ID永続/リセット/NUL、Unicode安全なerror切詰め、非2xx bodyの10秒/64 KiB上限も修正した。
- Flutterのrefresh/会話切替と添付uploadの世代race、未保存URLへのruntime PATCH、メモdialog label、
  preflight error欠落を修正した。読込中会話への誤送信、stream resource残留、旧接続先statusの誤表示も防止した。
- Androidでアプリをbackgroundへ移した際に、遅いHIGH回答の接続がprocess凍結とbackground network制限で
  失われ得る問題を修正した。foreground serviceのstop失敗・timeout後は次回runで必ず新しい開始ackを取り直し、
  Android 15以降の `Service.onTimeout()` でも猶予内にserviceを停止する。
- 会話lock/rate-limit entryの無限増加、cancel直前の偽 `already_done`、plan実行間TOCTOU、公開routeの
  scrub漏れ、insightsの丸め平均と重複数値scanを修正した。
- 再生成実行時の追加system指示をplan入力量に含め、内部failureをuser cancelとして永続化する分類ミスを修正した。
- conversation upload/deleteを同じlockへ統合し、削除中uploadのorphan化を防いた。重いstore・budget I/Oも
  async実行経路からthreadへ逃がし、全会話scan中の長時間store lock保持を廃止した。
- runtime設定をplanごとに1回だけsnapshotし、再生成を含むProvider/model、SSE meta、全Provider失敗時の
  synthesis sourceが途中の設定変更と混在しないようにした。
- 保存済みchat replayと実行中runへのjoinを新規plan・添付TTL・予算・確認判定より先に解決し、無課金の
  再接続が現在設定で拒否される問題を修正した。添付順序もrequest fingerprintへ含めた。
- 部分的なprice tableでも判明済みrateカテゴリをknown subtotalへ加え、unknown `allow` 時に既知費用を
  予約から落とさないようにした。mock/skipped entryは実費の未価格requestへ数えない。

### Security

- CIと通常pytestはAPIキーを持たず、local `.env` のlive設定から独立してSAFE MOCK境界を維持するようにした。
- `.env.*`、署名certificate、mobile provisioning、APK/AAB/IPAなどの生成物を公開対象から除外し、
  `.env.example` だけを明示的に追跡可能にした。
- Direct APIキーをSharedPreferencesへ平文fallbackせず、secret-first/public-commitのrevision不一致時は
  fail-closedにした。保存済みkeyを設定画面へ再表示せず、JSON/ZIP exportにも含めない。
- Web版はbrowser key extractionとProvider CORSのriskを避けるためDirect BYOKの有効化を拒否し、
  reference serverだけを利用可能にした。mode切替経由でもAPIキーを保存しないようUIと設定storeの双方で
  secretを消去し、旧Web recordも読み込まず削除を試みる。
- Directの新規質問と選択済み添付本文をProvider送信前に合わせてpolicy scanし、保存済みmemory/historyも
  後続promptへ入れる前にredactするようにした。
- Directの通信失敗をDNS、TLS、接続拒否、途中切断、network到達不能、client終了、timeoutなどの固定codeへ
  分類し、host、URI、生の例外本文、秘密値をUI・会話record・診断へ反射しないようにした。
- Native releaseをDirect BYOK専用にし、reference toggleをdebug/profileとWebへ限定した。Android releaseは
  cleartextを拒否し、app data全体をcloud backup/device transferから除外した。
- iOS/macOSへKeychain entitlement、Windowsへ固定secure-storage namespaceと非昇格manifest、Linux releaseへ
  compiler/linker hardening、WebへCSP・Trusted Types・`no-referrer` を追加した。
- Android release署名はprivateな `key.properties` に加え、4つすべて揃った `CLAGE_ANDROID_*` 環境変数を
  利用可能にした。部分設定・不完全propertiesはfail-closedでbuildを停止する。
- Web版ではBearer tokenがbrowser storage上にあり、XSS・拡張機能・共有端末から十分に保護されない旨を
  設定画面へ常時表示した。
- PDF text抽出を10秒上限の隔離subprocessへ移し、元添付の起動時/access時TTL purgeとdownloadの
  `X-Content-Type-Options: nosniff` を追加した。UTF-8 outputを明示し、stderrを保持せず、SHA-256/設定別の
  process内5分/64件TTL/LRU single-flight cacheとsubprocess同時2件上限を設けた。抽出cacheの期限切れは
  cache access時に除去し、process再起動時はcache全体を破棄する。
- live Web検索のtool別課金をtoken単価だけで「完全な金額見積もり」と扱う問題を修正し、
  budget有効時はunknown-cost policyでfail-closedまたは明示警告付き実行にした。`allow` でも価格判明済みの
  token小計は会議・日次上限で検査・予約し、不明なtool料金だけをlocal上限外として扱う。

### Known limitations

- Direct BYOKはWeb、durable run再接続、local金額budget、組織残高/請求取得、暗号化backup/importに未対応。
- Direct添付は1件512 KiB以下、1会話8件以下、合計512 KiB以下のUTF-8 text/Markdown/CSV/JSONだけで、
  内容はprocess memoryにだけ保持する。
  app再起動後の再利用・download・ZIP同梱はできない。
- Direct会話はSharedPreferences内のJSONで、application-level暗号化を行わない。
- Androidのforeground serviceは利用者・OSによる停止やprocess終了後の復旧を保証しない。iOS / Desktopの
  Direct background継続も未保証。
- Directでlocal保存retryがすべて失敗した課金済み結果を退避するrecovery outboxは未実装。
- Reference clientは本リポジトリのFastAPI契約に対応し、旧サブスクリプションserver固有APIの完全adapterではない。

## [0.2.0] - 2026-07-18

後方互換性を前提としない、公開を想定した最初の実用的なBYOK版です。repositoryは公開準備中のprivateです。

### Added

- 外部APIを呼ばず、参加Provider、model、最大call、最大output token、入力UTF-8 byte、retryを
  算出する `POST /api/plan`。
- API key、private key、認証token、秘密変数などをblockし、email・電話番号候補をconfirmにする
  決定論的local policy scannerと `POST /api/policy/scan`。
- AI名を決定論的な回答aliasへ置換してDEBATE・統合するBLIND optionと `!blind` command。
- 回答間のUnicode語彙・文字3-gram重なり、共有語、固有語、断定・不確実性・数値表現を示す
  完全localの `insights` SSE event。
- Provider responseの実測usageだけを表示するFlutter token台帳。cached、reasoning、tool tokenも
  Providerが返した場合だけ表示。
- Backend全文検索を使うdebounce付きFlutter検索、stale response破棄、error/retry UI。
- 会話JSONをclipboardへコピーするexport UIと `Ctrl/Cmd+K`、`Ctrl/Cmd+N` shortcut。
- 外部呼出前にpending turnを保存するdurable claimと、生成イベントごとに永続化するsanitized SSE journal。
- 完了、cancel、failure、server中断runの保存・再生と、範囲外 `Last-Event-ID` の409拒否。
- `CLAGE_DATA_DIR` の排他lockによる単一server process強制。
- `app/android/key.properties.example` を使う配布者固有のAndroid release signing設定。
- Provider共通のpartial/incomplete状態と、HTTP attempt、retry、outcome、usage不明リスクを残す監査field。
- xAIへ生のconversation IDを出さない安定SHA-256 `prompt_cache_key`。
- app再読込後も保存済みrunning turnから同一requestへ復帰または停止できるUIと、元の生request条件を
  保持する `resume_request`。
- 回答・統合を元結果を失わないimmutable attemptとして再生成するplan/API/UI、active pointer、
  synthesis stale警告、revision履歴。
- Provider応答headerのallowlist済みrate-limit snapshotと、全attemptを二重計上せず集計するlocal telemetry。
- 利用者設定のprice tableだけを使うDecimal金額換算、会議・日次budget、atomicな予約ledger、
  usage不明runのreconciliation pending状態。
- 通常推論キーと分離したAnthropic/OpenAI/xAIの読み取り専用admin telemetry、短期cache、部分失敗UI。
- Backend test/compileとFlutter format/analyze/test/Web buildを実行するGitHub Actions CI。
- worker/統合modelをrevision付きatomic JSONへ保存するruntime設定API/UI。
- 親conversationを変更せず対象turn直前から続ける編集分岐API/UI。
- opaque UUIDとowner検証を使うstreaming添付、MIME/signature/容量/TTL制限、text/PDF抽出。
- 4社server toolを使うターン単位・既定OFFのWeb検索、`!web`、構造化URL引用とクリック可能な出典UI。
- revision付きconversationローカルメモ、検索・分岐・外部promptへの安全な継承。
- JSON/Markdown/元添付を含む一時ZIP exportと、応答後の自動削除。
- reconciliation pending budget予約を明示確認後に解放するAPIとFlutter UI。

### Changed

- API keyの存在だけでliveへ移行する挙動を廃止。`CLAGE_LIVE_API_ENABLED=false` を既定とし、
  key設定済みでも4AIをSAFE MOCKで動作させるよう変更。
- Billable runは `confirm_live_api=true`、個人情報候補を含むbillable runは
  `confirm_sensitive_data=true` がなければ428で拒否するAPI契約へ変更。
- 生成HTTPの自動retry既定値を0回へ変更。retryを有効にした場合は、失敗試行を含むcall、output、
  input envelopeでrun limitを判定するよう変更。
- 会議履歴を含むinput byte上限、Provider call上限、最大output token上限をProvider呼出前に適用。
- DEBATEのround 1 / round 2についてmodel、elapsed、finish reason、usageを保持し、合算usageを返すよう変更。
- OpenAI、Gemini、xAIの保存設定を明示的な `store=false` に統一。
- conversation turn schemaへstatus、failure/cancel flag、usage incomplete flag、insights、event logを追加。
- Flutterの送信flowをplan/policy scan、必要な確認、SSE開始の順へ変更。
- Uvicornの複数workerを非対応とし、`--workers 1` を必須化。
- Android releaseのdebug signing fallbackを廃止。`key.properties` がある場合だけrelease署名する構成へ変更。
- 全文検索をGET queryからPOST JSON bodyへ変更し、検索語をURL・通常access logへ残さないようにした。
- 同一conversationの異なるrunを同時実行せず、`conversation_busy` 409で明示拒否するようにした。
- Flutterでpartial、複数HTTP試行、usage不完全、cancel/failure/interrupted状態を警告表示するようにした。
- SSE切断中の別run開始を防止し、`done` 後の取得失敗はSSEでなく保存済み会話だけを再読込するようにした。
- server起動時に前processが残したrunning turnを `interrupted` へ確定し、自動再実行しないようにした。
- Flutter接続設定をrevision・originで結合保存し、途中失敗、別origin、接続切替失敗をfail-closedにした。
- 接続URLのuserinfo/query/fragmentを拒否し、Bearer必須表示・空token拒否、検索200文字上限を追加した。
- SSE `done` 後に遅れて届くstream errorが完了状態を上書きしないようにした。
- local実測、price tableによる推定、Provider quota header、組織admin集計を互いに混ぜず表示するようにした。
- Web検索は初回回答だけへ限定し、DEBATE・統合で暗黙に再検索しないようにした。
- turn本文の編集をin-place mutationでなくimmutable branchとして扱うようにした。

### Security

- 秘密候補をconversation作成・Provider呼出前に遮断し、block responseへ秘密の生値を返さないようにした。
- 保存済みhistory、peer answer、synthesis材料を外部へ再送する前にも防御的にscan・redactするようにした。
- API keyを含まないprovider状態、safe mock/live gate、single-process状態、run上限を公開settingsへ追加。
- 任意exception文字列をSSE journal、保存JSON、client向けerrorへ流さないsanitizationを拡張。
- cancel responseで外部Providerの処理停止・課金停止を保証しないことを明示。
- `CLAGE_LIVE_API_ENABLED=true` かつ `CLAGE_AUTH_TOKEN` が空のserverを起動時に拒否する第三gateを追加。
- Android signing propertiesとkeystoreをgitignoreし、配布用secretをrepositoryから分離。
- ベンダーerror本文・生例外を反射せず、固定shapeのrequest auditだけをSSE・保存JSONへ通すようにした。
- 設定中のAPI key・Bearer tokenとblock候補を、成功したAI出力も含めてSSE、保存、一覧、検索、取得、
  exportの直前に再帰scrubする中央防御を追加した。
- Pydantic/FastAPIの入力検証エラーを値やvalidator詳細を含まない固定422へ変更し、Claudeの既知の
  billing/credit不足だけを本文非反射の固定分類で通知するようにした。
- admin telemetryを既定OFF、別資格情報、読み取り専用endpoint、Bearer必須に限定し、管理キーと
  Provider error本文・組織識別子を公開結果へ含めないようにした。
- budget checkと予約を同じprocess lock内で行い、送信済みusage不明runを0円として解放しないようにした。
- 再起動時の未settle予約を照合待ちへ昇格し、未照合backlogが設定件数へ達したら新規課金runを停止する
  circuit breakerを追加した。
- 添付pathをAPIへ公開せず、会話ownerとopaque UUIDの組合せだけで取得・削除できるようにした。
- 引用URLをHTTP(S)だけへallowlistし、Provider由来の任意schemeをクリック可能にしないようにした。
- ローカルメモの秘密候補を保存時にmaskし、外部転送前にも再scanするようにした。

### Known limitations

- price table未設定時は金額を推定しない。設定時も利用者入力単価によるlocal guardで、請求書ではない。
- policyとinsightsはpattern/文字列比較であり、秘密の完全検出、意味、事実性、品質、確信度を保証しない。
- 同じdata directoryを複数processまたは複数hostで共有できない。
- 画像入力・画像生成は未実装。画像添付は保存・exportのみでtext-only会議へ送らない。
- Web検索の内部実行回数はClaude以外で厳密保証せず、各社のmodel対応・料金・tool仕様に依存する。
- turn編集は親を変更しないbranchであり、既存turnのin-place書換えではない。
- OpenAI/Anthropicのcredit残高とGemini Developer APIの組織telemetryは取得せず、管理画面で確認する。
- local budgetは単一process向けで、Provider側spend cap、分散transaction、exactly-once課金を保証しない。

## [0.1.0] - 2026-07-18

### Added

- Anthropic、OpenAI、Google Gemini、xAIの公式APIを使うBYOK backendの初期実装。
- 利用可能なAIへの並列問い合わせ、任意の相互批評round、統合回答をSSEで配信する会議orchestration。
- API keyがない環境でも会議flowを確認できるmock mode。
- conversation history、一覧、検索、rename、delete、JSON exportを提供するFastAPI。
- Web、Windows、macOS、Linux、Android、iOSを対象とするFlutter clientの基盤。
- server URLとClage Cook用Bearer tokenの接続設定。
- SSE切断後のevent ID再接続、同一request IDの冪等再生、実行中会議の停止。
- conversation一覧、Markdown answer、tier、DEBATE、参加AI選択を備えたresponsive UI。
- Web、Windows、macOS、iOS、Android向けのlauncher icon。

### Security

- vendor API keyをbackendだけで保持する構成。
- 任意のBearer認証、同時実行数・rate・message長の制限。
- localhost限定の既定CORSと、Web client向けconversation ID header公開。
- 任意exception文字列をSSE、保存JSON、logへ流さないsanitization。
- Android、iOS、macOSからLAN/Tailscale上のlocal backendへ接続するためのplatform permission。
