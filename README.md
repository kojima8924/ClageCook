# Clage Cook

Claude、Gemini、ChatGPT、Grokへ同じ質問を並列に送り、回答を比較し、必要なら相互批評を
経て1つの結論へ統合するBYOK（Bring Your Own Key）のAI会議アプリです。

個人環境向けの旧Clage Cook実装は各社のサブスクリプションCLIを束ねますが、本リポジトリの
Clage Cookは各社の公式HTTP APIだけを使います。APIキーや有料サービスがなくても、4AI会議、
DEBATE、統合、履歴、検索などを完全なモックで試せます。

> `0.2.0` は後方互換性を前提としない、公開を想定した初期BYOK版です。GitHub repositoryは現在
> privateの公開準備中です。既定値は、APIキーを設定していても
> 外部APIを呼ばない `SAFE MOCK` です。実APIはlive設定、Bearer認証、会議ごとの明示確認をすべて
> 満たしたときだけ呼び出され、各社との契約に応じて料金が発生する可能性があります。

## 実装済み

- Anthropic Messages API、OpenAI Responses API、Gemini Interactions API、xAI Responses API
- 4AIの並列実行、完了順SSE配信、1社だけ失敗した場合の部分成功、成功回答だけを使う統合
- `low` / `balanced` / `high` tier、参加AI選択、統合省略、先頭コマンド
- 1ラウンドの相互批評（DEBATE）と、AI名を伏せて批評・統合するBLIND
- 課金APIを呼ばない会議前plan: 入力UTF-8 byte、Provider呼出、最大出力token、再試行の上限
- ローカルpolicy scan: 秘密情報候補を遮断し、メールアドレス・電話番号候補を確認対象化
- 設定中の秘密値とblock候補をSSE、保存JSON、検索、取得、エクスポートの全公開経路で再帰除去
- 完全ローカルの語彙比較insightsと、Providerが返した実測tokenだけを表示する利用量台帳
- partial/incomplete応答、HTTP試行・再試行回数、利用量不明リスクを共通監査fieldとして保存・表示
- 共通の会話履歴、会話単位ロック、1会話1JSONの原子的保存
- サーバー側全文検索、タイトル変更、削除、JSONエクスポートとFlutterの対応UI
- `request_id`によるdurable claim、SSE event journal、保存済みrunning runの再接続・停止と起動時中断確定
- chatと再生成で共有するbackground run registry、切断から独立した実行、明示停止、再生成attemptの中断復旧
- Provider結果後の終端保存・budget settle・結果公開をcancelから保護し、先に確定した完了/失敗を維持する
  terminal outcome契約
- live時に必須のBearer認証、localhost限定の既定CORS、レート・同時実行・入出力上限
- Web / Windows / macOS / Linux / Android / iOS向けのレスポンシブFlutter UI
- Markdown表示、接続先originへ結合したBearerトークン保存、Web保存警告、キーボードショートカット
- reverse proxy pathを保った接続URL検証、200文字上限の本文検索、終端SSEの競合防止
- SSE commentもactivityとして扱う90秒idle watchdog、会話選択の世代guard、upload中の切替防止
- 回答・統合のimmutable再生成attempt、active pointer、統合のstale表示、revision履歴UI
- Provider応答headerのallowlist済みrate-limit観測と、保存済み全attemptのlocal usage集計
- 利用者が正確なmodel単価を設定した場合だけ働くDecimal金額換算、会議・日次budget予約guard
- 別管理資格情報を明示有効化した場合だけの読み取り専用組織telemetry（OpenAI、Anthropic、xAI）と
  Provider別の実効集計期間
- revision付きruntime model設定、統合Provider/model設定と、キーを返さない公開catalog
- 完了turnの直前から親を破壊せず続ける編集分岐と、分岐元metadata
- owner固定opaque ID、streaming upload、MIME/signature/容量/TTL検査、隔離subprocessと時間上限付きPDF抽出を
  備えた添付
