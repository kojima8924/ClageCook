# セキュリティポリシー

Clage Cookの既定は、端末から各社APIへ直接接続するDirect BYOKです。開発用に、利用者が管理する
reference serverへ接続するmodeもあります。両modeはnetwork経路、秘密の置き場所、保存先、安全保証が
異なるため、この文書では分けて扱います。

## サポート対象

開発初期のため、security修正は原則として最新revision / releaseだけに提供します。API、保存schema、
安全機構の後方互換性はまだ保証しません。問題を報告する前に、秘密値を使わない最小環境で最新版でも
発生することを確認してください。

## Direct BYOKの脅威モデル

Direct BYOKは個人が所有・管理するAndroid、iOS、Windows、macOS、Linux端末を想定します。
APIキーと会話へアクセスできる同じOS account、root/jailbreak、debugger、malware、悪性keyboard、
画面共有、端末backup、物理的に解錠された端末からの保護は保証しません。

Flutter appはAnthropic、OpenAI、Google、xAIの公式HTTPS endpointへ直接接続します。Clage Cookのserver、
LAN、Tailscaleを経由しません。TLSの検証、Provider側の保存・logging・abuse monitoring・retentionは
OS HTTP stackと各社policyへ依存します。OpenAI、Gemini、xAI requestでは `store=false` を送りますが、
それだけで全telemetryや法定保持が無くなることを保証しません。

Directに現在ないものは次のとおりです。

- app終了後も継続するdurable runとevent journal
- process kill / network切断後の同一run再接続
- Provider請求と連動したlocal price table、会議・日次budget、管理telemetry
- 会話本文のapplication-level暗号化、暗号化backup/import、remote wipe
- 複数端末の同期、tenant分離、組織管理policy

Directを共有端末、管理されていない端末、高機密のmulti-tenant用途へ使わないでください。

## APIキー

### Direct BYOK

- APIキーは設定画面から入力し、`flutter_secure_storage` へ保存します。
- 実行方式、reasoning、model override、統合役、台帳の表示設定はSharedPreferencesへ別recordとして保存します。
- 秘密recordを先に書き、同じrevisionの公開recordだけをcommit pointとします。revision不一致、破損、
  secure storage read失敗ではキーを空として扱います。
- secure storageが使えない場合にAPIキーをSharedPreferencesへ平文fallbackしません。
- 保存済みキーをTextFieldへ読み戻しません。設定済み状態、更新入力、削除操作だけを表示します。
- APIキーを会話record、JSON/ZIP export、Provider prompt、reference server、source、Gitへ書きません。
- Direct ZIPは `conversation.json` と `README.txt` だけを含み、APIキーとmemory-only添付bytesを除外します。

`flutter_secure_storage` の実装と強度はplatform、OS version、device policyに依存します。保存後もAPIキーは
Provider requestを作るためapp memoryへ復元されます。hardware-backed key isolationや、侵害済みOSからの
完全な秘匿を保証しません。

iOS/macOSはKeychain entitlement、Windowsは固定secure-storage namespaceと非昇格実行を明示しています。
Linux release launcherにはstack protector、FORTIFY、RELRO/NOW、non-executable stackを設定しています。
これらは多層防御であり、OS accountやsecret service自体の侵害を防ぐ境界ではありません。

Web版ではブラウザからのkey抽出riskと各社CORS制約を避けるため、Direct BYOKの保存・有効化をUIで拒否します。
Webでvendor APIキーをlocalStorage、JavaScript source、build argumentへ埋め込まないでください。

### Reference server

Vendor APIキーはprivateな `backend/.env` またはbackend process environmentだけへ置きます。Flutterへは
server接続用Bearer tokenだけを保存します。組織telemetryでは推論キーを流用せず、別のread-only管理資格情報を
使います。

server URLの公開recordとBearerの秘密recordはrevisionと正規化originで結合します。別originへ旧tokenを
送らず、secure write失敗時に平文fallbackしません。Web上のBearer保存はbrowser storage / WebCryptoに依存し、
XSS、悪性extension、共有profileからの完全保護を保証しません。

### 漏えい時

キーまたはtokenが漏えいした可能性があれば、該当Providerで直ちに無効化し、必要なら新しいkeyを発行して
ください。Git履歴に入った秘密はfile削除だけでは除去されません。real key、Bearer、conversation、keystoreを
Issue、chat、test fixture、screen shot、build logへ含めないでください。

## 課金APIの確認

Direct BYOKはAPIキーが1社以上設定されていると実APIを使用します。会議・再生成の直前にplanを作り、通常の
課金可能性の確認を既定で表示します。利用者はdialogの「実行して次回から表示しない」または設定画面で、この
通常確認だけをOFFにできます。設定変更は公開recordだけへ保存し、APIキーを再保存しません。APIキー・秘密鍵
候補のblockと、個人情報候補の追加確認はOFFにできません。SAFE MOCKはDirectにはありません。

