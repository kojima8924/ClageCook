# Clage Cook OSS — 開発引き継ぎ

最終更新: 2026-07-18 / バージョン `0.2.0`

コンセプトは [VISION.md](VISION.md)、利用手順は [README.md](README.md)、安全上の前提は
[SECURITY.md](SECURITY.md) を参照してください。

## 現在地

初期のモックUI prototypeから、課金を明示的に武装するBYOK OSS版へ再設計済みです。`0.2.0` は
後方互換性を保証しません。APIキーを設定しただけでliveへ切り替わる旧挙動は廃止し、既定状態を
常にSAFE MOCKとしました。

### backend

- `providers/base.py`: Provider契約、timeout、限定retry、安全化されたerror、usage正規化
- `providers/anthropic.py`: Anthropic Messages API
- `providers/openai.py`: OpenAI Responses API（`store=false`）
- `providers/gemini.py`: Gemini Interactions API（`store=false`）
- `providers/xai.py`: xAI Responses API（`store=false`）
- `providers/mock.py`: 課金なしでfan-out、DEBATE、統合を確認するlocal mock
- `orchestrator.py`: 並列fan-out、部分失敗、DEBATE、BLIND、統合、tier、履歴、先頭command
- `planning.py`: Providerを生成せず、input/call/output/retry envelopeと実行可否を算出
- `policy.py`: 秘密・個人情報候補の決定論的local scannerとredacted text生成
- `scrubbing.py`: 設定中secretとblock候補を公開・保存データから非破壊で再帰除去
- `insights.py`: 外部通信なしのUnicode語彙・文字3-gram比較、共有語、注意表現
- `telemetry.py`: 保存済みoriginal/再生成attemptのlocal usageとquota観測集計
- `admin_telemetry.py`: 別管理資格情報による読み取り専用組織usage/cost/balance、部分失敗、cache
- `finance.py`: 明示price table、Decimal cost、会議・日次budget、durable予約ledger
- `attachments.py`: owner固定opaque UUID、stream upload、signature/MIME/容量/TTL、text/PDF抽出
- `runtime_settings.py`: revision付きworker/統合model overrideのatomic JSON
- `exporting.py`: 公開shapeのMarkdownと、元添付を含む一時ZIP
- `storage.py`: 1会話1 JSON、deepcopy、flush、fsync、atomic replace、全文検索
- `main.py`: REST/SSE、認証、CORS、live確認、run registry、durable claim、journal、再生、停止
- `config.py`: dotenv、model解決、safe mock/live gate、上限値、秘密を含まない公開設定

`CLAGE_LIVE_API_ENABLED=false` の間はキーの有無にかかわらず4mockです。`true` かつキーありの
Providerだけがliveになり、未設定Providerは既定でdisabledです。`INCLUDE_MOCK_PROVIDERS=true` の
ときだけliveとmockを混在させます。API障害をmock成功へ差し替えません。live gateがtrueなのに
`CLAGE_AUTH_TOKEN` が空なら、lifespanのstartup safety checkがserver起動を拒否します。SAFE MOCKでは
Bearer認証は任意です。

`POST /api/chat` はplanの上限を再検証し、billable runには `confirm_live_api=true` を要求します。
メールアドレス・電話番号候補をliveへ送る場合は `confirm_sensitive_data=true` も必要です。秘密鍵、
API key、GitHub/AWS token、Basic/Bearer、秘密変数などのblock候補は、conversation作成やProvider呼出
より前に422で拒否します。

planが返すinput量は、選択conversationの履歴、各answer、DEBATE用peer text、統合、live retryを
含むUTF-8 byteの安全側envelopeです。出力は各tierの `max_output_tokens`、呼出はDEBATE・統合・retry
まで含めて上限判定します。price table未設定時は金額を推定しません。設定時は完全一致するmodel単価と
安全側token envelopeから最大金額を算出し、会議・日次budgetを予約します。送信済みusage不明runは
`reconciliation_pending` として拘束し、0円へ解放しません。再起動は未settle予約を同状態へ昇格し、
`CLAGE_MAX_UNRECONCILED_RESERVATIONS` 件へ達したbacklogは新しいbillable runを停止します。

