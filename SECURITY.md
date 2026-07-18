# セキュリティポリシー

Clage Cookは、利用者自身が管理するbackendをlocalhost、自宅LAN、またはTailscale経由で使う
ことを前提としています。インターネットへ直接公開する用途や、相互に信頼しない複数tenantでの
共有運用は想定していません。

## サポート対象

開発初期のため、security修正は原則として最新releaseだけに提供します。`0.2.0` はAPI、保存schema、
安全機構の後方互換性を保証しません。問題を報告する前に、秘密値を使わない再現環境で最新版でも
発生することを確認してください。

## SAFE MOCKと実APIの武装

既定の `CLAGE_LIVE_API_ENABLED=false` では、API keyが設定済みでも外部Providerを呼びません。
live APIを使うには次をすべて満たす必要があります。

1. vendor API keyをbackend environmentへ設定する。
2. `CLAGE_LIVE_API_ENABLED=true` を設定してbackendを再起動する。
3. 十分に長いrandom値を `CLAGE_AUTH_TOKEN` へ設定する。live gateがtrueなのにtokenが空なら
   backendはstartupを拒否する。
4. 各billable runでFlutterの確認画面を承認するか、API requestへ
   `confirm_live_api=true` を明示する。
5. email addressまたは電話番号候補を含む場合は `confirm_sensitive_data=true` も明示する。

確認fieldがないbillable `POST /api/chat` は428で拒否されます。startup、認証、run確認のgateは誤操作を
減らしますが、
各社のprice、quota、契約、API側の処理結果を保証しません。

`POST /api/plan` は外部APIを呼ばず、historyを含む入力UTF-8 byte、Provider call、最大output token、
retryの安全側envelopeを返します。price table未設定時は金額を推定しません。利用者が契約単価を明示した
場合だけ、Decimal計算した安全側の最大金額と会議・日次budgetを適用します。生成requestは応答を失った場合に
同じ処理が課金され得るため、`HTTP_RETRIES=0` が既定です。
retryを増やす場合は、失敗した試行も課金され得る前提でrun limitと契約を確認してください。
各社のWeb検索toolはmodel tokenと別課金になる場合があり、現行price tableはtool単価・検索回数を
安全側に算出できません。budget有効時のlive Web検索はunknown costとし、既定の
`CLAGE_BUDGET_UNKNOWN_POLICY=block` で停止します。`allow` でも価格判明済みのtoken小計は会議・日次上限で
検査・予約しますが、不明なtool料金はlocal上限の外側です。この残余riskを理解した場合だけ使ってください。

budgetのcheckと予約は単一backend process内のlock下で行い、ledgerをatomic保存しますが、Provider請求と同じ
transactionではありません。外部送信後にusageが確定しないrunは0円へ解放せず照合待ちとして拘束します。
再起動時の未settle予約も照合待ちへ昇格し、設定件数へ達したbacklogは新しい課金runをfail-closedで止めます。
照合待ち・確定済みの同一request IDを、保存済み結果のreplayなしに外部へ再dispatchしません。
usageを価格換算できたrunは実測額をsettled ledgerへ保存し、conversationを削除しても当日のcommitから
除外しません。応答modelを価格換算できない場合は予約上限を保持します。手動reconciliationは照合待ち件数を
解消しますが、既知実測額、または予約上限を `settled_after_manual_reconciliation` としてcommitへ残します。
active予約はgross額を監査表示へ残しつつ、同じrequest IDで観測済みの実績との差額だけを
`active_reservation_top_up` としてcommitへ足します。actual実績とgross予約を同時に全額加算しません。
branch片側の再生成で既存結果をoriginal attemptへ包んでも、元turnと同じidentityへ正規化し、費用とlocal tokenを
新しい外部callとして二重計上しません。実際に追加送信した非original attemptだけを独立して数えます。
送信直前に実行snapshotと予算日を再検証し、参加Provider/modelと統合Provider/modelの両方を固定して、
予約と実行先の分離を防ぎます。
runtime設定はplan内で1回だけsnapshotし、SSE metaと失敗結果のsourceもそのProvider/modelへ固定します。
price tableがカテゴリ単位で不完全でも、判明済みrateの小計は予算拘束し、未知部分だけをunknownとして扱います。
これはProvider側spend cap、複数hostの分散lock、exactly-once課金、最終請求額を保証する機能ではありません。
実測費用のcache tokenはProvider別に正規化します。Anthropicの `input_tokens` はcache read/writeと独立した
uncached区分として使い、他Providerでは正規化済みinput totalからcached inputを差し引きますが、いずれも
利用者が設定したprice tableによるlocal推定であり請求書ではありません。
reasoning課金もresponse shapeに依存します。Gemini Interactionsの `total_thought_tokens` はoutput外数、
OpenAI/xAI Responsesのreasoningはoutput内数として扱います。旧xAI Chat互換shapeのdurable Grok usageだけは
completion外数を保持するため `total_tokens - input_tokens` をfallbackにします。これも請求仕様の将来変更を
自動検出するものではありません。

