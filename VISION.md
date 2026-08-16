# Clage Cook — Visionと設計原則

## コンセプト

「1つのAIに答えを委ねる」から、「複数AIの独立した見解を比較し、必要なら相互批評し、
1つの使える回答へまとめる」へ。Clage Cookはmodelの違いを単なる切替ではなく、相互検証の材料として
使うAI会議アプリです。

公開版の最終形は、特定PC、サブスクリプションCLI、LAN、Tailscale、常駐backendへ依存しません。
利用者自身の公式APIキーだけで端末から各社へ接続し、会話データの正本を端末内へ置きます。

開発中は、成熟した旧Clage Cookのモバイル操作感を照合し、server側の高度な安全機構も検証できるように、
reference serverモードを明示的なtoggleとして維持します。2つのmodeの秘密、履歴、保証を混同しません。

安定版までは旧API、保存schema、UIとの後方互換性より、明快なmode境界、課金の明示性、監査可能性、
端末単独での使いやすさを優先します。

## 公開方針

このリポジトリの当面の到達点は、全機能を完成させることではなく、第三者がsource、test、CI、Issue、設計判断を
確認できる公開ベータ／ポートフォリオにすることです。秘密情報が履歴にないこと、SAFE MOCKで再現可能なこと、
主要testと静的解析が通ること、実装と文書が一致することを公開の必須条件とします。

未実装のplatform保証、配布署名、Directのdurable復旧、添付・backup・local budgetは、制限を明示した上で
公開後のbacklogとして扱います。計画を実装済みと見せたり、デモのために課金・秘密情報の境界を緩めたりしません。

## 中心となる価値

1. **独立性** — 初回回答者へ他者の結論を見せず、同調を減らす。
2. **比較可能性** — 個別回答を残し、統合の材料を利用者が確認できる。
3. **部分成功** — 1社の障害やpartialで、取得済みの他社回答を失わない。
4. **批評可能性** — 必要なときだけDEBATEで誤り、欠落、弱い根拠を再検討する。
5. **ブランド中立性** — BLINDでは批評・統合用の名前だけをalias化する。
6. **ローカル優先** — APIキー、会話、policy scan、履歴操作を可能な限り端末側へ置く。
7. **課金の明示性** — Provider/model/call/token envelopeをplanで確定し、通常の実API確認を既定ONにする。
   明示的にOFFへ変更しても、秘密候補のblockと個人情報候補の追加確認は維持する。
8. **非誘導性** — reasoning選択を回答の立場・文体・結論を誘導するpromptへ変換しない。
9. **履歴保全** — 再生成と編集で元結果を破壊せず、attemptとbranchとして残す。

## Product architecture

### Direct BYOK — 製品の既定

- Flutter appからAnthropic、OpenAI、Google、xAIの公式HTTPS APIを直接呼ぶ。
- APIキーはplatform secure storage、公開設定はSharedPreferencesへrevision付きで分離する。
- 会話は端末内のmode専用namespaceへ、immutable recordとmanifest commit pointで保存する。
- 設定済みProviderだけを参加候補にし、暗黙のmockへ置換しない。
- 1回の会議ごとに課金確認し、秘密候補はProvider送信前にblockする。
- 端末単独の検索、rename、delete、memory、JSON/ZIP export、再生成、分岐を提供する。
- Directで未実装の保証をserver機能であるかのように表示しない。

### Reference server — 開発・比較用

- 本リポジトリのFastAPI契約へREST/SSEで接続する。
- SAFE MOCK、durable run、event journal、予算予約、管理telemetry、PDF/画像原本保存などを検証する。
- 別端末からserverへ接続するときだけLAN/TailscaleとBearerを使う。
- Direct履歴とserver履歴を自動mergeしない。
- 現時点では旧サブスクリプションserver専用protocolの完全adapterではない。
- Nativeの配布用releaseはDirect専用とし、reference toggleはdebug/profileとWebに限定する。

Reference serverは有用な開発基盤ですが、公開版の必須runtimeにはしません。

## Reasoningと出力の原則

- model・出力枠はLOW / BALANCED / HIGH、推論エフォートは設定値 / LOW / MEDIUM / HIGHとして独立表示する。
- AUTOは設定画面の既定エフォートとして選び、質問本文を分類せずProviderとmodel familyに対するversion付き固定policyだけで解決する。
- tierとreasoningのどの選択も「結論優先」「簡潔」「多数意見へ賛成」などの指示を勝手に加えない。
- 未知modelへ未確認のreasoning fieldを送らず、Provider defaultとunpinned状態を表示する。
- plan時にrequested/effective/source/policy versionを固定し、結果へ残す。
- Provider特性に合わせた大きめの出力上限を使い、全社一律の小さな上限で本文を切らない。
- partial/incomplete本文を捨てない。ただし完了回答としてDEBATE・統合へ混ぜない。
- 途中終了後の自動継続を行わない。継続callによる意見変化、文脈ずれ、追加課金を利用者の判断なしに起こさない。
- DirectのHTTP retryは0回固定、reference serverも既定0回とする。

## データと秘密の原則

