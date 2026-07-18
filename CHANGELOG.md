# Changelog

このプロジェクトの主な変更を記録します。形式は
[Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) を参考にし、バージョン番号は
[Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [0.2.0] - 2026-07-18

後方互換性を前提としない、最初の実用的なBYOK OSS版です。

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