## API keyと認証情報

- Anthropic、OpenAI、Google Gemini、xAIのAPI keyは `backend/.env` またはbackend processの
  environment variableだけに設定してください。
- 組織telemetryには推論キーを流用せず、`ANTHROPIC_ADMIN_KEY`、`OPENAI_ADMIN_KEY`、
  `XAI_MANAGEMENT_KEY` を別資格情報として最小のread権限で発行してください。これらも既知secretとして
  公開経路から除去されます。管理telemetryを有効にしたbackendはBearer認証なしでは起動しません。
- vendor API keyをFlutter app、Web browser、mobile設定、source、Git履歴へ保存しないでください。
  frontendが保持してよい秘密はClage Cook backendへ接続するためのBearer tokenだけです。
- Flutterはserver URLとBearer tokenをrevision・正規化originで結合し、secure storageと公開設定の両recordが
  完全一致した場合だけtokenを復元します。secure writeを先に、公開recordをcommit pointとして保存するため、
  途中失敗や別originとの組合せではfail-closedにtokenを空にします。実行中は接続先を変更できません。
  URLのpathはreverse proxy用に許可しますが、userinfo、query、fragmentは秘密の平文保存やAPI path破損を
  避けるため、画面と保存層の両方で拒否します。認証必須serverでは空tokenを保存できません。
- Web版の `flutter_secure_storage` はbrowser側storageとWebCryptoに依存します。XSS、悪性拡張機能、
  共有browser profileや共有端末からBearer tokenを完全に保護できるsecret managerではありません。
  設定画面の常時警告を確認し、共有端末ではtokenを保存しないでください。
- `backend/.env` をcommitしないでください。log、screen share、test fixture、bug reportにもreal keyや
  完全なBearer tokenを含めないでください。
- keyまたはtokenが漏えいした可能性がある場合は、該当service側で直ちに無効化・再発行してください。
  Git履歴に入った秘密は、fileを削除するだけでは除去されません。
- backendの公開settingsとhealthはkeyの値を返さず、configured/modeなどの状態だけを返します。
- backendは現在設定中のvendor API keyとBearer tokenを既知secretとして扱い、Provider成功出力を含む
  SSE、event journal、conversation保存、一覧、検索、取得、exportを公開する直前に再帰scrubします。

`CLAGE_ADMIN_TELEMETRY_ENABLED=false` が既定で、この状態では管理APIへ外部通信しません。有効時も
organization usage/cost、xAI balance/invoice preview/spending limitの読み取りだけを実装し、top-up、支払方法、
spend limit変更を呼びません。結果は短期cacheされ、API error本文、team ID、key ID、個別workspace情報は
公開せず集計値と固定error分類だけを返します。Provider実効期間とlocal予算期間も分離し、
不完全なUTC bucketを完全な当日値として表示しません。Geminiの残高は通常API keyから推測しません。

## ローカルpolicy scan

送信前scanは、private key block、vendor API key、GitHub token、AWS access key、Basic/Bearer、既知の
secret environment assignmentなどを `block` として検出します。blockされたchatはconversation作成や
Provider呼出より前に422で拒否し、responseには該当秘密の生値を含めません。Flutterはmask済みtextへの
置換を提示します。

email addressと電話番号らしい文字列は `confirm` です。mock runは外部送信しないため追加確認を必須に
しませんが、live runでは明示確認が必要です。保存済みhistoryやAI answerは、DEBATE・統合・後続turnで
外部へ渡す前にも防御的に再scanし、secretとcontact候補をredactします。