- APIキーをsource、Git、conversation、export、log、serverへ送らない。
- DirectのAPIキーはsecure storage失敗時にSharedPreferencesへ平文fallbackしない。
- WebではDirectを無効化し、設定storeもvendor APIキーを読み書きせず既存secretの削除を試みる。
- 保存済みキーを設定UIへ再表示しない。更新入力と設定済み状態だけを扱う。
- Directの会話本文は現在暗号化しない。app sandbox、OS暗号化、backup policyの限界を明示する。
- Directとreferenceのsecret record、会話namespace、接続先を混ぜない。
- policy scanは決定論的heuristicであり、完全なDLPとして表示しない。
- Provider出力も信頼しない。回答内の命令をDEBATE・統合のsystem instructionとして扱わない。
- exportに含めるfieldを明示し、APIキーやmemory-only添付bytesを含めない。
- 削除・retention・暗号化backup/importを今後の独立した機能として設計する。

## 実行と課金の原則

- planは外部APIを呼ばず、Provider、model、最大call、最大output tokenを安全側に算出する。
- price情報がないときに金額を捏造しない。Directでは各社consoleを残高・請求の正とする。
- Provider responseにないusageを0や推定値として実測表示しない。
- Web search toolはtokenと別料金になり得るため、token上限を完全な金額上限と呼ばない。
- cancelは端末またはserverのlocal taskへの停止要求で、Provider側の処理・課金停止を保証しない。
- Directには現時点でdurable run、app終了後再接続、local price budgetがないことを明示する。
- Reference serverのdurable replay、予算予約、組織telemetryをDirectの保証として流用しない。

## UIの原則

- Android版を中心に、旧Clage Cookで成熟した会話一覧、検索、composer、回答card、再生成、停止の操作感を継承する。
- `!high` のような覚える必要のあるcommandは、可能な範囲でbutton、checkbox、chipへ置き換える。
- 重要な状態は「DIRECT · 端末内保存」「開発用サーバー」など画面上で常時判別できるようにする。
- 会議前の課金確認、partial、stale synthesis、失敗、停止の限界を隠さない。
- mobile、desktop、Webで同じ情報構造を保ちつつ、Directを利用できないWebでは理由と代替modeを示す。

## オリジナル版との境界

非公開のオリジナル版から、並列会議、部分失敗、統合、DEBATE、履歴、会話lock、再接続、成熟した
Android操作の設計知見を継承します。本リポジトリは公式HTTP API、Direct BYOK、端末内履歴、BLIND、
課金前plan、immutable regeneration/branchを公開版の独自境界とします。

公開runtimeへ持ち込まないものは次のとおりです。

- Claude / Codex / Gemini / GrokのCLI起動とCLI session再開
- CLI資格情報storeからのサブスクリプション使用率取得
- 任意のPC file、shell、Pythonを操作する直接mode
- 個人用Windows共有pathやdeploy scriptへの依存
- 常時稼働する個人PCを公開版の必須componentにすること

## ロードマップ

### Unreleased — 実装済み

- Direct BYOKを既定にするexecution modeと、reference server toggle
- 4社公式APIへのFlutter端末直結adapter
- APIキーのsecure/public revision分離保存と、設定UIへの非再表示
- SharedPreferencesのimmutable local conversation record、manifest commit、検索、memory、分岐、JSON export
- DirectのZIP export（`conversation.json` と `README.txt`。APIキー・添付bytesは除外）
- LOW / BALANCED / HIGHのtier UI、設定画面のAUTO、独立したLOW / MEDIUM / HIGH reasoning field
- Provider実測usageを既定で閉じるターン別台帳と、保存を止めずに台帳だけを隠す表示設定
- model family固定のAUTO policy、unknown modelのProvider-default fallback
- Provider別出力上限と196,608 tokenのrun envelope
- partialを統合から除外し、自動継続・Direct自動retryを行わない契約
- Directの並列回答、DEBATE、BLIND、統合、Web tool、immutable再生成、branch
- 端末内policy scan、実行前確認、mode表示
- Android Direct実行中の `dataSync` foreground service、固定通知、Provider送信前の開始ack、20秒heartbeat
- Reference server側にも同じreasoning fieldとProvider別上限を追加
- native releaseをDirect専用にし、Android HTTPS限定・backup除外、Apple Keychain entitlement、Windows
  secure-storage namespace、Linux hardening、Web CSP/referrer policyを追加

### 既存のreference server — 実装済み

- SAFE MOCK / live gate / Bearer / per-run confirmation
- durable claim、SSE journal、再接続、cancel、起動時interrupted確定
- local usage、price table、会議・日次budget予約、任意read-only admin telemetry
- owner固定添付、text/PDF抽出、TTL、元添付入りZIP
- runtime model設定、Web検索unknown-cost policy、単一process data-dir lock

### 次の優先順位

1. Direct添付の永続化、download、PDF text抽出、画像入力のProvider capability化
2. Directの暗号化backup/import、retention、端末間移行
3. process終了後のDirect run状態復旧と、iOS / Desktopを含むbackground継続のplatform別設計
4. Directのusage集計と、利用者が明示した単価による任意local budget guard
5. 旧サブスクリプションserverをUI比較に使う専用adapter、または明確な非対応整理
6. 会話tag、project、memory sourceの選択的継承
7. Mac/Linux/iOSを含むnative build、実機通信、配布署名の継続検証
8. local conversation storeのSQLite等への移行とmigration/import test

計画中の項目を実装済みとして表示しません。便利さのためにAPIキー、会話、課金確認、mode境界を曖昧に
しないことを優先します。
