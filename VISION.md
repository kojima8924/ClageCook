# Clage Cook OSS — Visionと設計原則

## コンセプト

「1つのAIに答えを委ねる」から、「複数AIの独立した見解を比較し、1つの使える結論へ
まとめる」へ。Clage Cook OSSは、モデルの違いを単なる切替メニューではなく、相互検証の
材料として使うAI会議アプリです。

OSS版の目標は、特定の個人PCやサブスクリプションCLIへ依存せず、利用者自身の公式APIキーで
再現できることです。同時に、キーを設定しただけでは課金APIを呼ばないこと、何を外部へ送り得るかを
実行前に見せること、再接続や停止でも同じ会議を重複実行しないことをコア要件とします。

`0.2.0` は設計を固めるための破壊的な初期OSS版です。安定版までは古いAPI・保存schema・UIとの
互換性より、課金安全性、監査可能性、明快なデータモデルを優先します。

## 中心となる価値

1. **独立性** — 回答者へ他者の結論を先に見せず、同調バイアスを減らす。
2. **比較可能性** — 個別回答を隠さず残し、統合結論の材料を利用者が確認できる。
3. **部分成功** — 1社の障害で会議全体を失敗させない。
4. **批評可能性** — 必要なときだけDEBATEで互いの誤りと欠落を再検討する。
5. **ブランド中立性** — BLINDでは批評・統合用の出典名を決定論的aliasへ置換する。
6. **課金の明示性** — live APIを既定OFFにし、会議前planと実行直前の承認を分離する。
7. **再現可能性** — 共通履歴、durable claim、event journal、透明なJSONを正とする。
8. **ローカル優先** — policy scanと語彙insightsは外部サービスを使わず決定論的に実行する。

## アーキテクチャ原則

- ベンダーAPIキーはバックエンドだけが保持し、レスポンスやFlutterへ返さない。
- `CLAGE_LIVE_API_ENABLED=false` を既定とし、キーの存在だけではliveへ移行しない。
- live会議はplan後に毎回明示確認する。APIを直接使う場合もconfirmation fieldを必須にする。
- live gateを有効にしたserverはBearer tokenなしで起動させない。SAFE MOCKではlocal開発のため認証を任意にする。
- 各社APIは共通のProvider契約へ正規化し、オーケストレーターはHTTP仕様を知らない。
- 会話履歴はClage Cook側を正とし、呼び出しごとに各Providerへ必要な文脈を渡す。
- 回答は並列実行し、完了順でUIへ届ける。保存時はProvider名で正規化する。
- 統合は成功回答だけを使い、回答内の命令をsystem instructionではなく引用データとして扱う。
- BLINDは外部へ渡すpeer labelを隠すが、利用者が出典と実測usageを監査できる情報は残す。
- policy scanは新規質問の秘密候補を外部呼出前・保存前に遮断する。保存履歴とAI回答は、後続の
  外部転送時にも防御的に再検査・redactする。
- Provider成功出力も信頼境界の外側とみなし、設定中secretとblock候補をSSE、永続化、既存履歴の
  公開直前に中央scrubberで再帰除去する。
- planはProviderを生成せず、履歴を含む入力UTF-8 byte、呼出回数、最大出力token、再試行を
  安全側に算出する。Provider固有token数、思考token、金額は推定したふりをしない。
- 生成HTTPの自動再試行は既定OFFとする。有効化時は失敗試行を含む実行envelopeへ算入する。
- Providerのpartial/incompleteとHTTP attemptを監査可能にし、usageが不明な試行を完全実測として扱わない。
- 切断はキャンセルと同義にしない。外部呼出前のdurable claimと生成イベントごとの永続化で復旧し、
  client再読込後も同一runへ復帰または停止できる。server再起動後の孤立runは中断確定し、自動再実行しない。
- 同じ `request_id` は同じpayloadにしか使えず、保存済みまたは未完了runを新規実行へ戻さない。
- 同一conversationの異なるrunは直列化し、plan・履歴・保存の競合を許さない。
- cancelはローカルtaskへの停止要求であり、外部Providerの処理停止・課金停止を保証しない。
- 1つの `CLAGE_DATA_DIR` は1サーバープロセスだけが所有する。起動時lockで複数processを拒否する。
- モックと実回答を暗黙に混在させず、障害時にモックを実成功として見せない。
- 外部公開を前提にしない。通常はlocalhost、別端末からはLAN/TailscaleとBearer認証を使う。
- mobile releaseを共通debug keyで署名しない。配布者固有のsecret signing materialをrepositoryから分離する。

## オリジナル版との境界

`C:\code\ClageCook` から継承したのは、並列会議、部分失敗、統合、DEBATE、tier、共通履歴、
会話ロック、原子的保存、再接続という設計知見です。OSS版はその操作感を踏まえつつ、公式HTTP API、
safe mock、課金前plan、BLIND、policy、offline insights、durable journalを独自の境界として持ちます。

次の個人環境向け機能はOSS版へ持ち込みません。

- Claude / Codex / Gemini / GrokのCLI起動とCLIセッション再開
- CLIや資格情報storeからのサブスクリプション使用率取得
- 任意のPCファイル、shell、Pythonを操作する直接モード
- Windows共有pathや個人用deploy script
- CLIを入れ子にした画像生成broker

## ロードマップ

### 0.2.0 — 実装済み

- 4社公式APIのBYOK Providerと、キーがあっても既定OFFのSAFE MOCK gate
- 会議ごとのlive・個人情報送信確認と、秘密候補を遮断するローカルpolicy scan
- 並列回答、部分失敗、統合、tier、DEBATE、BLIND、参加者選択
- 入力byte・Provider呼出・最大出力token・再試行を含む課金前plan
- 実測usageのtoken台帳と、完全ローカルの決定論的な語彙比較insights
- 会話履歴、server-side検索、タイトル、削除、JSON export
- durable claim、sanitized SSE event journal、イベントID再接続、保存run再生、cancel
- Bearer、CORS、rate・concurrency・入出力上限、単一process強制
- live時のBearer必須startup gateと、Android release signing secretのrepository分離
- URLへ検索語を出さないPOST検索、partial/request audit、xAI opaque cache key、切断中の二重run防止
- origin-bound Bearer保存、client再読込後のrunning run復旧、起動時のorphan中断確定
- immutable attemptによる回答・統合再生成、active revision、stale統合表示
- rate-limit header観測、全attemptのlocal usage、利用者設定price table、会議・日次budget予約
- 別管理資格情報による読み取り専用組織telemetryと、local/estimated/admin値の分離
- runtime model設定、immutable turn編集分岐、手動budget reconciliation
- owner固定opaque添付、text/PDF抽出、TTL、添付込みZIP export
- 既定OFFの4社Web検索、構造化引用、会話ごとのローカルメモ
- BackendとFlutter Webを常時検証するCI
- レスポンシブFlutter UIと全6platformのscaffold

### 次の優先順位

1. 検索index、暗号化backup/import、会話・添付のretention管理UI
2. Provider請求exportとlocal/admin usageのreconciliation差分監査
3. 画像入力・画像生成をProvider capabilityと個別予算へ統合
4. 会話tag、複数会話を束ねるproject、memory sourceの選択的継承
5. 共有受信、通知、background runなどのplatform連携
6. Mac/Linux上のnative build、全platformの実機通信、release署名検証
7. SQLite等へのstorage移行と、単一process不変条件を保つmigration tool

画像入力・生成や外部共有は便利ですが、コア会議の整合性、課金制御、秘密管理を
崩さないことを優先します。計画中の項目を実装済みとして表示しません。
