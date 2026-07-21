# Clage Cook Flutter client

Android / iOS / Windows / macOS / Linux / Webを対象にしたFlutter UIです。既定の **Direct BYOK** では
端末からClaude、Gemini、ChatGPT、Grokの公式APIへ直接接続し、会話を端末内へ保存します。
開発・UI照合用の **reference server** へ切り替えることもできます。

## 起動

```powershell
flutter pub get
flutter run -d windows
```

Android実機では `flutter devices` でdevice IDを確認し、`flutter run -d <device-id>` を使います。
Direct BYOKならbackendの起動、LAN、Tailscaleは不要です。

Web版はAPIキーの安全な保持と各社CORSの制約を避けるためDirect BYOKを無効化しています。設定storeも
vendor APIキーを読み書きせず、旧secret recordは読み込まずに削除を試みます。`flutter run -d chrome` で
使う場合はreference serverを先に起動してください。

## 接続とBYOK設定

右上の設定から実行方式を切り替えます。

### Direct BYOK（既定）

- 使うProviderのAPIキーを1つ以上入力する
- 任意でProvider別model IDをoverrideする
- 統合役を自動、または設定済みProviderから選ぶ
- 既定の推論エフォートをAUTO / LOW / MEDIUM / HIGHから選ぶ

APIキーは `flutter_secure_storage`、実行方式・reasoning・model override・統合役は
SharedPreferencesへ分離保存します。秘密recordを先に書き、同じrevisionの公開recordをcommit pointにするため、
途中失敗やrevision不一致ではAPIキーを読み込まないfail-closed設計です。secure storageが使えなくても
平文storageへ自動降格しません。

保存済みAPIキーは設定画面の入力欄へ読み戻しません。空欄なら既存キーを維持し、入力したProviderだけを
更新します。Provider単位の削除予定と、4社すべてのキー削除を用意しています。キーは実行中のmemoryには
存在するため、root化・debugger・侵害済みOSから保護する仕組みではありません。

### Reference server（開発用）

server URLと任意のBearer tokenを保存し、本リポジトリのFastAPIへREST/SSEで接続します。
URLの公開recordとBearerの秘密recordはrevision・正規化originで結合し、別originへ旧tokenを送らないように
しています。userinfo、query、fragmentは拒否し、reverse proxy用pathだけを許可します。

この切替は開発中のUI比較を目的とします。現在のclientはClage Cook OSS FastAPI契約に対応しており、
旧Clage Cookのサブスクリプションserver固有APIを完全に変換するadapterではありません。

Reference toggleはnativeのdebug/profile buildと、Directを無効化しているWebでだけ表示します。Android / iOS /
Desktopのrelease buildはDirect BYOK専用です。

## LOW / BALANCED / HIGHと推論エフォート

composerは、model・最大出力枠のtierと推論エフォートを別々に選びます。

| モデル・出力枠 | tier |
| --- | --- |
| LOW | low |
| BALANCED | balanced |
| HIGH | high |

直下のエフォート行は `設定値 / LOW / MEDIUM / HIGH` です。`設定値` は設定画面で保存した
`AUTO / LOW / MEDIUM / HIGH` を使い、他の3つはそのターンだけ上書きします。wire contractは
`reasoning_mode=auto|low|medium|high` です。

AUTOは質問内容を分類したりpromptへ「結論優先」「簡潔」などを追加したりしません。Providerとmodel familyの
固定policyから推奨思考量を決め、実行前に固定します。未知・非対応modelではreasoning fieldを推測せず、
Provider既定値へ委ねます。

Directの1 call上限はClaude / ChatGPT / Grokが4,096 / 8,192 / 16,384、Geminiが
8,192 / 16,384 / 32,768（LOW / BALANCED / HIGH）です。会議全体は196,608 tokenまでです。
これは最大envelopeで、生成量や料金の保証ではありません。

## 送信前の安全flow

Directとreferenceのどちらも、planとpolicy scanを実行してから会議を開始します。

1. APIキー・秘密鍵らしいblock候補があれば送信しない。
2. Provider、model、最大call、最大output tokenをplanで確認する。
3. billable runは既定で確認dialogを表示する。利用者は「実行して次回から表示しない」または設定画面で、
   通常の課金可能性の確認だけをOFFにできる。
