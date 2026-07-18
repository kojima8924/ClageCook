# Clage Cook Flutter client

Clage Cook OSS backendへREST/SSEで接続する、Web / Windows / macOS / Linux / Android / iOS共通の
Flutter clientです。vendor API keyは扱いません。保存する秘密情報は、任意のClage Cook用Bearer
tokenだけです。

## 起動

先にroot [README](../README.md) の手順でbackendを `--workers 1` で起動します。

```powershell
C:\dev\flutter\bin\flutter.bat pub get
C:\dev\flutter\bin\flutter.bat run -d chrome
```

右上の設定画面で次を指定します。

- server URL（PC上の既定値は `http://127.0.0.1:8000`）
- `CLAGE_AUTH_TOKEN` を設定した場合のBearer token

URLの公開recordはSharedPreferences、Bearerを含む秘密recordは `flutter_secure_storage` に保存します。
両recordはrevision・正規化origin・base URLがすべて一致した場合だけ結合されます。秘密recordを先に、
公開recordをcommit pointとして書くため、途中失敗や接続先変更で旧tokenを別serverへ送りません。
安全なstorageを利用できないHTTP originではtokenをplain storageへ自動降格しません。Web版はlocalhostまたは
HTTPSで開いてください。会議のpreflight・実行・切断復旧中は接続設定を変更できません。
server URLはreverse proxy用pathを許可しますが、userinfo、query、fragmentは拒否します。backendが
Bearer認証必須と返した場合は必須表示になり、空tokenを保存できません。

設定画面はbackendの公開settingsだけを読み、keyの値を取得しません。backend側で
`CLAGE_LIVE_API_ENABLED=false` の場合は、keyの有無に関係なく `SAFE MOCK` とlock iconを表示します。
Flutterの設定だけでlive APIを有効化することはできません。backendはlive gateがtrueなのに
`CLAGE_AUTH_TOKEN` が空なら起動を拒否するため、live利用時は同じBearer tokenをFlutterへ保存してください。

## 送信前の安全flow

送信時は `/api/plan` と `/api/policy/scan` を並列に実行してから `/api/chat` を開始します。

1. secret候補がある場合は送信せず、検出分類とmask済みtextを表示する。
2. call、output token、input UTF-8 byte、retryの上限超過があれば開始しない。
3. billable planの場合だけ、Provider/modelと最大実行量をdialogで表示する。
4. 利用者が承認したrunへ `confirm_live_api=true` を送る。
5. email address・電話番号候補があるlive runは `confirm_sensitive_data=true` も送る。

表示するinputはUTF-8 byteの安全側envelope、outputは設定上の最大tokenです。backendへ正確なprice tableが
設定されている場合だけ、確認dialogへ安全側の最大金額、価格版、会議・日次残額を表示します。未登録単価を
推測しません。token台帳はProvider responseの実測usageだけを表示し、欠損値を0へ置換しません。

## 主なUI

- responsiveなconversation一覧、title変更、delete、新規conversation
- backendのtitle・question・answer・synthesisを対象にした全文検索
- 350ms debounce、古いsearch responseの破棄、error表示とretry
- backend契約と同じ200文字の検索入力上限
- conversation JSONのclipboard exportと、JSON/Markdown/元添付を含むZIP保存
- 会話ごとのrevision付きローカルメモと、親を壊さないturn編集分岐
- opaque添付の選択・upload・削除・prompt取込状態表示
- 保存済みturnと実行中turnの分離
- Claude / Gemini / ChatGPT / Grokの完了順answer card
- 選択可能Markdownのsynthesis
- tier、DEBATE、BLIND、WEB、synthesis、参加AIの操作
- 4社Web検索の構造化URL引用と、外部browserで開く出典chip
- worker/統合modelを変更するrevision付きruntime設定UI
- 回答間のlocal語彙比較、共有語、固有語、注意表現
- Provider、model、phase別の実測token ledger
- 保存済み全attemptのlocal usage、rate-limit header観測、local budgetを分離した利用状況画面
- 任意の読み取り専用組織管理telemetryと、Provider別の部分失敗・取得時刻・cache状態
- 外部請求を確認済みのreconciliation pending予約だけを手動解放する確認UI
- 回答・統合の再生成、immutable revision履歴、active attempt、stale統合の警告
- partial/incomplete、HTTP複数試行、usage不完全、cancel/failure/interruptedの警告表示
- SSE切断後の同一run・event ID再接続
- app再読込後に保存済みrunning turnから行う同一run再接続または停止
- 実行中会議へのcancel request
- SAFE MOCK / live / mixedとProvider設定状態の表示
- `Ctrl/Cmd+K` のsearch focus、`Ctrl/Cmd+N` の新規conversation shortcut