planはProvider、model、最大call、最大output tokenを表示しますが、実際のtoken、入力token、Web tool料金、
最終請求額を保証しません。Directは金額上限を実装していないため、Provider側spend cap、期限付きkey、
各社consoleのusage alertを併用してください。

Directの生成HTTPは自動retryを行いません。partial/incomplete後の自動継続も行いません。これは二重課金や
意見変化を減らしますが、network error時にProvider側で処理・課金済みか判別できることを意味しません。
待機上限は要求ごとに2〜15分で、timeout・DNS・TLS・切断などはURIや生の例外本文を含まない固定codeで保存します。

Android DirectはProvider送信前に `dataSync` foreground serviceの開始完了を待ち、開始失敗時は有料requestを
送信しません。実行中は固定文言の通知を表示します。これはbackground中のprocess凍結を抑える仕組みであり、
利用者・OSによるservice停止、端末再起動、process終了後の復旧、Provider側の処理停止を保証しません。

Reference serverは `CLAGE_LIVE_API_ENABLED=false` のSAFE MOCKが既定です。liveにはvendor key、
`CLAGE_LIVE_API_ENABLED=true`、十分に長い `CLAGE_AUTH_TOKEN`、runごとの `confirm_live_api=true` が必要です。
HTTP retry既定値は0です。任意price tableとbudget予約はlocal guardであり、Provider請求書やspend capと同じ
transactionではありません。

## Reasoningと出力

model・出力枠のLOW / BALANCED / HIGHと、推論エフォートの設定値 / LOW / MEDIUM / HIGHは独立しています。
AUTOは設定画面の既定エフォートで、質問内容を分類せずProvider/model familyの固定policyから解決します。
回答の方向、結論、簡潔さを変える指示は追加しません。明示したLOW / MEDIUM / HIGHは対応modelへ同名effortを
送り、未知・非対応modelには未確認のreasoning fieldを送りません。

Directの1 call上限はClaude / ChatGPT / Grokが4,096 / 8,192 / 16,384、Geminiが
8,192 / 16,384 / 32,768（LOW / BALANCED / HIGH）、
会議全体が196,608 tokenです。これらはClage Cookのrequest上限で、Provider context、availability、費用の
保証ではありません。partial本文は完全回答ではないため、重要判断へそのまま使わないでください。

## Local policy scan

Directは送信前に、次の決定論的patternを端末内で検査します。

- private key block
- Anthropic、OpenAI、Google、xAIのAPIキーらしい文字列
- メールアドレス、電話番号らしい文字列

private keyとvendor key候補はblock、メール・電話は追加確認です。新規質問だけでなく、送信対象の添付本文を
合わせて再scanし、保存済みmemory、質問、回答を後続promptへ入れるときも検出箇所をredactします。
Reference serverはこれに加えてGitHub/AWS/Bearer/environment assignmentなどのpatternを持ちます。

scannerはDLP、secret manager、個人情報分類、法令適合性判定ではありません。未知形式、短い値、分割、encoded、
難読化、文脈依存の秘密を見逃し、通常文を誤検出する可能性があります。添付、memory、履歴、AI回答を含め、
利用者自身でも送信内容を確認してください。

Provider errorの生bodyはUIへ反射せず、固定分類を表示します。一方、AI回答本文は利用者へ表示・保存する
主データであり、機密情報、誤情報、prompt injectionを含み得ます。信頼できないdataとして扱ってください。

## 会話データ

### Direct BYOK

会話はSharedPreferencesのDirect専用namespaceへJSONとして保存します。immutable recordを先に書き、manifestを
commit pointにするため、中断したwriteは旧revisionを正として扱います。同じisolate・namespaceの操作を直列化し、
expected revisionの競合を拒否します。

保存され得るものは質問、memory、Provider回答、統合、model、usage、completion状態、再生成attempt、分岐metadata、
明示確認後のメール・電話などです。会話record自体は暗号化しません。OS app sandbox、端末暗号化、画面lock、
backup access controlを利用者が管理してください。Androidはapp data全体をcloud backupとdevice transferから
除外します。他platformのbackup、snapshot、user profile同期はplatform/管理policyに依存します。

Directの現在の上限は1会話16 MiB、memory 20,000文字です。全文検索は端末内recordを走査する単純な部分一致で、
大規模indexやsecure eraseを実装していません。削除後のflash storage、OS backup、snapshotからの復元不能性も
保証しません。

Direct添付は1件512 KiB以下、1会話8件以下、合計512 KiB以下のUTF-8
`txt` / `md` / `markdown` / `csv` / `json`だけです。NULを含む名前・本文は拒否します。添付text/bytesは
app process memoryだけに保持し、会話recordやZIPへ保存しません。app再起動後は再利用・downloadできません。

### Reference server

会話JSON、SSE journal、添付、budget ledgerは `CLAGE_DATA_DIR` に保存します。disk暗号化やbackup暗号化は
提供しません。同じdata dirを複数process/hostで共有せず、Uvicornは `--workers 1` を使ってください。

