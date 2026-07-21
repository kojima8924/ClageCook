# Clage Cook — 開発引き継ぎ

最終更新: 2026-07-21 / バージョン `0.2.0` + Unreleased

## 現在地

公開版の既定architectureを、PC backend前提から **Direct BYOK + 端末内会話保存** へ変更しました。
Flutter appから4社の公式HTTPS APIへ直接接続でき、LAN / Tailscale / 常駐serverは不要です。

開発中のUI照合と高度なserver機能の検証用に、従来のOSS FastAPIへ接続するreference server modeを
toggleで維持しています。Directとreferenceはsecret、履歴の正本、復旧・課金保証が異なります。

現在のreference clientは本repositoryのFastAPI契約向けです。非公開のオリジナル版が使う
サブスクリプションserver固有APIへ完全対応するadapterは未実装です。

## Flutter / Direct BYOK

### 主なsource

- `lib/main.dart`: productionではDirect設定storeとDirect会話storeを注入
- `lib/services/direct_settings_store.dart`: execution mode、reasoning、4社key、model override、統合役
- `lib/services/local_conversation_store.dart`: immutableな端末内conversation repository
- `lib/services/direct_provider_client.dart`: 4社公式APIのrequest/response adapter
- `lib/services/direct_byok_client.dart`: 既存 `ApiClient` 契約へ合わせた端末内orchestrator
- `lib/screens/app_settings_screen.dart`: Direct / reference切替、折りたたみProvider key/model、reasoning/統合役
- `lib/screens/home_screen.dart`: 共通会話UI、2段composer、run snapshot、prompt template、会議実行
- `lib/widgets/turn_view.dart`: 1行回答header、本文 / 批評前回答 / immutable attempt accordion
- `lib/services/api_client.dart`: reference server REST/SSE client
- `lib/services/settings_store.dart`: reference URL + Bearerのorigin-bound保存

### 起動とmode選択

`DirectSettings` の既定は `executionMode=directByok`、`reasoningMode=auto`、
`showTokenUsageLedger=true` です。`HomeScreen._bootstrap()` は
Direct設定を先に読み、Directなら `DirectByokClient`、referenceなら通常 `ApiClient` を生成します。
初期Directにkeyがなければhealth自体は成功しますが、active workerが0件なので設定画面へ誘導します。

mode切替中に会議を実行している場合は拒否します。新modeのbootstrapが失敗した場合、旧clientへ黙って
戻らず接続を破棄するfail-closed動作です。

Directとreferenceのhistoryは自動mergeしません。Directは
`LocalConversationNamespace.directByok`、referenceはserver側 `CLAGE_DATA_DIR` が正本です。

### Direct settingsとAPIキー

`DirectSettingsStore` は公開recordをSharedPreferences、API key recordを `flutter_secure_storage` へ保存します。
台帳の表示設定 `show_token_usage_ledger` と通常の実API確認設定 `show_live_api_confirmation` は公開recordに入り、
API key recordには入りません。後者のdialog内変更は公開recordだけを部分更新し、APIキーを再保存しません。
secretを先にwriteし、同revisionのpublic recordをcommit pointにします。revision不一致、secure read失敗、
破損recordでは公開設定だけを返し、keyを空にします。secure storage失敗時の平文fallbackはありません。

設定UIは保存済みkeyをTextFieldへ読み戻しません。空欄は既存維持、入力したProviderだけ更新、個別削除予定と
全社一括削除を提供します。Directを保存するには1社以上のkeyが必要です。WebではDirectの保存を拒否します。

Native releaseはDirect専用です。Reference toggleはnative debug/profileとWebだけで表示します。Android releaseは
cleartextを拒否し、app data全体をcloud backup/device transferから除外します。iOS/macOSはKeychain entitlement、
Windowsは固定storage prefixと非昇格manifest、Linux releaseはcompiler/linker hardening、WebはCSP、
Trusted Types、`no-referrer` を設定しています。

### Direct Provider adapter

Endpointは次の4つです。

- Claude: `https://api.anthropic.com/v1/messages`
- ChatGPT: `https://api.openai.com/v1/responses`
- Gemini: `https://generativelanguage.googleapis.com/v1/interactions`
- Grok: `https://api.x.ai/v1/responses`

OpenAI/Gemini/xAIには `store=false` を渡します。API error本文は公開せず、HTTP statusと
DNS / TLS / connection refused / reset / network unreachable / client closed / timeoutなどの固定codeだけを返します。
HTTP timeoutはProvider・tier・実効effort・Web検索別の2〜15分、attemptは常に1回で、自動retryしません。
responseは共通answer shapeへ正規化し、text、model、elapsed、usage、completion、partial、reasoning、citations、
request auditを保存します。

partial/incompleteは `ok=false` です。本文は表示・保存しますが、DEBATE参加者と統合材料へ含めません。
自動継続は実装していません。