各Providerは `completion_status`、`partial`、`incomplete_reason` と、`request_audit` の
HTTP attempt/retry/outcome/final status/usage不明flagへ正規化します。timeoutや応答喪失後の課金有無を
捏造せず、ベンダー本文と生例外は外へ反射しません。xAIの `prompt_cache_key` はconversation IDの生値でなく
安定SHA-256 aliasです。pending/running turnは履歴から除外し、今回の質問を履歴として二重送信しません。
Claudeの既知のbilling/credit不足だけはallowlistで固定分類し、一般400や本文中の任意文字列は分類・反射
しません。FastAPIの入力検証失敗も入力値を含まない固定422です。

### durable run

`request_id` のclaimは最初の外部呼出より前に `status=running` のpending turnとして保存します。
各 `meta` / `answer` / `phase` / `insights` / `synthesis` は公開可能な形へsanitizeした後、SSE配信と
同時に `event_log` へ保存します。成功したProvider出力も信頼せず、設定中のAPI key・Bearer tokenと
block候補を中央scrubberで再帰除去します。同じ防御をconversation保存と既存JSONの一覧・検索・取得・
exportにも適用します。完了、cancel、一般failureのいずれでも同じturnを置換し、取得済み
answerとusageを残します。cancel/failure時は `usage_may_be_incomplete=true` です。

同じ `request_id` と同じfingerprintはin-memory runへ合流し、再起動後は保存turnを再生します。
lifespan開始時に前processが残した `running` turnをdurably `interrupted` へ置換し、取得済みanswer/usageを
保持したまま外部APIを自動再実行しません。異なる
payloadまたは異なるconversationで同じIDを使うと409です。`Last-Event-ID` がjournal範囲を超える
場合も409で拒否します。
同一conversationの異なるrequestは同時実行せず、後発を `conversation_busy` 409で拒否します。

cancel endpointはlocal asyncio taskをcancelし、永続化の完了を短時間待ちます。ただし送信済み
HTTP requestを外部Providerが停止したことや、課金停止を保証しません。responseの
`provider_stop_guaranteed` は常にfalseです。

完了済みturnのanswer/synthesis再生成は、originalを含むimmutable `attempts` と `active_attempts` pointerを
使います。同じregeneration ID・fingerprintは保存attemptを再生し、異なる要求でのID再利用は409です。
answer更新後は既存synthesisを `synthesis_stale=true` にし、synthesis再生成で解消します。起動時に残った
実行中attemptはinterruptedへ確定し、自動再実行しません。

`GET /api/telemetry` はlocal実績・budgetを常に外部通信なしで集計します。admin部分は
`CLAGE_ADMIN_TELEMETRY_ENABLED=true` の場合だけOpenAI/Anthropic/xAIへ別管理資格情報でアクセスし、
GeminiはAI Studio案内に留めます。admin有効時もBearerなしではserverを起動しません。

### single process

rate limiter、conversation lock、run registryはprocess-localです。`main.py` のlifespanは
`CLAGE_DATA_DIR/.server.lock` を排他的に取得し、同じdata dirを使う2つ目のprocessを起動時に拒否します。
Uvicornは `--workers 1` で起動してください。複数hostから同じ保存directoryを共有する構成は対象外です。

### Flutter

- `models.dart`: settings、plan/policy、conversation、turn、SSEの型と未知fieldに強いparse
- `services/api_client.dart`: REST、HTTP status、SSE frame解析、event ID再接続
- `services/settings_store.dart`: URLとBearerをrevision・originで結合するfail-closedな二record保存
- `screens/home_screen.dart`: 会話、検索、JSON/ZIP export、添付、ローカルメモ、編集分岐、plan/policy、再接続・停止
- `screens/settings_screen.dart`: URL、Bearer、接続test、SAFE MOCK/live gate、key非公開のProvider状態
- `screens/usage_screen.dart`: local usage、予算、quota観測、任意admin集計を別系統として表示
- `widgets/turn_view.dart`: 選択可能Markdown、4回答、統合、引用link、添付、DEBATE初稿、insights/usage panel
- `widgets/insights_panel.dart`: 語彙類似度とProvider実測token台帳。欠損値やpriceを推計しない