公開・保存経路の中央scrubberは、既知secretと `block` 候補だけを除去します。明示確認で扱える
email address・電話番号などの `confirm` 候補は会話内容として保存され得るため、保存JSON自体を
機密データとして管理してください。入力schema違反は固定422へ変換し、不正な入力値やvalidator詳細を
responseへ反射しません。vendor error本文も公開せず、Claudeで確認済みの請求・credit不足だけを
固定分類へ変換します。

scannerは公開された正規表現による決定論的heuristicです。次を保証しません。

- 未知形式、短い値、分割・encoded・難読化された秘密の検出
- 誤検出がないこと
- 文脈上の個人情報・機密情報の完全な分類
- 利用者がredacted markerへ置換した後の文面が業務上安全であること

送信前に利用者自身でも内容を確認してください。scan結果をDLP、secret manager、法令適合性判定の
代替として使わないでください。

## 保存データとevent journal

`CLAGE_DATA_DIR` のconversation JSONには、質問、Provider answer、統合、model、実測usage、policyで
maskされなかった個人情報、sanitized SSE event journalが含まれる可能性があります。保存先をshare folder、
cloud同期の公開directory、公開repositoryに置かず、backupにも同等のaccess controlを適用してください。

runは外部呼出前にpending claimを保存し、answer/synthesisを中間checkpointとして更新し、
終端時にその時点までの非終端生成eventをjournalへ確定します。`error` / `done` はjournalへ重複保存せず、
再生時にdurableなturn終端状態から再構成します。各小eventで会話全体をfsyncしないため、最後checkpoint後から
process強制終了までの非checkpoint eventは失われ得ます。ただし外部呼出前claimにより二重実行は避けます。
server起動時に前process由来の `status=running` は `interrupted` へ確定し、
外部APIを自動再実行しませんが、disk暗号化やbackup暗号化は提供しません。OS account、full-disk encryption、
backup policyは利用者が管理してください。不要なconversationはappまたはAPIで削除してください。
保存済みturnまたはin-memory runへの同一fingerprint replay/joinは新規planより先に解決します。現在の予算や
確認flagを迂回して新しい外部送信を行うものではなく、保存済みeventを返すだけです。添付の並び順はpromptを
変えるためfingerprintに含め、順序違いを同じrequest IDとして再利用しません。
完了した再生成はdurable attemptを正としてregistryから直ちに除去し、conversation全体を含む一時resultを
既定1時間のrun retentionへ残しません。後続の同一IDは保存attemptから再生します。認証とfingerprint照合は維持し、
保存済みterminal replayだけをrate limiterとactive conversation claimより先に返すため、無関係な429/409を避けても
新しい外部送信や課金は行いません。

添付PDFは10秒上限の隔離Python processで先頭page/文字数だけを抽出します。これは
pypdfのparser失敗やCPU占有を主processと分離するための境界で、悪意あるPDFの安全性や完全な
sandboxを保証しません。同時subprocessは2本まで、成功結果はSHA-256と抽出上限をkeyにした
process内5分/64件TTL/LRU cacheで再利用します。期限切れの抽出cacheは次のcache access時に除去され、
再起動時にはcache全体が消えます。これとは別に、元添付の保存TTLはserver起動時または添付access時に
purgeします。元PDF byteは利用者が取得するdownload/ZIPではscrubされません。

同じ `CLAGE_DATA_DIR` を複数processまたは複数hostで共有してはいけません。serverは起動時に
`.server.lock` を排他的に取得し、同じdata dirを使う2つ目のprocessを拒否します。Uvicornは必ず
`--workers 1` で起動してください。このlockはnetwork filesystem上の分散lockを提供しません。

保存全文検索は `POST /api/search` のJSON本文を使います。検索語をURL query、browser history、通常の
access logへ載せないためです。search本文や結果も機密データとして扱ってください。同一conversationへ
異なるrunを同時送信した場合は、古いplanや履歴で実行しないよう409で拒否します。

## 停止と課金の限界