- 既定OFFのターン単位Web検索、4社のserver tool、構造化引用とクリック可能な出典UI
- 会話ごとのrevision付きローカルメモ、検索・分岐・promptへの安全な継承
- JSON/Markdown exportと、元添付を含む一時ZIPの保存・送信後自動削除
- 照合待ちbudget予約を外部請求確認後に確定状態へ移し、実測額または予約上限を日次commitへ保持する
  明示確認付き手動reconciliation UI/API
- GitHub ActionsでのBackend test/compileとFlutter format/analyze/test/Web release build

## 安全な既定値

実APIの利用には、次の3つのgateをすべて満たす必要があります。

1. バックエンドでAPIキーを設定し、`CLAGE_LIVE_API_ENABLED=true` を明示する。
2. 十分に長い `CLAGE_AUTH_TOKEN` を設定する。live gateがtrueなのにtokenが空なら、serverは
   起動を拒否する。
3. 各会議の実行直前に、Flutterの確認画面で承認するか、`POST /api/chat` へ
   `confirm_live_api=true` を送る。

メールアドレスまたは電話番号らしい文字列をlive APIへ送る場合は、さらに
`confirm_sensitive_data=true` が必要です。APIキー、秘密鍵、認証tokenなどの秘密候補は
確認ではなく実行前に遮断されます。policy scanは決定論的なローカルパターン一致であり、
秘密・個人情報の完全な検出を保証しません。

生成APIの自動再試行は、応答を失った試行でも課金され得るため既定で0回です。会議前planは
最大呼出回数、最大出力token、送信するUTF-8 byte量を安全側に算出します。価格tableを設定しない
既定状態では金額を推測しません。利用者が契約に合うmodel単価を明示した場合だけ、安全側の最大金額、
会議上限、固定UTC offsetの日次上限を適用します。usage不明の送信済みrunは0円扱いにせず、照合待ちの
予約としてbudgetを拘束します。再起動時に未settle予約を照合待ちへ昇格し、照合待ち件数が
`CLAGE_MAX_UNRECONCILED_RESERVATIONS` に達すると新しい課金runを停止します。
usageを価格換算できたrunは実測額をsettled ledgerへ保存し、conversationを削除しても当日のcommitから
差し引きません。応答modelを価格換算できない場合は予約上限を保持します。手動reconciliationも照合待ちを
0円へ解放せず、完全に価格換算できる既知額、できなければ予約上限を
`settled_after_manual_reconciliation` として当日のcommitへ残します。
有効予約のgross額はtelemetryへ別表示しますが、日次commitには同じrequest IDで観測済みの実績との差額だけを
`active_reservation_top_up` として足します。したがって、部分usageが保存された実行を「実績 + 予約総額」で
二重拘束しません。
送信直前に履歴・memory・添付・modelを同じsnapshotで再計画し、予算予約の金額と日付を再検証した後、
参加Provider/modelと統合Provider/modelをrun完了まで固定します。解放済み予約は新しい予算check後だけ再利用でき、
照合待ち・確定済み予約は保存済み結果なしに再dispatchしません。
価格表が入力・出力など一部の単価だけを持つ場合も、既知カテゴリの小計は予算判定・予約から落とさず、
未知カテゴリだけをunknown-cost policyへ委ねます。

## 構成

```text
Flutter client
  ├─ local confirmation UI / secure Bearer storage
  └─ REST + SSE
       └─ FastAPI (single process per CLAGE_DATA_DIR)
            ├─ local plan + policy scan
            ├─ price snapshot + durable budget reservation
            ├─ local usage + optional read-only admin telemetry
            ├─ conversation store + durable event journal
            └─ conference orchestrator
                 ├─ Claude   ─ Anthropic API
                 ├─ Gemini  ─ Google API
                 ├─ ChatGPT ─ OpenAI API
                 └─ Grok    ─ xAI API
```

ベンダーAPIキーはバックエンドだけが読みます。Flutterへキーを返したり、Flutterに保存したり
する経路はありません。入力検証エラーは値やvalidator詳細を反射しない固定応答です。Providerの
成功出力も信頼せず、設定中のAPIキー・Bearer tokenとblock判定された秘密候補を、SSE配信、event journal、
conversation保存、検索、取得、JSONエクスポートの直前に再帰的に除去します。