Reference serverの添付はowner固定opaque ID、MIME/signature/容量/TTL検査を行い、PDF text抽出を時間制限付き
subprocessへ分離します。これは悪意あるPDFの完全なsandboxではありません。元添付入りZIPは原bytesを含み、
secret scrubしません。

## Web検索と外部送信

`検索あり` は各Providerのserver-side search toolへ質問を送ります。通常のmodel APIに加えて検索service、取得先site、
Providerのtool処理へdataが渡り、別料金が発生する可能性があります。既定OFFとし、初回回答にだけ適用します。
DEBATE・統合で暗黙に再検索しません。

Claudeへ最大3 uses、xAIへ最大3 turnsを指定しますが、全Providerの内部検索回数を厳密に制限しません。
引用はHTTP(S) URLだけをclickableにしますが、リンク先の安全性・正確性を保証しません。DirectにはWeb toolの
金額guardがなく、reference serverでもtoken price tableだけではtool料金を完全見積できません。

## 停止の限界

Directの停止は端末側HTTP clientをcloseします。Reference serverの停止はlocal async taskへcancelを要求します。
どちらも、送信済みrequestがProviderで停止したこと、responseが生成されなかったこと、quota・課金が止まったことを
保証しません。timeout、network error、app終了後もProvider側では処理済みの場合があります。

Directは取得済み結果を処理終了時に保存しますが、強制終了直前またはlocal保存retryがすべて失敗した結果を
退避するrecovery outboxは未実装です。同じ質問を新しいIDで実行し直すと、改めて課金される可能性があります。
Reference serverはpending claim、checkpoint、event journal、terminal outcomeでより強い復旧を提供します。
このserver側保証をDirectへ読み替えないでください。

## Reference serverのnetwork公開範囲

- 通常は `127.0.0.1`、`--workers 1` で待ち受ける。
- smartphoneなど別端末から使う場合だけ `0.0.0.0` とし、信頼できるLAN/Tailscaleに限定する。
- router port forwarding、公開tunnel、無認証cloud endpointでinternetへ直接公開しない。
- LAN/Tailscale接続では十分に長い `CLAGE_AUTH_TOKEN` を必須にする。
- Bearerは通信を暗号化しない。信頼できないnetworkではTailscaleまたは正しく構成したHTTPSを使う。
- CORSとprocess-local rate limitをinternet向け認証・DoS防御として扱わない。

Direct BYOKにはこのreference networkが不要です。端末から各社公式HTTPS endpointへ出られるnetworkだけを使います。

Nativeのrelease buildはDirect BYOK専用で、reference toggleを表示しません。Reference serverはnativeの
debug/profile buildとWebでだけ選べます。Android releaseはcleartext trafficも拒否します。Web buildはCSP、
Trusted Types、`no-referrer` をmetaで設定していますが、production hostingでもHSTS、CSP、`nosniff`、
frame制御などをHTTP response headerで設定してください。meta CSPだけを完全なXSS防御として扱わないでください。

## Android release署名

Android releaseは共通debug keyへfallbackしません。`app/android/key.properties`、または
`CLAGE_ANDROID_KEYSTORE` / `CLAGE_ANDROID_STORE_PASSWORD` / `CLAGE_ANDROID_KEY_ALIAS` /
`CLAGE_ANDROID_KEY_PASSWORD` の4環境変数をすべて設定したときだけrelease signing configを作ります。
環境変数の部分設定と不完全なpropertiesはfail-closedでbuildを止めます。配布者自身のkeystoreを使い、公開前に
意図したcertificateを確認してください。

`key.properties`、`*.jks`、`*.keystore` はgitignore対象です。real signing materialをrepository、Issue、chat、
build logへ含めないでください。key紛失はupdate配布へ影響するため、暗号化backupとaccess controlを配布者が
管理してください。

## AI response、DEBATE、BLINDの限界

Provider回答と統合は不正確、有害、偏向、古い、またはprompt injectionを含む可能性があります。DEBATEは
誤りを見つける場合も、同じ誤りを増幅する場合もあります。統合は多数決でも正しさの証明でもありません。

BLINDは批評・統合promptのProvider名だけをalias化し、利用者向けcard、保存JSON、network接続先を匿名化しません。
重要な医療、法律、金融、安全判断では原典と適切な専門家を確認してください。

## 脆弱性の報告

脆弱性の詳細、APIキー、Bearer token、conversation、keystoreを公開Issueへ投稿しないでください。
[GitHubのPrivate vulnerability reporting](https://github.com/kojima8924/ClageCook/security/advisories/new) から
非公開で報告してください。この機能を利用できない場合は、詳細を伏せたIssueで非公開の連絡方法を
問い合わせてください。

報告には影響version、再現条件、想定影響、秘密を含まない最小再現手順を含めてください。