cancel endpointはchatと再生成で共通のClage Cook local taskへcancelを要求します。Provider結果が未確定なら、
取得済みanswerとusageを不完全なcancelled turn/attemptとして保存します。Provider結果後に終端保存、budget
settle、結果公開へ入った場合はその処理をcancelから保護し、先に確定したcompleted/failedをcancelledへ
変更しません。再生成Provider failure後のcleanup中にcancelされても、failed outcome、台帳settle、
`cancelled=false` のinterrupted attemptを保存します。cancel APIの `terminal_outcome` は判明済みの
completed/failed/cancelledを区別し、未確定ならnullになり得ます。
すでに送信したHTTP requestが外部Providerで停止したこと、responseが生成されなかったこと、quota・課金が
止まったことは保証しません。UIやAPI clientはcancel要求の受理を外部課金停止として扱わないでください。
cancelled/failed turnのusageは `usage_may_be_incomplete=true` です。
timeout、network error、retry後の成功も、外部Provider側で処理済みか確認できない場合があります。
各answer/synthesisの `request_audit` は試行数と固定分類だけを残し、課金額やusage完全性を保証しません。

## ネットワーク公開範囲

- 通常はbackendを `127.0.0.1`、`--workers 1` で待ち受けさせてください。
- smartphoneなど別端末から使う場合に限り `0.0.0.0` で待ち受け、自宅LANまたはTailscaleからだけ
  到達できるようOS firewallを設定してください。
- router port forwarding、公開tunnel、公開cloudの無認証endpointなどを使ってinternetへ直接公開しないで
  ください。外出先からはTailscaleを使用してください。
- LAN/Tailscaleから接続できる構成では、十分に長いrandom値を `CLAGE_AUTH_TOKEN` に設定し、Flutterには
  同じ値をBearer tokenとして保存してください。
- Bearer認証は通信を暗号化しません。local HTTPは信頼できるLAN内だけで使い、信頼できないnetworkでは
  Tailscaleまたは適切に構成したHTTPSを使用してください。
- Web版は `CLAGE_CORS_ORIGINS` を実際のWeb originへ限定してください。CORSは認証の代わりではありません。
- rate limitとconcurrency limitはprocess-localの誤操作防止であり、internet向けDoS防御ではありません。

## Android release署名

Android releaseはFlutterの共通debug keyへfallbackしません。`app/android/key.properties` がある場合だけ
release signing configを作ります。repositoryの `key.properties.example` をcopyし、配布者自身のkeystore
path、alias、passwordへ置き換えてください。

`app/android/key.properties`、`*.jks`、`*.keystore` はgitignore対象です。real passwordやkeystoreをcommit、
chat、Issue、build logへ含めないでください。propertiesがないrelease outputは配布用署名済みではないものと
扱い、公開前に自分のrelease/upload keyで署名されていることをAndroidの署名確認toolで検証してください。
keyを紛失するとupdate配布へ影響するため、暗号化backupとaccess controlを配布者が管理してください。

## AI responseとBLIND/insightsの限界

Provider answerと統合は不正確、有害、またはprompt injectionを含む可能性があります。DEBATE・統合では
peer answer内の命令を引用dataとして扱うsystem instructionを使いますが、model挙動を完全には保証しません。
重要な判断では原典と専門家による確認を行ってください。

BLINDはDEBATE・統合用promptからProvider名をalias化する機能で、利用者向けcard、保存JSON、network先まで
匿名にする機能ではありません。offline insightsのscoreは文字・語彙の重なりであり、意味的一致、事実性、
品質、bias、modelの確信度を表しません。token台帳はProviderが返した実測fieldだけです。local金額は
利用者設定のprice tableによる推定、admin costはProvider管理APIの期間集計であり、いずれも最終請求書や
個々のrunへの完全な費用帰属を示しません。

partial/incompleteの本文は利用可能でも完全回答ではありません。UIの警告と `incomplete_reason` を確認し、
出力上限・filter・context上限などの理由を解消してから重要判断へ使ってください。

## 脆弱性の報告

脆弱性の詳細、API key、Bearer token、conversation dataを公開Issueへ投稿しないでください。GitHub repositoryで
Private vulnerability reportingが利用できる場合は、Security tabの「Report a vulnerability」から非公開で
報告してください。利用できない場合は、詳細を伏せたIssueで非公開の連絡方法を問い合わせてください。

報告には、影響version、再現条件、想定影響、可能であればsecretを含まない最小再現手順を含めてください。
受領後、内容を確認し、修正方針と公開時期を報告者と調整します。