BLINDはDEBATE・統合へ渡すAI名を回答A、回答Bのようなaliasへ置換します。利用者が出典を監査できる
answer cardや保存JSONからProvider名を消す機能ではありません。語彙insightsも正しさ、品質、modelの
確信度を評価するものではありません。

## 再接続と停止

実行状態は `conversation_id + request_id` で追跡します。SSE切断後は同じpayload、confirmation、run ID、
最後のevent IDで再接続し、backendに保存されたevent journalを続きから受け取ります。切断中は別runを
開始できず、再接続または停止を選びます。`done` 後にconversation取得だけが失敗した場合はSSEを再開せず、
保存結果だけを再読込します。送信textはpreflightとserverのHTTP responseを受理するまでclearしません。

Flutter自体を再読込した場合も、backendが保持するrunning turnに「実行へ再接続」「停止を要求」を表示します。
再接続はpending claimに保存した元の生request条件・確認flagと同じ `request_id` を使い、新しいrunとして
実行しません。backendを再起動した場合、前processのrunning turnは起動時に `interrupted` へ確定されます。

全文検索は検索語をURLへ出さない `POST /api/search` を使います。同じconversationへ別runが進行中なら
backendの `conversation_busy` 409を表示し、先行run完了後に再試行します。

停止buttonはbackendのlocal taskへcancelを要求します。すでに送信済みのrequestについて、外部Providerの
処理停止や課金停止を保証しません。停止・failureまでに取得できたanswerとusageは不完全なturnとして保存・
表示される場合があります。

## 検証

```powershell
C:\dev\flutter\bin\dart.bat format --output=none --set-exit-if-changed lib test
C:\dev\flutter\bin\flutter.bat analyze
C:\dev\flutter\bin\flutter.bat test
C:\dev\flutter\bin\flutter.bat build web --release
C:\dev\flutter\bin\flutter.bat build windows --release
C:\dev\flutter\bin\flutter.bat build apk --release
```

test件数は追加で変わるため固定しません。release時は実行結果を記録してください。iOS / macOS / Linuxは
対応scaffoldとnetwork permissionを含みますが、それぞれのOSでnative buildと実機通信を最終確認する必要が
あります。通常testはreal vendor API keyや課金APIを使いません。

launcher iconを再生成する場合は、root `icon/icon_color.png` を編集してから次を実行します。

```powershell
C:\dev\flutter\bin\dart.bat run flutter_launcher_icons
```

## Android release署名

release buildはFlutterの共通debug keyで署名しません。配布用APK/AABを作る場合は
`android/key.properties.example` を `android/key.properties` へcopyし、自分のrelease/upload keystoreの
path、alias、passwordへ置き換えてください。real `key.properties`、`*.jks`、`*.keystore` はgitignore対象です。

`android/key.properties` がないrelease outputは配布用署名済みとは扱えません。公開前に自分のkeyで署名し、
意図したcertificateで署名されていることを確認してください。keystoreとpasswordをrepository、Issue、chat、
build logへ含めないでください。