### tier、推論エフォート、上限

composerの「モデル・出力枠」は `LOW / BALANCED / HIGH` をそれぞれ
`tier=low|balanced|high` として送ります。その下の「推論エフォート」は `設定値 / LOW / MEDIUM / HIGH` です。
設定値は `DirectSettings.reasoningMode` の `auto|low|medium|high` を使い、他の3つはそのターンだけ上書きします。

AUTOは設定画面だけにあり、質問を分類せずProvider/model prefixの固定policyから
medium/high/provider-defaultを解決します。明示LOW/MEDIUM/HIGHも対応modelだけへ同名effortを送り、
未知・非対応modelではreasoning fieldを省略します。tierとeffortは互いを変更しません。

### モバイルUIとrun snapshot

composerは高さを固定した2本の横scroll stripです。1段目はLOW / BALANCED / HIGHとDEBATE、2段目は
既定 / LOW / MEDIUM / HIGHのeffort、WEB ON / OFF、統合、BLIND、active Providerを表示します。
狭いAndroid幅でもoptionを非表示にせず横scrollで到達させます。AUTOは設定画面だけにあります。

`_send()` はpreflightより前にmessage、conversation ID、tier、reasoning、DEBATE、統合、BLIND、Web、
Provider順、添付IDをlocal snapshotへ取り、`LiveTurn` に同じ値を保持します。plan / policy scan / 確認 /
`startChat()`受理までのpreparing中はdraftとoptionをlockします。受理後は元のdraftが未変更の場合だけ消去し、
生成中の入力とoption編集を次回用として許可します。現在runのsnapshotは変えません。

送信buttonはrun active中に同位置の停止buttonへ置換し、controller側のactive run guardと合わせて二重送信を
防ぎます。添付の追加・削除は生成中もlockします。次回draftはqueueではなく、現在run完了後の手動送信用です。

Provider回答は外側のaccordionを閉じると、Provider / model / 実効effort / elapsed / DEBATE / WEB、status、copy、
再生成を1行で確認できます。本文内にはDEBATE前の最初の回答とProvider別immutable attempt監査履歴を別々の
accordionで置き、本文を持つ各attemptと最初の回答にもcopy操作を提供します。failure、partial、DEBATE errorは初期状態で開きます。
Provider設定もstatusとmodel要約をheaderへ残す折りたたみcardです。

Provider実測トークン利用量台帳もターンごとのaccordionで、既定は閉じています。
`DirectSettings.showTokenUsageLedger=false` は台帳の描画だけを止め、usage収集、会話record、JSON/ZIP export、
利用状況画面には影響しません。回答比較insightsは台帳を非表示にしても残します。

2段の密度、同位置の停止、批評前回答accordionはオリジナル版の操作感を照合して反映しました。一方、
run snapshot、partial / request audit、immutable attempt監査はこのrepositoryのDirect / reference契約です。
Android DirectはProvider POST前に `dataSync` foreground serviceの開始ackを待ち、20秒heartbeatでUIのidle期限を
延長します。開始失敗は送信前に中止し、全終了経路でserviceを解放します。利用者・OSによるservice停止、
process再起動後の復旧、Android以外のbackground継続、次回draftの自動queue、生成中の添付編集は未実装です。

Direct既定model:

| Provider | low | balanced | high |
| --- | --- | --- | --- |
| Claude | `claude-haiku-4-5-20251001` | `claude-sonnet-5` | `claude-opus-4-8` |
| ChatGPT | `gpt-5.6-luna` | `gpt-5.6-terra` | `gpt-5.6-sol` |
| Gemini | `gemini-3.1-flash-lite` | `gemini-3.5-flash` | `gemini-3.5-flash` |
| Grok | `grok-4.3` | `grok-4.3` | `grok-4.5` |

Directの1 call出力上限:

| Provider | low | balanced | high |
| --- | ---: | ---: | ---: |
| Claude | 4,096 | 8,192 | 16,384 |
| ChatGPT | 4,096 | 8,192 | 16,384 |
| Gemini | 8,192 | 16,384 | 32,768 |
| Grok | 4,096 | 8,192 | 16,384 |

run全体のoutput envelopeは196,608です。planはanswer、DEBATE、synthesisのcall/tokenを加算し、0 retryを
明示します。Direct input envelopeは初回promptのmessage、添付snapshot、memory、履歴、worker systemを
UTF-8 byteで積算し、1 call 1 MiBを超える送信を遮断します。生成回答に依存するDEBATE/統合入力は未加算と
明示します。金額見積、price table、daily budgetはありません。

### Direct会議flow

