# Clage Cook OSS

Claude、Gemini、ChatGPT、Grokへ同じ質問を並列に送り、回答を比較し、必要なら相互批評を
経て1つの結論へ統合するBYOK（Bring Your Own Key）のAI会議アプリです。

オリジナルのClage Cookは各社のサブスクリプションCLIを束ねる個人環境向けアプリですが、
このOSS版は各社の公式HTTP APIだけを使います。APIキーや有料サービスがなくても、4AI会議、
DEBATE、統合、履歴、検索などを完全なモックで試せます。

> `0.2.0` は後方互換性を前提としない初期OSS版です。既定値は、APIキーを設定していても
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
- live時に必須のBearer認証、localhost限定の既定CORS、レート・同時実行・入出力上限
- Web / Windows / macOS / Linux / Android / iOS向けのレスポンシブFlutter UI
- Markdown表示、接続先originへ結合したBearerトークン保存、キーボードショートカット
- reverse proxy pathを保った接続URL検証、200文字上限の本文検索、終端SSEの競合防止
- 回答・統合のimmutable再生成attempt、active pointer、統合のstale表示、revision履歴UI
- Provider応答headerのallowlist済みrate-limit観測と、保存済み全attemptのlocal usage集計
- 利用者が正確なmodel単価を設定した場合だけ働くDecimal金額換算、会議・日次budget予約guard
- 別管理資格情報を明示有効化した場合だけの読み取り専用組織telemetry（OpenAI、Anthropic、xAI）
- revision付きruntime model設定、統合Provider/model設定と、キーを返さない公開catalog
- 完了turnの直前から親を破壊せず続ける編集分岐と、分岐元metadata
- owner固定opaque ID、streaming upload、MIME/signature/容量/TTL検査、text/PDF抽出を備えた添付
- 既定OFFのターン単位Web検索、4社のserver tool、構造化引用とクリック可能な出典UI
- 会話ごとのrevision付きローカルメモ、検索・分岐・promptへの安全な継承
- JSON/Markdown exportと、元添付を含む一時ZIPの保存・送信後自動削除
- 照合待ちbudget予約を外部請求確認後にだけ解放する、明示確認付き手動reconciliation UI/API
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
既定値より優先されます。API keyや任意endpoint URLをruntime設定へ保存する機能はありません。

### 任意の価格・予算guard

`backend/pricing.example.json` をprivateな場所へcopyし、利用するmodel IDと契約単価へ置換してから
`CLAGE_PRICE_TABLE_FILE` で指定します。example内の値は実価格ではありません。`CLAGE_PER_RUN_BUDGET_USD`
または `CLAGE_DAILY_BUDGET_USD` を設定すると、未登録modelやusage不明を既定でfail-closedに扱います。
checkと予約は単一server process内のlock下で行い、予約ledgerを `CLAGE_DATA_DIR/.control/` へatomic保存します。

### 任意の組織管理telemetry

`CLAGE_ADMIN_TELEMETRY_ENABLED=true` と別管理資格情報を設定した場合だけ、`/api/telemetry` が組織集計を
読み取ります。この機能を有効にすると `CLAGE_AUTH_TOKEN` は必須です。推論キーを管理APIへ転用せず、
`ANTHROPIC_ADMIN_KEY`、`OPENAI_ADMIN_KEY`、`XAI_MANAGEMENT_KEY` を分離してください。取得は短期cacheされ、
top-up、支払方法、spend limitの変更endpointは実装していません。Gemini Developer APIのcredit/組織usageは
AI Studioの管理画面で確認します。

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
| `true`、Bearerあり、APIキー0個 | 4つのモック |
| `true`、Bearerあり、APIキー1〜3個 | 設定済みの実APIだけ |
| `true`、Bearerあり、4キーすべて設定 | 4つの実API |
| `true`、Bearerあり、`INCLUDE_MOCK_PROVIDERS=true` | 実APIと未設定モックを明示的に混在 |