## クイックスタート

### 1. バックエンド

Python 3.10以降を用意します。

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

Windowsでは、依存を入れた後に `backend\run_server.bat` でも起動できます。保存JSONと実行の
冪等性を守るため、同じ `CLAGE_DATA_DIR` を使えるサーバープロセスは1つだけです。起動時の
ファイルロックで複数プロセスを拒否するため、Uvicornは必ず `--workers 1` で起動してください。

初期状態の `backend/.env` は `SAFE MOCK` です。実APIを使うときだけ、自分のキーとlive gateを
設定して再起動します。

```dotenv
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=
XAI_API_KEY=

# まずfalseのままモックで確認し、実APIを使うときだけtrueへ変更
CLAGE_LIVE_API_ENABLED=false

# live APIをtrueにする前に、十分に長いrandom値を必ず設定
CLAGE_AUTH_TOKEN=
```

`CLAGE_LIVE_API_ENABLED=false` の間は、キーの有無に関係なく4AIすべてがモックで、Bearer認証は
任意です。`true` にするとBearer認証が必須になり、設定済みの実Providerだけが参加します。
未設定AIをモックとして混在させたい場合だけ
`INCLUDE_MOCK_PROVIDERS=true` を明示してください。モデル、統合役、上限値は
[.env.example](backend/.env.example) から上書きできます。

Flutter設定画面からもworker modelと統合Provider/modelを変更できます。値は
`CLAGE_DATA_DIR/.control/runtime-settings.json` へrevision付きで原子的に保存され、runtime設定がenv、
既定値より優先されます。1つのplanはruntime設定を1回だけsnapshotし、実行中のSSE metadataも同じ
Provider/modelを表示します。API keyや任意endpoint URLをruntime設定へ保存する機能はありません。

### 任意の価格・予算guard

`backend/pricing.example.json` をprivateな場所へcopyし、利用するmodel IDと契約単価へ置換してから
`CLAGE_PRICE_TABLE_FILE` で指定します。example内の値は実価格ではありません。`CLAGE_PER_RUN_BUDGET_USD`
または `CLAGE_DAILY_BUDGET_USD` を設定すると、未登録modelやusage不明を既定でfail-closedに扱います。
checkと予約は単一server process内のlock下で行い、予約ledgerを `CLAGE_DATA_DIR/.control/` へatomic保存します。
実測費用のcache token区分はProviderごとのusage契約に従います。Anthropicの `input_tokens` はcache read/writeを
含まない独立区分としてそのまま使い、他の正規化済みProviderでは `input_tokens` から
`cached_input_tokens` を差し引いて二重課金を避けます。
reasoning tokenは表示値と課金outputを分けます。Gemini Interactionsの `total_thought_tokens` だけを
`total_output_tokens` の外数として加算し、OpenAI/xAI Responsesのoutput tokenはreasoning内包として
二重加算しません。旧xAI Chat互換shapeのdurable Grok usageはreasoningがcompletion外数の場合があるため、
`total_tokens - input_tokens` をoutput側の安全なfallbackとして使います。

### 任意の組織管理telemetry

`CLAGE_ADMIN_TELEMETRY_ENABLED=true` と別管理資格情報を設定した場合だけ、`/api/telemetry` が組織集計を
読み取ります。この機能を有効にすると `CLAGE_AUTH_TOKEN` は必須です。推論キーを管理APIへ転用せず、
`ANTHROPIC_ADMIN_KEY`、`OPENAI_ADMIN_KEY`、`XAI_MANAGEMENT_KEY` を分離してください。取得は短期cacheされ、
top-up、支払方法、spend limitの変更endpointは実装していません。Gemini Developer APIのcredit/組織usageは
AI Studioの管理画面で確認します。要求期間は `CLAGE_BUDGET_UTC_OFFSET` の暦日で作りますが、Anthropicの
Usage/CostはUTC日次bucketのため完全一致しない場合があります。API応答とFlutterはProviderごとの実効期間、
最終完全bucket、予算期間との一致有無を分けて表示します。