4. メールアドレス・電話番号らしい文字列は、通常確認がOFFでも追加確認を求める。
5. 確認後にだけ各社APIまたはreference serverへ実行を依頼する。

送信本文はpreflightと実行開始を受理するまで消しません。policy scanは決定論的patternであり、秘密や
個人情報の完全検出を保証しません。

Directは同じ生成HTTP要求を自動retryせず、partial/incomplete後の自動継続もしません。途中本文は警告付きで
保存しますが、完了回答としてDEBATE・統合へ混ぜません。Reference serverも既定retryは0で、自動継続は
行いません。

Directの待機上限はProvider・tier・effort・Web検索に応じた有限の2〜15分です。Androidでは有料POST前に
`dataSync` foreground serviceの開始完了を待ち、実行中通知を出します。Direct streamは20秒ごとにactivityを
通知するため、HIGHの正常な長時間待機をUIの無通信判定で誤って切断しません。

## 主なUI

- responsiveな会話一覧、新規会話、タイトル変更、削除
- タイトル・メモ・質問・回答・統合を対象にした全文検索
- 350 ms debounce、古い検索responseの破棄、error/retry表示
- 会話JSONのclipboard exportとZIP保存
- revision付き会話メモと、親を壊さないturn編集分岐
- Claude / Gemini / ChatGPT / Grokの完了順answer card。1行headerから本文を開き、現在回答をコピー可能
- selectable Markdown、引用URL、partial/request audit表示
- 既定で閉じた実測usage台帳と、usage保存を止めずに台帳だけを非表示にする設定
- 2段の横スクロールstripへ圧縮したLOW / BALANCED / HIGH、DEBATE、独立エフォート、WEB ON / OFF、
  統合、BLIND、参加AIの操作
- DEBATE前の最初の回答と、回答・統合のimmutable再生成履歴、active attempt、stale統合警告
- キー状態とmodel要約を閉じたheaderで確認できる、Provider別設定accordion
- `Ctrl/Cmd+K` の検索focus、`Ctrl/Cmd+N` の新規会話shortcut

### モバイルcomposerとrun snapshot

1段目は品質とDEBATE、2段目はエフォート、Web、統合、BLIND、参加Providerです。どちらも横scrollで、
狭いAndroid画面でも項目を削りません。AUTOは設定画面にだけ置き、composerの「既定」は保存済みの
AUTO / LOW / MEDIUM / HIGHを参照します。

送信開始時に質問、全option、参加Provider、添付IDを現在runのsnapshotへ固定します。実行が受理されると入力欄を
次回用へ戻し、生成中も下書きと次回のoptionを編集できます。同じ位置の送信buttonは停止buttonへ替わるため、
現在runへ重ねて送信しません。plan / 確認 / 開始受理までの短い準備中は入力とoptionをlockし、生成中は添付の
追加・削除だけを禁止します。次回用下書きは自動実行queueではありません。

回答cardはProvider、model、実効effort、経過時間、DEBATE / WEB、完了・部分回答・失敗を1行headerへ集約します。
headerから本文を開き、DEBATE前の「最初の回答（批評前）」とimmutable attemptの監査履歴を内側のaccordionで
個別に確認できます。現在回答、最初の回答、本文を持つ各attemptにはそれぞれcopy操作があります。partial / failure cardは警告を
見落としにくくするため初期状態で開きます。

この密度、同位置の停止操作、批評前回答accordionはオリジナル版とのUI照合を反映しています。run snapshot、
partial / request audit、immutable attempt履歴はClage CookのDirect / reference契約に合わせた追加情報です。
ただし、次回用下書きの自動queue、生成中の添付編集、process再起動後の復旧は未実装です。Android Directは
foreground serviceでbackground実行を保護しますが、利用者・OSによる停止やprocess終了後の復旧を保証しません。
Android以外のDirect runはbackground継続を保証しません。

トークン利用量台帳は各ターンで閉じた状態から始まります。設定画面の「トークン利用量台帳を表示」をOFFにすると
台帳だけを描画しません。Provider usageの取得、端末内会話への保存、JSON/ZIP exportはそのまま継続します。

BLINDはDEBATE・統合promptへ渡すProvider名だけを回答A、回答Bのようなaliasへ変えます。利用者向けcard、
保存JSON、network接続先を匿名化しません。AI回答、DEBATE、統合も正しさを保証しません。

## Directの端末内保存