統合役は `SYNTHESIZER_PROVIDER=auto` の場合、利用可能な実Providerから自動選択します。
safe mockまたは全キー未設定時は専用モック統合役を使います。Provider障害をモック成功へ
自動置換することはありません。

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

会話検索はサーバー側のタイトル、ローカルメモ、質問、各回答、統合を対象にします。JSONはFlutterで
clipboardへコピーでき、ZIPはJSON、読みやすいMarkdown、保存期限内の元添付をまとめて端末へ保存します。
ZIP生成用一時fileは応答完了後に削除します。`Ctrl/Cmd+K` で検索、`Ctrl/Cmd+N` で新規会話へ移動できます。
会話headerのメモbuttonでは目的・制約・用語をrevision付きで保存でき、秘密候補は保存時にマスクされます。
完了済みturnの各回答と統合は再生成でき、元結果と過去attemptは削除されません。回答を再生成すると
既存統合をstale表示し、統合を再生成すると解消します。同じ再生成IDの再送は同一要求だけを再生し、
異なるtargetへの再利用は409で拒否します。
完了turnの本文編集は親を上書きせず、対象turn直前までをcopyした新しいbranchを作り、編集本文をcomposerへ
戻します。添付は会話にowner固定したopaque UUIDで参照し、許可したtext/Markdown/CSV/JSON/PDFは抽出して
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
| `POST` | `/api/runs/{request_id}/cancel` | 実行中会議へ停止を要求 |
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
| `POST` | `/api/budget/reconciliation/{request_id}/release` | 外部請求確認済み予約の手動解放 |

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
外部呼出より前にpending turnを保存し、
各生成イベントをsanitized event journalへ永続化するため、サーバー再起動後も同じrunを再課金目的で
再実行しません。起動時に残っている `status=running` は、前プロセスにtaskが存在しないため
`interrupted` へ確定し、取得済み回答とusageを保持します。同じIDへ異なる質問を送ると409です。
同一conversationへ異なるrunを同時送信した場合も `conversation_busy` の409で拒否し、先行runの
完了後に安全に再試行できます。

停止APIはローカルtaskをキャンセルし、取得済み回答とusageを不完全状態として保存します。ただし、
すでに送信したHTTP要求を外部Providerが停止したことや、課金が止まったことは保証しません。

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
- 添付のPDF抽出は先頭の設定page数・文字数までです。TTL経過後は削除対象になり、ZIPに含まれる元添付byteは
  利用者が明示的に取得する原本のためsecret scrubを行いません。
- Web検索は各社のserver tool仕様・model対応・料金に依存します。Claude以外の内部検索回数を厳密制限せず、
  URL引用が返らない回答へ出典を捏造しません。
- ターン編集はin-place変更ではなくimmutable branchです。親と以降のturnを自動削除しません。
- OpenAI/Anthropicの組織credit残高、Gemini Developer APIの管理telemetryは公式APIから取得できません。
  xAI残高はProvider報告の符号を保持します。管理telemetryは反映遅延や権限により部分取得になり得ます。
- budget ledgerは現在の単一process JSON設計に合わせたlocal guardで、Provider側spend capや分散transactionでは
  ありません。送信後切断のexactly-once課金や最終請求額は保証しません。
- 手動reconciliation解放は「外部請求に未観測chargeがない」と利用者が確認した事実だけを記録し、
  Providerの請求額・credit残高を自動確定する機能ではありません。
- 全文検索は保存JSONを走査する単純な部分一致です。大規模データ向けindexはありません。
- 保存先は既定で `backend/data/` です。1つのdata dirを複数processや複数ホストで共有できません。
- 保存JSONには質問、回答、実測usage、SSE journalが含まれます。機密データとして管理してください。
- repositoryはAndroid配布用keystoreを同梱しません。配布者が自分のkeyを安全に管理する必要があります。

計画と設計原則は [VISION.md](VISION.md)、開発状況は [HANDOFF.md](HANDOFF.md)、変更履歴は
[CHANGELOG.md](CHANGELOG.md) を参照してください。

## ライセンス

[MIT License](LICENSE)