### 2. Flutterクライアント

```powershell
cd app
C:\dev\flutter\bin\flutter.bat pub get
C:\dev\flutter\bin\flutter.bat run -d chrome
```

一般的なFlutter環境では `flutter` をフルパスなしで実行できます。起動後、右上の設定から
バックエンドURLを指定します。PC上では既定の `http://127.0.0.1:8000` を使えます。

スマートフォンから接続する場合、`127.0.0.1` はスマートフォン自身を指します。バックエンドを
`--host 0.0.0.0 --workers 1` で起動し、PCのLAN IPまたはTailscale IPを設定してください。
その場合は長いランダム値を `CLAGE_AUTH_TOKEN` に設定し、同じ値をFlutterのBearerトークン欄へ
保存します。live APIでは接続元がlocalhostでもこのtokenが必須です。インターネットへ直接公開しないで
ください。詳しくは
[SECURITY.md](SECURITY.md) を参照してください。

## 動作モード

| 設定 | 参加AI |
| --- | --- |
| `CLAGE_LIVE_API_ENABLED=false` | キーの有無に関係なく4つのモック。Bearerは任意 |
| `true`、`CLAGE_AUTH_TOKEN` なし | 安全のためserver起動を拒否 |
| `true`、Bearerあり、APIキー0個 | fail-closed。参加Providerなしで実行を拒否 |
| `true`、Bearerあり、APIキー1〜3個 | 設定済みの実APIだけ |
| `true`、Bearerあり、4キーすべて設定 | 4つの実API |
| `true`、Bearerあり、`INCLUDE_MOCK_PROVIDERS=true` | 実APIと未設定モックを明示的に混在 |

統合役は `SYNTHESIZER_PROVIDER=auto` の場合、利用可能な実Providerから自動選択します。
safe mockでは専用モック統合役を使います。liveで全キー未設定の場合や、Provider障害時に暗黙のモックへ
置換することはありません。liveで未設定Providerも明示的にモック参加させる場合だけ
`INCLUDE_MOCK_PROVIDERS=true` を使います。

## 操作

Flutter画面ではtier、DEBATE、BLIND、統合、参加AIを選択できます。質問の先頭へ1行ずつ書く
コマンドも使用できます。

- `!high` / `!low`: 品質tierを変更
- `!debate`: 相互批評を1ラウンド追加
- `!blind`: AI名を回答A、回答Bのような決定論的aliasへ置換して批評・統合
- `!web`: 初回回答だけで各社のサーバー側Web検索を許可
- `!nosynth`: 統合を省略
- `!claude` / `!gemini` / `!chatgpt` / `!grok`: 参加AIを限定
- `!help`: ヘルプをローカル表示

BLINDは批評・統合用promptのProvider名を隠す機能です。利用者向けの回答カードと保存データでは
出典を確認できます。DEBATEは各回答者をもう1回呼び出すため、通常よりAPI利用量と待ち時間が
増えます。

WEBは既定OFFで、明示したターンの初回回答にだけ適用します。検索toolは通常のmodel利用量とは別に
課金される場合があります。Claudeには設定した `max_uses` を渡しますが、OpenAI/Gemini/xAIの
1リクエスト内の検索回数をこのアプリが厳密に保証するものではありません。Providerが返したURL引用は
構造化して保存し、Flutterでクリック可能な出典として表示します。
現在のprice tableはtoken単価だけを扱うため、live Web検索はtool料金を含む金額上限を完全に見積もれません。
budget有効時は費用不明とし、`CLAGE_BUDGET_UNKNOWN_POLICY=block` でfail-closedに停止します。
`allow` では警告付きで実行できます。この場合も価格が判明しているtoken小計は会議・日次上限で検査して
予約しますが、不明なtool料金はlocal金額上限の外側です。