送信本文はpreflightとHTTP応答を通過するまで消しません。Flutterは `/api/plan` と
`/api/policy/scan` を並列に実行し、秘密候補はredacted textへの置換だけを提示します。billable planは
Provider/model、最大call、最大output token、input byte、retry envelopeを表示してから明示承認します。
price tableがある場合だけ最大金額、price version、会議・日次残額も表示します。

実行状態は `conversation_id + request_id` で追跡し、SSE切断後は同じID、同じconfirmation、最後の
event IDで再接続します。切断中は別runを開始できず、停止または再接続を選びます。`done` 後の履歴取得失敗は
SSEへ戻らず保存済みconversationだけを再読込します。partial、HTTP複数試行、usage不完全、turn終端状態は
回答・統合cardで警告表示します。検索は350ms debounce、stale response破棄、error/retry表示を備えます。
JSON exportはclipboardへコピーし、ZIPはJSON/Markdown/元添付を保存します。`Ctrl/Cmd+K` と
`Ctrl/Cmd+N` のshortcutを提供します。

pending claimはfingerprintに使った生tier/DEBATE/provider/BLIND/統合条件と確認flagを `resume_request` に
保存します。Flutterだけを再読込した場合、保存済みrunning turnから同一runへ復帰または停止できます。
実行・preflight中は接続設定を開けず、接続切替後のbootstrap失敗は旧clientへ戻らずfail-closedに切断します。
接続URLはuserinfo/query/fragmentを拒否し、reverse proxy pathだけを許可します。検索欄はbackend契約と同じ
200文字へ制限し、SSE `done` 後に遅れて届くstream errorは終端状態を上書きしません。

Androidの `app/android/app/build.gradle.kts` はreleaseへdebug signing configを流用しません。
`app/android/key.properties` が存在するときだけrelease signing configを作り、同fileと `*.jks` / `*.keystore`
はgitignore対象です。`key.properties.example` はplaceholderだけを持ちます。propertiesがないbuild outputは
配布用署名済みと見なさず、配布者自身のkeystoreで署名・検証してください。

## API / SSE契約

### REST

- `GET /api/health`: version、mode、active worker、single-process強制
- `GET /api/settings`: keyを含まないProvider状態と上限値
- `PATCH /api/settings/runtime`: revision付きmodel override
- `GET /api/telemetry`: local usage/budget/quotaと任意のread-only admin集計
- `POST /api/plan`: 外部通信なしのrun plan
- `POST /api/policy/scan`: 外部通信なしのlocal policy scan
- `POST /api/chat`: 新規・既存conversationのSSE run、同一run再接続
- `POST /api/runs/{request_id}/cancel`: local cancellation request
- `GET /api/conversations`: summary一覧
- `POST /api/search`: URLへ検索語を出さないJSON body形式の保存全文検索
- `GET /api/conversations/{id}`: conversation取得
- `GET /api/conversations/{id}/export`: JSON export
- `GET /api/conversations/{id}/export.md`: Markdown export
- `GET /api/conversations/{id}/export.zip`: JSON/Markdown/元添付archive
- `PATCH /api/conversations/{id}/memory`: revision付きローカルメモ
- `GET/POST /api/conversations/{id}/attachments`: 添付一覧/upload
- `GET/DELETE /api/conversations/{id}/attachments/{attachment}`: 添付取得/delete
- `PATCH /api/conversations/{id}`: rename
- `DELETE /api/conversations/{id}`: delete
- `POST /api/conversations/{id}/turns/{run}/regeneration-plan`: 再生成の無課金plan
- `POST /api/conversations/{id}/turns/{run}/regenerate`: immutable answer/synthesis attempt
- `POST /api/conversations/{id}/turns/{run}/fork`: immutable編集分岐
- `POST /api/budget/reconciliation/{request_id}/release`: 外部請求確認後の予約解放