会話はSharedPreferencesのmode専用namespaceに、immutable recordとmanifest commit pointで保存します。
同じisolate・namespace内の操作を直列化し、revision競合を検出します。保存対象は会話、turn、回答、統合、
再生成attempt、メモ、分岐metadataです。

- 1会話: 16 MiBまで
- 会話メモ: 20,000文字まで
- 検索結果: 最大100件（UIは50件を要求）
- JSON export: 端末内recordの整形JSON
- ZIP export: `conversation.json` と `README.txt`。APIキー・添付bytes・Markdownは含めない

会話本文は暗号化していません。app sandbox、端末の画面lock/full-disk encryption、backup policyへ依存します。
Direct履歴とreference server履歴は別の正本で、mode切替時に自動mergeしません。暗号化backup/import、
cross-device同期、retention UIは未実装です。

Androidはapp data全体をcloud backupとdevice transferから除外します。iOS/macOSはKeychain entitlementを明示し、
Windowsはsecure storage namespaceを固定しています。ただし、各platformのOS accountやkey store自体が侵害された
場合まで保護するものではありません。

## 添付

file pickerは共通UIのためPDF・画像も候補に表示しますが、Direct BYOKが現在受理するのは次だけです。

- 1件512 KiB以下、1会話8件以下、合計512 KiB以下
- UTF-8 text
- `txt` / `md` / `markdown` / `csv` / `json`
- file名と本文にNULを含まない

Directの添付bytes/textは実行中のmemoryだけにあり、会話storeへ保存しません。app再起動後の再利用、download、
ZIP同梱はできません。Reference serverでは別実装によりtext/PDF抽出、画像の原本保存、TTL、download、
元添付入りZIPを提供しますが、画像をmodel入力には使いません。

## Web検索

Web検索は既定OFFで、`検索あり` を明示したターンの初回回答だけへProviderのserver toolを追加します。DEBATE・統合へは
自動適用しません。Claudeへは最大3回、xAIへは最大3 turnを指定しますが、すべてのProviderで厳密な総検索回数を
保証するものではありません。tool料金・対応model・引用shapeは各社仕様に依存します。

DirectにはWeb tool料金を含むlocal金額guardがありません。Reference serverのprice tableもtoken単価だけなので、
tool料金はunknown-cost policyの対象です。

## 実行、停止、再接続

Directはアプリprocess内で4社callを管理します。停止時はHTTP clientをcloseしますが、すでに届いたrequestの
Provider側処理・課金停止を保証しません。アプリ終了、process kill、network切断後のdurable再接続は未実装です。
会話turnは処理終了時に端末storeへcommitします。

Reference serverは `conversation_id + request_id`、event journal、pending claimを使い、SSE切断後の同一run再接続、
app再読込後の復帰・停止、server再起動時のinterrupted確定を提供します。これらをDirectの保証として扱わないで
ください。

## 利用量

DirectはProvider responseに含まれた実測tokenだけを各answerへ保存します。credit残高、請求額、spend limit、
組織usageは取得せず、各社consoleで確認します。Reference serverだけがlocal usage集計、利用者設定price table、
budget予約、任意のread-only admin telemetryを提供します。

## 検証

```powershell
dart format --output=none --set-exit-if-changed lib test
flutter analyze
flutter test
flutter build web --release
flutter build windows --release
flutter build apk --release
```

通常testはHTTP mockを使い、real API keyや課金APIを呼びません。iOS / macOS / Linuxは各OSでnative buildと
実機通信を確認してください。

## Android release署名

release buildは共通debug keyへfallbackしません。配布用APK/AABでは
`android/key.properties.example` をprivateな `android/key.properties` へcopyし、配布者自身のkeystore、alias、
passwordを設定してください。代わりに `CLAGE_ANDROID_KEYSTORE`、`CLAGE_ANDROID_STORE_PASSWORD`、
`CLAGE_ANDROID_KEY_ALIAS`、`CLAGE_ANDROID_KEY_PASSWORD` の4環境変数をすべて設定できます。部分設定や不完全な
propertiesはbuildを停止します。`key.properties`、`*.jks`、`*.keystore` はgitignore対象です。

propertiesがないrelease outputを配布用署名済みと見なさず、公開前に意図したcertificateを確認してください。
keystoreとpasswordをrepository、Issue、chat、build logへ含めないでください。

root [README](../README.md) と [SECURITY](../SECURITY.md) も参照してください。