会話検索はサーバー側のタイトル、ローカルメモ、質問、各回答、統合を対象にします。JSONはFlutterで
clipboardへコピーでき、ZIPはJSON、読みやすいMarkdown、保存期限内の元添付をまとめて端末へ保存します。
ZIP生成用一時fileは応答完了後に削除します。`Ctrl/Cmd+K` で検索、`Ctrl/Cmd+N` で新規会話へ移動できます。
会話headerのメモbuttonでは目的・制約・用語をrevision付きで保存でき、秘密候補は保存時にマスクされます。
完了済みturnの各回答と統合は再生成でき、元結果と過去attemptは削除されません。回答を再生成すると
既存統合をstale表示し、統合を再生成すると解消します。同じ再生成IDの再送は同一要求だけを再生し、
異なるtargetへの再利用は409で拒否します。完了した再生成stateはdurable attemptを正としてregistryから
直ちに除去するため、conversation全体を含む結果を共通の `RUN_RETENTION_SEC`（既定1時間）へ残しません。
その後の同一IDは保存attemptから再生します。保存済みterminal attemptの照合はrate limiterと
active conversation claimより前に行うため、無課金replayを新規実行向けの429/409で拒否しません。
完了turnの本文編集は親を上書きせず、対象turn直前までをcopyした新しいbranchを作り、編集本文をcomposerへ
戻します。copyされた過去turnの同一billing identityはlocal usage/費用集計で重複加算しません。再生成開始時に
既存回答を包むoriginal attemptも新しいAPI呼出ではないため、financeとlocal usage telemetryの双方で元turnと
同じidentityを使い、片側branchだけで再生成した後もcopy元回答の費用・tokenを二重計上しません。
実際に追加送信した再生成attemptは別identityです。
添付は会話にowner固定したopaque UUIDで参照し、許可したtext/Markdown/CSV/JSON/PDFは抽出して
未信頼資料としてpromptへ含めます。画像は保存・exportできますが、現在のtext-only会議promptには送りません。

## API概要

| Method | Path | 用途 |
| --- | --- | --- |
| `GET` | `/api/health` | バージョン、モード、単一process強制の稼働確認 |
| `GET` | `/api/settings` | キーを含まない公開設定と上限値 |
| `PATCH` | `/api/settings/runtime` | revision付きworker/統合model設定 |
| `GET` | `/api/telemetry` | local usage、予算、quota観測と任意の読み取り専用組織telemetry |
| `POST` | `/api/plan` | 外部APIを呼ばず会議の最大実行量と可否を算出 |
| `POST` | `/api/policy/scan` | 外部送信せず秘密・個人情報候補をローカル検査 |
| `POST` | `/api/chat` | 会議を開始または同一runへ再接続（SSE） |
| `POST` | `/api/runs/{request_id}/cancel` | 実行中会議へ停止を要求し、判明済みの `terminal_outcome` を返す |
| `GET` | `/api/conversations` | 会話一覧 |
| `GET` | `/api/conversations/{id}` | 会話取得 |
| `PATCH` | `/api/conversations/{id}` | タイトル変更 |
| `DELETE` | `/api/conversations/{id}` | 会話削除 |
| `GET` | `/api/conversations/{id}/export` | JSONエクスポート |
| `GET` | `/api/conversations/{id}/export.md` | Markdownエクスポート |
| `GET` | `/api/conversations/{id}/export.zip` | JSON、Markdown、元添付のZIP |
| `PATCH` | `/api/conversations/{id}/memory` | revision付きローカルメモ更新 |
| `GET/POST` | `/api/conversations/{id}/attachments` | 添付一覧・streaming upload |
| `GET/DELETE` | `/api/conversations/{id}/attachments/{attachment}` | owner固定添付の取得・削除 |
| `POST` | `/api/search` | JSON本文の検索語で保存会話を全文検索（URLへ検索語を出さない） |
| `POST` | `/api/conversations/{id}/turns/{run}/fork` | 対象turn直前からimmutable分岐 |
| `POST` | `/api/conversations/{id}/turns/{run}/regeneration-plan` | 回答・統合再生成の無課金plan |
| `POST` | `/api/conversations/{id}/turns/{run}/regenerate` | immutable attemptとして回答・統合を再生成 |
| `POST` | `/api/budget/reconciliation/{request_id}/release` | 外部請求確認後に照合待ちを確定し、既知額または予約上限をcommitへ保持 |