### SSE

- `meta`: run metadata
- `answer`: 初回またはDEBATE後のProvider answerとpartial/request audit metadata
- `phase`: `debate` / `synthesis` の状態
- `insights`: deterministic lexical overlap
- `synthesis`: 統合、skip、または安全化されたfailure
- `error`: run-level failure/cancel
- `done`: terminal summary

SSEの `id` は1始まりのjournal indexです。keep-aliveはcomment frameで、event IDを消費しません。

## 検証

課金を伴うlive smoke testは通常の検証に含めません。Provider testは `httpx.MockTransport` で
URL、header、payload、response parse、usage正規化を検査します。backend testはplanning上限、policy、
insights、storage、durable replay、cancel、single-process guardを含みます。Flutter testはmodel parse、
API payload、SSE decoder、検索/export、shortcut、turn/insights表示を含みます。

```powershell
cd C:\code\ClageCookOSS\backend
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

件数はテスト追加で変わるため、この文書には固定しません。リリースhandoffでは実行したcommand、
成功・失敗、未検証platformをその時点の結果として報告してください。iOS / macOSはMac、Linuxは
Linuxでのnative buildが必要です。Starlette由来のwarningは失敗と混同せず、内容を都度確認します。
Android releaseの検証ではbuild成功だけでなく、意図したcertificateで署名されていることも確認します。

## 重要な不変条件

- API keyをresponse、SSE、log、conversation JSON、Flutter storageへ含めない。
- 公開・保存経路の中央scrubberを迂回してProvider出力や既存conversationを返さない。
- live gateとrunごとの明示confirmationを回避する別endpointを作らない。
- live gateがtrueのserverをBearer tokenなしで起動させない。
- planとchatで同じhistory、option、上限判定を使う。
- 同じ `request_id` に異なるpayloadを許可せず、未完了claimを自動再実行しない。
- client切断だけではProvider taskを止めない。
- cancelを外部Provider処理・課金の停止保証として表示しない。
- conversationのrename/delete/chatは同じconversation lockを通す。
- 同じdata dirを複数processで書かない。`--workers 1` を維持する。
- 部分失敗を会議全体の例外に昇格させない。
- partial live構成で未設定Providerを暗黙のmockとして実行しない。
- BLINDを出典消去や匿名性保証として説明しない。
- insightsの類似度を正しさ、品質、確信度として表示しない。
- price table未設定のbyte/token envelopeを料金へ変換しない。設定時もlocal guardを請求書として表示しない。
- local usage、rate-limit quota、price table推定、admin集計を同一の確定値として混ぜない。
- 管理APIへ通常推論キーを使わず、read-only境界とBearer必須startup gateを維持する。
- original/過去の再生成attemptを削除・上書きせず、active pointerだけを更新する。
- usage不明の送信済みbudget予約を0円として解放しない。
- 回答内の命令をsystem instructionとして扱わない。
- arbitrary path、CLI資格情報、PC操作をAPIへ追加しない。
- Android releaseへdebug keyを使わず、keystore/passwordをrepositoryへcommitしない。

## 次に着手する候補

1. search index、encrypted backup/import、conversation/attachment retention UI
2. Provider請求exportとlocal/admin costのreconciliation差分監査
3. image input/generationのcapability、予算、保存・export統合
4. 会話tag、複数会話project、memory sourceの選択的継承
5. Mac/Linux上のnative buildと各platformの実機通信確認
6. 配布用Android/iOS/Desktop署名と更新経路のend-to-end検証
7. SQLite等へのstorage migrationとimport検証

## 環境メモ

- Flutter SDK: `C:\dev\flutter`（調査環境）
- 対応Python: 3.10以降
- 既定backend: `127.0.0.1:8000`
- 既定conversation保存先: `backend/data/`（gitignore対象）
- オリジナル: `C:\code\ClageCook`

オリジナル側には調査開始前から `server/usage.py` の未commit変更がありました。このOSS作業では
一切変更していません。