1. Homeが質問と全optionをsnapshotし、`planChat()` と `scanPolicy()` を行う。
2. billable確認後だけ `confirmLiveApi=true` で `startChat()` を呼ぶ。
3. Direct clientはmessage、選択添付snapshotを再検査し、key/secret候補をblockする。
4. 設定済み・選択済みProviderを並列callし、完了順で`answer` eventを出す。
5. DEBATE時は完了回答者が互いを検証する。BLINDではpeer名だけalias化する。
6. 統合は完了回答だけを材料にし、設定済み統合Providerを1回callする。
7. turnを端末storeへcommitし、`done` eventを出す。

worker/debate/synthesis system promptへ「結論優先」「簡潔」を追加していません。AUTOもprompt rewriteを行いません。
Webは初回answer callだけにtoolを追加し、DEBATE/synthesisでは再検索しません。

Direct run stateはprocess memoryだけです。Androidではforeground serviceと固定通知でbackground中の実行を
保護しますが、app kill後のdurable再接続とevent journalはありません。iOS / Desktopのbackground継続も
保証しません。stopはprovider clientをcloseしますが、Provider側処理/課金停止を保証しません。

### Direct conversation store

`SharedPreferencesLocalConversationRepository` は1 namespaceにつき1 manifestと、conversation revisionごとの
immutable recordを使います。recordを書いてからmanifestをcommitし、古い/孤立recordは `compact()` で除去します。
同じisolate/namespace内のoperationはmutexで直列化し、expected storage/memory revisionの競合を拒否します。

実装済み:

- create/read/save/list/search/rename/delete
- memory revision更新
- 対象turn直前までをcopyするimmutable fork
- JSON export
- 1会話16 MiB、memory 20,000文字の上限
- title/memory/question/answer/synthesisの端末内全文検索

会話JSONはapplication-level暗号化をしていません。Direct ZIPは `conversation.json` と `README.txt` を含み、
API key、添付bytes、Markdown版は含みません。

### Direct添付

現在は1件512 KiB以下、1会話8件以下、合計512 KiB以下のUTF-8
`txt` / `md` / `markdown` / `csv` / `json`だけです。NULを含む名前・本文は拒否します。添付snapshotをrun開始時に
固定し、messageと合わせてpolicy再scanします。添付text/bytesはprocess memoryだけで、conversation recordへ
保存しません。app再起動後の再利用、download、ZIP同梱は不可です。

共通file pickerはPDF/画像も表示するため、Directではupload時に明示errorになります。Reference serverだけが
PDF抽出、画像原本保存、TTL、download、元添付入りZIPを提供します。

### Direct再生成・分岐・usage

完了answerまたはsynthesisを1 callで再生成し、originalと新結果をimmutable `attempts` へ記録し、
`active_attempts` pointerだけを更新します。answer再生成後はsynthesisをstale、synthesis再生成後は解除します。
turn編集は親を破壊せずbranchを作ります。

Direct usage画面は残高/請求を取得しません。Provider responseのusageだけをanswer/turnへ保存し、各社consoleを
残高・請求の正とします。offline lexical insightsはDirect pathでは現在空です。

## Reference server

Reference serverはDirectの必須componentではありません。以下は開発・SAFE MOCK・高度なserver機能のために
維持します。

### Backend module map

- `main.py`: FastAPI routes、認証、rate/concurrency、run lifecycle
- `config.py`: dotenv、model/reasoning解決、Provider別上限、公開settings
- `planning.py`: call/output/input/retry/cost/budget plan
- `orchestrator.py`: 並列回答、DEBATE、BLIND、統合
- `providers/`: 4社API、mock、usage/completion/rate-limit正規化
- `storage.py`: 1会話1JSON、atomic replace、会話lock
- `runs.py`: background run、SSE replay、cancel、retention
- `regeneration.py`: immutable regeneration state
- `finance.py`: price snapshot、budget reservation/settle/reconciliation
- `attachments.py`: owner固定upload、MIME/signature/TTL、PDF subprocess
- `telemetry.py` / `admin_telemetry.py`: local usageとopt-in管理集計
- `scrubbing.py` / `policy.py`: 公開pathの再帰scrub、送信前policy

`CLAGE_LIVE_API_ENABLED=false` は4 mockです。live=trueではBearer必須、設定済み実Providerだけが参加し、
未設定を暗黙mockへしません。`INCLUDE_MOCK_PROVIDERS=true` だけが明示混在です。

Backendも `reasoning_mode=auto|low|medium|high` を受理します。AUTO policyは質問を分類しません。
BackendのLOW/BALANCED/HIGH上限はClaude/ChatGPT/Grokが4,096/8,192/16,384、
Geminiが8,192/16,384/32,768、run上限196,608です。Flutterも3つのtierをすべて公開します。

Reference固有の主な保証:

- external call前のdurable pending claim
- answer/synthesis checkpointと終端event journal
- same request ID/fingerprintのjoin/replay、SSE event ID再接続
- startup時のorphan run/attemptをinterrupted確定し、自動再実行しない
- conversation claim、single-process data-dir lock、`--workers 1`
- optional price table、per-run/daily budget reservation、manual reconciliation
- optional read-only admin telemetryとlocal usageの分離
- owner固定添付、PDF抽出、元添付入りZIP

ReferenceのHTTP retry既定値は0ですがenvで変更可能です。partial後の自動継続はありません。
Web tool料金はtoken price tableで完全見積できず、budget時はunknown-cost policyを通します。

## Reference REST / SSE契約

Directは同じDart methodを端末adapterで実装するため、以下のHTTP routeを呼びません。

### REST

- `GET /api/health`, `GET /api/settings`, `PATCH /api/settings/runtime`
- `GET /api/telemetry`, `POST /api/plan`, `POST /api/policy/scan`
- `POST /api/chat`, `POST /api/runs/{request_id}/cancel`
- conversation list/search/get/rename/delete/export/memory
- attachment list/upload/get/delete
- regeneration plan/run、turn fork
- manual budget reconciliation

### SSE

- `meta`: run、Provider、model、tier、reasoning snapshot
- `answer`: 初回/DEBATE回答、partial、usage、request audit
- `phase`: debate / synthesis
- `insights`: deterministic lexical overlap
- `synthesis`: 統合またはskip/failure
- `error`: safe run-level error
- `done`: terminal summary

## 検証

実APIkeyを使うsmoke testは通常検証へ含めません。

```powershell
cd backend
python -m pytest -q -p no:cacheprovider
python -m compileall .

cd ..\app
dart format --output=none --set-exit-if-changed lib test
flutter analyze
flutter test
flutter build web --release
flutter build windows --release
flutter build apk --release
```

release handoffでは実際に実行したcommand、結果、未検証platform、Android署名状態を別途記録してください。
iOS/macOSはMac、LinuxはLinuxでnative buildが必要です。

Android signingはprivateな `app/android/key.properties`、または
`CLAGE_ANDROID_KEYSTORE` / `CLAGE_ANDROID_STORE_PASSWORD` / `CLAGE_ANDROID_KEY_ALIAS` /
`CLAGE_ANDROID_KEY_PASSWORD` の4環境変数をすべて使えます。部分設定と不完全propertiesはGradleが拒否します。
署名後はcertificate fingerprintを検証してください。

## 重要な不変条件

- Direct API keyをSharedPreferences、conversation、export、log、reference serverへ含めない。
- secure storage失敗時に平文fallbackしない。保存済みkeyをUIへ再表示しない。
- Direct/referenceのhistoryとsecretを暗黙mergeしない。
- billable会議・再生成の通常確認は既定ONとし、OFFは保存済み利用者設定からだけ適用する。通常確認OFFでも
  秘密候補をblockし、個人情報候補の追加確認を省略しない。
- AUTOで質問を分類・rewriteせず、回答の方向や簡潔さを誘導しない。
- partialを完了回答としてDEBATE・統合へ混ぜず、自動継続しない。
- DirectでHTTP retryを追加しない。Referenceのretry既定0を変更するときはplan envelopeも更新する。
- Provider error生body、API key、BearerをUI/JSONへ反射しない。
- cancelをProvider処理・課金の停止保証として表示しない。
- 生成中のcomposer変更を現在runへ混ぜず、開始時のmessage / option / Provider / 添付snapshotを維持する。
- original regeneration attemptを削除・上書きせず、active pointerだけを更新する。
- turn編集で親会話を破壊しない。
- WebでDirect API key保存を有効にしない。
- Reference live serverをBearerなしで起動させず、同じdata dirを複数processで書かない。
- Android releaseへdebug keyを使わず、keystore/passwordをcommitしない。
- Native releaseへreference toggleを戻さず、Androidのcleartext拒否とapp data backup除外を外さない。

## 次に着手する候補

1. 生成結果に依存するDEBATE/統合input envelopeのより明確なpreview
2. Direct添付の永続化、download、PDF抽出、画像input capability、ZIP統合
3. Direct会話の暗号化backup/import、retention、migration test
4. Direct app/process終了後のrun復旧と、Android以外のbackground継続
5. Direct local usage集計と任意price/budget guard
6. 旧subscription server専用adapter、またはreference対象の明確な整理
7. local storeのSQLite化と全文検索index
8. Mac/Linux/iOS/Android/Desktopの実機、署名、更新経路検証

## 環境メモ

- Flutter SDK: stable channel
- Python: 3.10以降
- Reference backend既定: `http://127.0.0.1:8000`
- Reference conversation: `backend/data/`（gitignore対象）
- Direct conversation: platform app storageのSharedPreferences namespace
- オリジナル版: 非公開、本repositoryには含めない