SSEイベントは次の順序要素で構成されます。

- `meta`: run、参加AI、tier、モード
- `answer`: 完了順の回答。DEBATE時は同じsourceの更新も配信。部分回答とHTTP監査情報を含み得る
- `phase`: DEBATEまたは統合の開始・完了
- `insights`: 完全ローカルの語彙重なり、共有語、注意表現
- `synthesis`: 統合結果または省略状態
- `error`: run全体の安全化されたエラー
- `done`: 終端と会話summary

接続が切れても、サーバー側の生成は続きます。同じ `request_id` と `Last-Event-ID` で途中から
再接続でき、範囲外のイベントIDは409で拒否されます。Flutterを再読込しても、保存済みrunning turnから
同じ生payloadと確認済みflagで同一runへ再接続するか、`request_id` 指定で停止を要求できます。
外部呼出より前にpending turnを保存し、answer/synthesisで中間checkpointを保存し、終端時に
その時点までの非終端生成イベントをsanitized event journalへ確定します。`error` / `done` はjournalへ
重複保存せず、再生時にdurableなturn状態から再構成します。このためサーバー再起動後も同じrunを
再課金目的で再実行しません。起動時に残っている `status=running` は、前プロセスにtaskが存在しないため
`interrupted` へ確定し、取得済み回答とusageを保持します。同じIDへ異なる質問を送ると409です。
保存済みturnまたは実行中runとのfingerprint照合は新規planより先に行うため、現在の添付TTL・予算・runtime設定・
確認flagが変わっても、外部APIを呼ばない同一結果の再生/joinは妨げません。添付はpromptへ入る順序も
fingerprintに含み、同じIDで順序を入れ替えると409です。
同一conversationへ異なるrunを同時送信した場合も `conversation_busy` の409で拒否し、先行runの
完了後に安全に再試行できます。

停止APIはローカルtaskへcancelを要求します。Provider結果が確定する前にcancelされた場合は、取得済み回答と
usageを不完全なcancelled状態として保存します。一方、Provider結果後にchat/再生成の終端保存、budget settle、
結果公開へ入った後は、このcritical sectionを完走し、先に確定したcompleted/failedをcancelledで上書きしません。
cancel responseの `terminal_outcome` は、判明済みなら `completed` / `failed` / `cancelled` を返します。5秒の
永続化待機内に終端しなければ未確定のまま返る場合があります。Flutterは確定値を文言と完了・失敗・停止iconへ
反映します。再生成Provider failure後のcleanup中にcancelされても、terminal outcomeはfailedのまま、予約台帳と
durable attemptの中断記録（user cancelではない）を完了します。ただし、すでに送信したHTTP要求を外部Providerが
停止したことや、課金が止まったことは保証しません。

## テストとビルド

```powershell
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest -q -p no:cacheprovider
python -m compileall .

cd ..\app
C:\dev\flutter\bin\dart.bat format --output=none --set-exit-if-changed lib test
C:\dev\flutter\bin\flutter.bat analyze
C:\dev\flutter\bin\flutter.bat test
C:\dev\flutter\bin\flutter.bat build web --release
C:\dev\flutter\bin\flutter.bat build windows --release
C:\dev\flutter\bin\flutter.bat build apk --release
```

通常テストは実APIキーを使いません。Provider契約テストはHTTP mockでURL、header、payload、usage
正規化を検証します。テスト件数やビルド済み対象は変更されるため、リリース時は上のコマンドの
実行結果を記録してください。iOS / macOSはMac、LinuxはLinuxでの最終ビルド確認が必要です。

Android releaseはFlutterの共通debug keyで署名しません。配布用に署名するときは
`app/android/key.properties.example` を `app/android/key.properties` へcopyし、自分のrelease/upload
keystoreのpath、alias、passwordへ置き換えてからbuildしてください。`key.properties`、`*.jks`、
`*.keystore` はgitignore対象です。`key.properties` がないrelease outputは配布用署名済みとは扱えないため、
配布前に必ず自分のkeyで署名・検証してください。

## 現在の制限

- 実キーを使った各社APIへのsmoke testは、意図しない課金を避けるため自動実行しません。
- price table未設定時のplanはUTF-8 byteと最大出力tokenの上限であり、金額を推測しません。設定時の
  最大金額も利用者入力単価と安全側token envelopeによるguard値で、Provider請求書ではありません。
- policy scanと語彙insightsは決定論的パターン・文字列比較で、秘密の完全検出、意味的一致、
  事実性、品質、確信度を判定しません。
- 公開経路の再帰scrubも既知値と同じpatternに基づく多層防御であり、未知・分割・難読化された秘密を
  完全には検出できません。
- 画像生成は未実装です。画像添付は保存・download・ZIP exportのみで、会議modelへ画像入力しません。
- 添付のPDF抽出は隔離したPython subprocessで10秒、先頭の設定page数・文字数までです。同一内容・設定の
  成功結果はprocess内だけの5分/64件LRU cacheで再利用し、PDF subprocessは同時に最大2本までです。期限切れの
  抽出cacheは次のcache access時に除去され、process再起動ではcache全体が消えます。これとは別に、元添付の
  保存TTLはserver起動時または添付access時にpurgeされます。ZIPに含まれる元添付byteは利用者が明示的に
  取得する原本のためsecret scrubを行いません。
- Web検索は各社のserver tool仕様・model対応・料金に依存します。Claude以外の内部検索回数を厳密制限せず、
  URL引用が返らない回答へ出典を捏造しません。token用price tableは別課金のtool料金を
  覆わないため、unknown costの扱いは上記policyに従います。
- ターン編集はin-place変更ではなくimmutable branchです。親と以降のturnを自動削除しません。
- OpenAI/Anthropicの組織credit残高、Gemini Developer APIの管理telemetryは公式APIから取得できません。
  xAI残高はProvider報告の符号を保持します。管理telemetryは反映遅延や権限により部分取得になり得て、
  Anthropicの日次UTC bucketはローカル予算期間の境界外を含む場合があります。
- budget ledgerは現在の単一process JSON設計に合わせたlocal guardで、Provider側spend capや分散transactionでは
  ありません。送信後切断のexactly-once課金や最終請求額は保証しません。
- `.control/budget-reservations.json` はidempotencyと日次commitのためsettled・reconciliation済み・
  `released_before_dispatch` も保持し、各状態更新で台帳JSON全体をatomic再書込します。個人ローカル利用を超えて
  数千〜数万のbillable runを長期保存すると、台帳件数に比例するI/Oとlock保持時間が増えます。SQLite化、または
  過去日aggregateと最小idempotency tombstoneへのcompactionは公開規模へ広げる前の候補で、まだ未実装です。
- 手動reconciliationは「外部請求に未観測chargeがない」と利用者が確認した事実だけを記録します。照合時点の
  既知額または予約上限はsettled ledgerへ残し、Providerの請求額・credit残高を自動確定する機能ではありません。
- 全文検索は保存JSONを走査する単純な部分一致です。大規模データ向けindexはありません。
- BudgetGuardのactual-cost集計は `ConversationStore` revision、予算日、price versionをkeyにcacheしますが、
  `/api/telemetry` のlocal usage集計は現在もrequestごとに全conversation JSONを走査します。
- 保存先は既定で `backend/data/` です。1つのdata dirを複数processや複数ホストで共有できません。
- 保存JSONには質問、回答、実測usage、SSE journalが含まれます。機密データとして管理してください。
- repositoryはAndroid配布用keystoreを同梱しません。配布者が自分のkeyを安全に管理する必要があります。

計画と設計原則は [VISION.md](VISION.md)、開発状況は [HANDOFF.md](HANDOFF.md)、変更履歴は
[CHANGELOG.md](CHANGELOG.md) を参照してください。

## ライセンス

[MIT License](LICENSE)
