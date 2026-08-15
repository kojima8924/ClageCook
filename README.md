# Clage Cook

[![CI](https://github.com/kojima8924/ClageCook/actions/workflows/ci.yml/badge.svg)](https://github.com/kojima8924/ClageCook/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Flutter stable](https://img.shields.io/badge/Flutter-stable-02569B?logo=flutter)](https://flutter.dev/)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)

Claude、Gemini、ChatGPT、Grokへ同じ質問を並列に送り、回答を比較し、必要なら相互批評を経て
1つの回答へ統合するBYOK（Bring Your Own Key＝自分で取得した各社のAPIキーを持ち込んで使う方式）の
AI会議アプリです。

現在の本命は、Flutterアプリから各社の公式HTTPS APIへ直接接続する **Direct BYOK** です。
PC上のバックエンド、LAN、Tailscaleは不要で、会話は端末内へ保存します。旧Clage Cookの
サブスクリプション方式やサーバー版とUIを照合する開発用途のため、**reference server** 接続も
切り替えて使えます。

> **このアプリでできること / 使う前に確認すること**
> 1つの質問に対する各社の回答がどう食い違うかを、比較・相互批評・統合まで1画面で確認できます。
> 呼び出しに使うのは利用者自身のAPIキーなので、送信先のProviderと費用の主導権は利用者側にあります。
> 一方で現在は公開ベータであり、production supportや後方互換性はまだ保証しません。Direct BYOKは
> 各社の有料APIを呼び得るため、送信前に会議前planで最大呼出回数と利用するProviderを必ず確認してください。

本プロジェクトはAnthropic、OpenAI、Google、xAIによる公式製品または提携製品ではありません。
各社名、サービス名、商標はそれぞれの権利者に帰属します。

## この文書で使う用語

初めての方向けに、以降で使う用語を先にまとめます。

| 用語 | 意味 |
| --- | --- |
| Provider | Claude等を提供する各社（Anthropic / OpenAI / Google / xAI）、またはその接続先のこと |
| APIキー | 各社の有料APIを呼ぶための、利用者ごとに発行される認証用の文字列 |
| BYOK | Bring Your Own Key。運営者のキーではなく、利用者自身のAPIキーで各社APIを呼ぶ方式 |
| トークン（token） | AIが文章を数える単位。入出力の量＝課金量もこの単位で決まる |
| SSE | Server-Sent Events。サーバーから結果を細切れに受信し続けるHTTPの仕組み |
| モック / SAFE MOCK | 本物のAPIを呼ばずに偽の応答を返すテスト用動作。課金ゼロで挙動を確認できる |
| live gate | 実APIを呼べる状態になるまでの安全弁。条件が揃わない限りモックのままにする仕組み |
| fail-closed | 設定が不完全・不正なとき「動かない」側へ倒す設計。中途半端に動いて事故になるのを防ぐ |
| Bearer token | HTTPリクエストに添える合言葉のような認証文字列 |
| secure storage | OSが提供する暗号化された保存領域。APIキーの置き場所に使う |
| partial | 途中で終わった不完全な回答。破棄せず「不完全」と明示して残す |
| tier | 使用モデルと出力上限の段階。本アプリでは `LOW / BALANCED / HIGH` |
| durable | プロセスが落ちても実行記録が消えないよう、逐次ディスクへ書く性質 |
| immutable | 一度記録したら後から書き換えない性質。再生成の履歴保持などに使う |

## 2つの実行方式

アプリ本体（Flutter製）は共通で、接続先だけが異なります。

| 項目 | Direct BYOK（既定） | Reference server（開発用） |
| --- | --- | --- |
| API接続 | 端末から4社の公式APIへ直接 | FlutterからClage Cook OSS FastAPIへREST/SSE |
| 必要なもの | 1社以上のAPIキー | 起動済みserver、必要ならBearer token |
| 会話の正本 | 端末のapp storage | serverの `CLAGE_DATA_DIR` |
| LAN / Tailscale | 不要 | 別端末のserverへ接続するときだけ必要 |
| APIキー保存 | `flutter_secure_storage` | serverのenvironment / `.env` |
| モック | なし | SAFE MOCKあり |
| Web版 | Directは無効 | 利用可能 |
| 配布用native release | 利用可能 | 非表示（Direct専用） |
| durable再接続・予算台帳 | 未実装 | 実装済み |

Directの履歴とreference serverの履歴は混ざりません。設定画面の切替は、開発中にオリジナルの
操作感やOSS serverの挙動と見比べるために残しています。現時点のreference clientは本リポジトリの
FastAPI契約を実装しており、旧サブスクリプションserver固有APIへの完全な互換adapterではありません。

## クイックスタート

### 前提条件

- 共通: `git clone` でこのリポジトリを取得済みであること
- Direct BYOK: [Flutter](https://flutter.dev/) stable（`flutter doctor` で確認）と、対象OSのビルド環境
- Reference server: Python 3.10以上
- **APIキーをまだ持っていない場合**は、先にreference serverのSAFE MOCKを試すのがおすすめです。
  課金ゼロ・キー入力なしでアプリの流れを確認できます。

### A. まず課金ゼロで動かす（Reference server / SAFE MOCK）

Windows PowerShellの例です（macOS / Linuxは各手順の注記どおり読み替えてください）。

1. backendへ移動します。

   ```powershell
   cd backend
   ```

2. このプロジェクト専用のPython環境（venv）を作ります。PC全体のPythonを汚さないためです。

   ```powershell
   python -m venv .venv
   ```

3. venvを有効化します。

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

   macOS / Linuxでは `source .venv/bin/activate` です。PowerShellで「スクリプトの実行が無効」
   エラーになる場合は、同じウィンドウ内でだけ許可する
   `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned` を先に実行してください。

4. 依存パッケージをインストールします。

   ```powershell
   python -m pip install -r requirements.txt
   ```

5. 設定ファイルの雛形をコピーします（macOS / Linuxでは `cp .env.example .env`）。

   ```powershell
   Copy-Item .env.example .env
   ```

6. serverを起動します。

   ```powershell
   python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
   ```

7. ブラウザで <http://127.0.0.1:8000/api/health> を開きます。`"ok": true` と `"mode": "mock"` を
   含むJSONが表示されれば起動成功です。

初期状態は、APIキーがあっても外部APIを呼ばない `SAFE MOCK` です。Flutterアプリから繋ぐ場合は、
実行方式を「開発用サーバー」にしてこのURLを設定します。Web版（`flutter run -d chrome`）は
Direct BYOKを使えないため、この方式が前提です。

実APIを使う場合だけ、privateな `backend/.env` へ自分のキー、`CLAGE_LIVE_API_ENABLED=true`、
十分に長い `CLAGE_AUTH_TOKEN` を設定し、serverを再起動します。Flutter側にも同じBearer tokenを
保存します。live gate（うっかり課金APIを呼ぶ事故を防ぐ安全弁）の動作は次のとおりです。

| 設定 | 動作 |
| --- | --- |
| `CLAGE_LIVE_API_ENABLED=false` | 4つのSAFE MOCK。Bearerは任意 |
| `true`、`CLAGE_AUTH_TOKEN` なし | 起動を拒否 |
| `true`、APIキー0個 | Providerなしとしてfail-closed |
| `true`、APIキー1〜4個 | 設定済み実Providerだけを使用 |
| `INCLUDE_MOCK_PROVIDERS=true` | 未設定Providerの明示的なmock混在を許可 |

運用上の注意です。

- 同じ `CLAGE_DATA_DIR` を書くserverは1 processだけにし、Uvicornは `--workers 1` で起動して
  ください（複数processが同じ保存先を書くとデータが壊れるためです）。
- 別端末から接続する場合だけ `0.0.0.0` で待ち受け、信頼できるLANまたはTailscaleとBearer認証を
  使います。**インターネットへ直接公開しないでください。**
- Androidを含むnative releaseはDirect BYOK専用です。Reference toggleはnativeのdebug/profileと、
  Directを使えないWebでだけ表示します。

価格表、会議・日次budget、管理telemetry、runtime model設定はreference serverだけの機能です。
詳細は [`backend/.env.example`](backend/.env.example) と [HANDOFF.md](HANDOFF.md) を参照してください。

### B. 本命の使い方（Direct BYOK / Android・iOS・Desktop）

1. Flutterの状態を確認します。

   ```powershell
   flutter doctor
   ```

2. appへ移動して依存を取得します。

   ```powershell
   cd app
   flutter pub get
   ```

3. 対象deviceで起動します（Windowsの例）。

   ```powershell
   flutter run -d windows
   ```

   Android実機なら、接続済みdeviceを `flutter devices` で確認してから
   `flutter run -d <device-id>` を実行します。

4. アプリ右上の「接続とBYOK設定」で次を設定します。

   1. 実行方式を `Direct BYOK` にする。
   2. Claude / Gemini / ChatGPT / Grokのうち、使う会社のAPIキーを1つ以上入力する。
   3. 必要ならmodel IDと統合役を変更する。空欄は組み込み既定値を使う。
   4. 既定の推論エフォートをAUTO / LOW / MEDIUM / HIGHから選び、保存する。

5. 質問を送ると会議前planが表示されます。**最大呼出回数と利用Providerを確認してから**実行して
   ください（Direct BYOKは各社の有料APIを直接呼ぶためです）。

保存済みキーは画面へ読み戻しません。空欄のまま保存すると既存値を維持し、入力した会社だけを
更新します。キーを全削除する操作も用意しています。APIキーはsource、repository、会話JSONへ
保存しないでください。

Web buildでは、ブラウザからのキー抽出riskと各社CORS制約（ブラウザが別サイトへのAPI呼び出しを
制限する仕組み）を避けるためDirect BYOKを有効化できません。設定storeもvendor APIキーを
読み書きせず、旧recordは読み込まずに削除を試みます。Webで使う場合はreference serverへ
切り替えてください。

## 構成

```text
既定: Direct BYOK

Flutter app
  ├─ API keys ─ platform secure storage
  ├─ conversations ─ app-local SharedPreferences records
  ├─ local plan / policy / orchestration
  └─ HTTPS
       ├─ Anthropic Messages API
       ├─ OpenAI Responses API
       ├─ Gemini Interactions API
       └─ xAI Responses API

開発用: Reference server

Flutter app ─ REST/SSE ─ FastAPI
                           ├─ SAFE MOCK / official provider APIs
                           ├─ durable run + event journal
                           ├─ budget / telemetry
                           └─ server-local conversation store
```

`app/` がFlutterクライアント（詳細は [app/README.md](app/README.md)）、`backend/` が
reference server（FastAPI）です。backendの主なファイルは次のとおりです。エンドポイント群は
FastAPIのAPIRouter（エンドポイントを別ファイルへ分割して登録する仕組み）でmain.pyから
分離しています。

| ファイル | 役割 |
| --- | --- |
| `main.py` | アプリ本体。会議（`/api/chat`）、plan、設定、停止など中心エンドポイントと共有処理 |
| `api_models.py` | リクエスト入力の検証（Pydanticモデル） |
| `conversations_api.py` | 会話のCRUD・検索・添付・エクスポートAPI（APIRouter） |
| `regeneration_api.py` | 回答・統合の再生成API（APIRouter） |
| `sanitizing.py` | SSE・API応答・保存turnから内部情報を落とす整形ヘルパー |
| `scrubbing.py` | 公開データからの秘密らしい文字列の除去 |
| `policy.py` | 外部送信前に秘密らしい文字列を検出するローカルスキャナ |
| `planning.py` | 課金APIを呼ばずに1会議の最大実行量を見積もるplan |
| `orchestrator.py` | 4AIへの並列送信・相互批評・統合のコア |
| `providers/` | 各社APIアダプタ（anthropic / openai / gemini / xai）とSAFE MOCK |
| `finance.py` / `finance_costs.py` | 予算予約の台帳と、価格表による純粋なコスト計算 |
| `storage.py` | 会話の保存（1会話1JSONファイル） |
| `config.py` | `.env` 読み込みとlive gate判定 |

このほかに実行管理（`runs.py`）、利用量記録（`telemetry.py` / `admin_telemetry.py`）、
添付（`attachments.py`）、エクスポート（`exporting.py`）などの補助モジュールがあります。

## Direct BYOKで実装済み

既定方式でできることの一覧です（初回は読み飛ばして構いません）。

- Anthropic Messages API、OpenAI Responses API、Gemini Interactions API、xAI Responses APIへの端末直結
- 設定済みProviderの並列回答、部分失敗、統合、DEBATE、BLIND、Provider選択、統合省略
- model・出力枠の `LOW` / `BALANCED` / `HIGH` と、独立した推論エフォート選択
- AUTOを設定画面の既定エフォートとして保存し、質問を分類・書き換えずmodel familyの固定policyだけで解決
- 狭い画面でも全項目を省略しない、2段の横スクロール式composer。品質 / DEBATEと、
  エフォート / Web / 統合 / BLIND / 参加Providerを分離
- 会議条件を開始時にsnapshotし、生成中も現在のrunを変えずに次回用の下書きと設定を編集できるUI
- 会議前plan、Provider/model/最大call/最大出力tokenの表示、既定ONの課金API確認。dialogの
  「実行して次回から表示しない」または設定画面で、通常の課金可能性の確認だけをOFFにできる
- 初回promptの質問・添付・memory・履歴・systemをUTF-8 byteで積算し、1 call 1 MiBで遮断
- APIキー・秘密鍵らしい文字列の端末内blockと、メールアドレス・電話番号らしい文字列の確認
- partial/incomplete本文の表示・保存。途中終了後の自動継続や同一HTTP要求の自動再試行は行わない
- AndroidではProvider送信前に `dataSync` foreground serviceの開始完了を確認し、実行中通知と20秒ごとの
  app内heartbeatで、画面消灯・他アプリ表示中の長いDirect実行を保護
- 回答・統合のimmutable再生成attempt、active revision、stale統合表示、親を壊さない会話分岐
- Provider、model、実効effort、状態、経過時間を1行で確認できる回答cardと、本文、批評前の最初の回答、
  immutable attempt履歴のaccordion。現在回答、最初の回答、本文を持つ各attemptを個別に表示・コピー可能
- 端末内の会話一覧、全文検索、タイトル変更、削除、会話メモ、JSON export
- Provider応答が返した実測usageを既定で閉じた台帳へ表示。設定画面から台帳だけを非表示にでき、
  OFFでもusageの取得・端末保存・エクスポートは継続
- completion状態、reasoning解決、引用URLの保存・表示
- 既定OFFのWeb検索。各社のserver toolを初回回答でだけ許可し、DEBATE・統合では再検索しない
- APIキーをsecure storage、公開設定をSharedPreferencesへrevision付きで分離するfail-closed保存
- 会話ごとのimmutable recordとmanifest commit pointを使う端末内保存
- Android / iOS / Windows / macOS / Linux向けFlutter UI

## モデルtier・推論エフォート・出力上限

composerでは、使用modelと最大出力枠を決めるtierを `LOW / BALANCED / HIGH` から選びます
（内部値もそのまま `low / balanced / high` です）。推論エフォートはtierと独立しており、
直下の `設定値 / LOW / MEDIUM / HIGH` から選びます。`設定値` は設定画面に保存した
`AUTO / LOW / MEDIUM / HIGH` の既定値を使います。

| 推論エフォート | `reasoning_mode` | 動作 |
| --- | --- | --- |
| 設定値 | 保存済み既定値 | 設定画面の選択を使用 |
| LOW | low | 対応modelへlowを明示 |
| MEDIUM | medium | 対応modelへmediumを明示 |
| HIGH | high | 対応modelへhighを明示 |

設定画面のAUTOは、質問内容ではなくProviderとmodel familyの固定policyだけから実効エフォートを決めます。
回答の方向、文体、簡潔さ、結論を誘導するpromptではありません。未知modelやreasoning指定に未対応のmodelには、
未確認のfieldを送らずProvider既定値を使います。選択値と実効値はplan・結果へ残します。

Direct BYOKの1 callあたりの既定上限（単位はtoken）は次のとおりです。

| Provider | LOW | BALANCED | HIGH |
| --- | ---: | ---: | ---: |
| Claude | 4,096 | 8,192 | 16,384 |
| ChatGPT | 4,096 | 8,192 | 16,384 |
| Gemini | 8,192 | 16,384 | 32,768 |
| Grok | 4,096 | 8,192 | 16,384 |

会議全体の最大出力envelopeは196,608 tokenです。これは実際の生成量、context window、料金を保証する
値ではなく、Clage Cookが1回の会議へ許可する安全上限です。途中終了した回答はpartialとして残し、
意見を変えたり二重課金したりする可能性がある自動継続は行いません。

Reference serverも同じ `LOW / BALANCED / HIGH` のtierと、独立した
`auto / low / medium / high` のreasoning契約を使います。serverの既定上限とoverrideは
[`backend/.env.example`](backend/.env.example) を参照してください。

## 操作

composer上部は2段とも横へscrollできます。1段目でモデル・出力枠のLOW / BALANCED / HIGHとDEBATE、
2段目で独立した推論エフォート、WEB ON / OFF、統合、BLIND、参加AIを選びます。AUTOはcomposerへ
置かず、設定画面の既定エフォートとしてだけ選択します。

会議開始時に質問、tier、effort、DEBATE、統合、BLIND、Web、参加Provider、添付IDをsnapshotします。
実行が受理された後も入力欄と会議設定は次回用として編集でき、現在のrunは変化しません。送信ボタンは同じ位置の
停止ボタンへ切り替わり、実行中の二重送信を防ぎます。添付の追加・削除は生成中に行えず、次回用下書きは
自動queueではないため、現在のrunが終わってから利用者が送信します。

- DEBATEは完了した回答者をもう1回呼び、互いの回答を検証するため利用量と待ち時間が増えます。
- BLINDは批評・統合promptのAI名だけを決定論的aliasへ置換します。画面や保存データの出典は消しません。
- Web検索は既定OFFで、`検索あり` を選んだターンの初回回答だけに適用します。tool料金や対応modelは各社仕様に依存します。
- 統合は完了回答だけを材料にします。partial/incomplete回答は表示・保存しますが統合へ混ぜません。
- 回答と統合は個別に再生成でき、元結果はimmutable attemptとして残ります。
- ターン編集は親を上書きせず、対象ターン直前までを複製した分岐を作ります。

各Providerの回答は、状態と主要metadataを載せた1行headerから本文を開くaccordionです。DEBATEでは
「最初の回答（批評前）」を本文内の別accordionで開けます。再生成attemptも本文付きの履歴accordionへ残し、
partial / failure cardは見落とさないよう初期表示から開きます。Direct BYOK設定のProvider editorも、
キー状態とmodel要約をheaderへ出した折りたたみcardです。

トークン利用量台帳はターンごとのaccordionとして既定で閉じています。設定画面の「表示」から台帳だけを
非表示にできます。非表示は描画だけに作用し、Providerが返した実測usageを会話データやexportから削除しません。

会話検索はタイトル、メモ、質問、回答、統合を対象にします。`Ctrl/Cmd+K` で検索、`Ctrl/Cmd+N` で
新規会話へ移動できます。

## 保存と添付の現状

Direct BYOKの会話はSharedPreferences内のJSON recordです。1会話16 MiB、会話メモ20,000文字が現在の
実装上限です。これはapp sandbox内の保存であり、会話本文を暗号化する機能ではありません。端末の画面lock、
OS暗号化を利用者が管理してください。Androidはapp dataのcloud backupとdevice transferを除外します。
他platformのbackup policyと、将来の暗号化backup/importは未実装です。

Direct BYOKの添付は現在、1件512 KiB以下、1会話8件以下、合計512 KiB以下のUTF-8
`txt` / `md` / `markdown` / `csv` / `json`だけです。NULを含む名前・本文は拒否します。
選択した添付本文は実行中のmemoryにだけ保持され、会話recordへ元bytesを保存しないため、app再起動後の
再利用・download・添付込みarchiveはできません。DirectのZIP exportには `conversation.json` と内容を説明する
`README.txt` だけが入り、APIキー・添付bytes・Markdown版は含みません。PDF・画像の保存、PDF抽出、元添付入り
ZIPはreference serverでだけ利用できます。

## Directとreferenceの安全境界

安全機能はいずれも「誤操作・設定ミス・想定外の中断で、意図しない課金や情報送信が起きない」ことを
目的としています。

共通して、実APIを呼ぶ会議・再生成の通常確認は既定ONです（なぜ: 課金が発生し得る操作を、毎回
利用者の明示的な同意にするためです）。「次回から表示しない」または設定画面で通常の課金可能性の
確認だけをOFFにできます。APIキー・秘密鍵候補のblockと、メールアドレス・電話番号候補の追加確認は
この設定では解除されません。ただし両modeの機能は同一ではありません。

- Directは端末からAPIへ直接送信します。local price table、日次budget、管理API残高、durable event journal、
  app強制終了後のrun再接続はまだありません。Androidのforeground serviceはbackground中のprocess凍結を
  抑える実行時保護であり、OS・利用者による停止やprocess終了後の復旧を保証しません。
- Directの停止は端末側HTTP clientを閉じますが、Provider側の処理・課金停止を保証しません。
- Reference serverはdurable claim、再接続、budget予約（なぜ: 想定額を超える連続課金を上限で
  止めるためです）、server側scrubber（なぜ: 秘密らしい文字列を応答・保存データへ残さないためです）
  など追加防御を持ちます。
- Reference serverのHTTP retry既定値も0ですが、管理者が設定で増やせます。Directは常に0です
  （なぜ: 自動再試行は同じ課金を二重に発生させ得るためです）。
- どちらもpartial回答を自動継続しません。利用者が必要性を判断して再生成してください。

詳しい脅威モデルは [SECURITY.md](SECURITY.md) を参照してください。

## Reference server API概要

以下は開発用FastAPIの契約であり、Direct BYOKは端末内adapterを使うためHTTPでこれらを呼びません。

| Method | Path | 用途 |
| --- | --- | --- |
| `GET` | `/api/health` | mode、version、稼働確認 |
| `GET` | `/api/settings` | keyを含まない公開設定・上限 |
| `PATCH` | `/api/settings/runtime` | revision付きmodel設定 |
| `GET` | `/api/telemetry` | local usage、budget、任意admin集計 |
| `POST` | `/api/plan` | 無課金plan |
| `POST` | `/api/policy/scan` | local policy scan |
| `POST` | `/api/chat` | 会議開始・同一run再接続（SSE） |
| `POST` | `/api/runs/{request_id}/cancel` | local停止要求 |
| `GET/POST/PATCH/DELETE` | `/api/conversations...` | 履歴、検索、メモ、添付、export、分岐、再生成 |
| `POST` | `/api/budget/reconciliation/{request_id}/release` | 照合待ちの手動確定 |

## テストとビルド

```powershell
cd backend
python -m pip install -r requirements-dev.txt
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

通常testはreal API keyを使いません。`backend/tests/conftest.py` がテスト開始前に実API無効・
キー空の安全設定を強制するため、手元の `.env` で実APIを有効にしていてもtestから課金APIを
呼ぶことはありません。

iOS / macOS / Linuxのnative buildは各OS上で最終確認が必要です。
Android releaseはdebug keyへfallbackしません。配布用署名にはprivateな `app/android/key.properties` と
配布者自身のkeystore、または4つすべて揃った `CLAGE_ANDROID_*` 署名環境変数を用意し、certificateを
検証してください。部分設定はfail-closedでbuildを停止します。

## 現在の主な制限

- Direct BYOKのWeb利用、添付永続化、PDF/画像入力、Markdown同梱export、暗号化backup/importは未実装です。
- DirectのusageはProvider responseが返したfieldだけです。残高、請求額、spend limitは各社consoleで確認します。
- Directにはlocal金額上限がなく、Web tool料金も完全には予測できません。
- Android以外のDirect実行はbackground継続を保証しません。Androidも利用者・OSによるservice停止、強制終了、
  再起動後の復旧には未対応です。
- Directにはlocal保存が最終的に失敗した課金済み結果を退避するrecovery outboxがありません。
- model IDと各社API仕様は変わり得ます。組み込みmodelが利用できない場合は設定画面でoverrideしてください。
- policy scanは決定論的patternであり、秘密・個人情報の完全検出を保証しません。
- Provider回答は不正確になり得ます。BLINDやDEBATEは正しさを保証しません。
- Reference serverは旧サブスクリプション版の専用protocolを完全再現していません。
- 配布用Android/iOS/Desktop署名と全platform実機通信はreleaseごとに検証が必要です。

## 開発と公開方針

実装への参加方法は [CONTRIBUTING.md](CONTRIBUTING.md)、設計原則は [VISION.md](VISION.md)、
Flutter固有の説明は [app/README.md](app/README.md)、開発状況は [HANDOFF.md](HANDOFF.md)、
変更履歴は [CHANGELOG.md](CHANGELOG.md) を参照してください。

Flutter / DartによるUI実装はAI coding agentへ委任し、作者は仕様とアーキテクチャの決定、生成物への
修正指示、Windows・Android実機での動作確認を担当しています。Providerごとに異なるAPI契約（認証header、
endpoint、応答形式）を共通interfaceへ吸収すること、一部Providerが失敗しても完了済み回答を保持して統合まで
進めること、APIキーをOSのsecure storageへ置くこと、Direct接続と開発用reference serverを分離することは、
作者の設計判断です。

どの工程にAIを使い、人間が何を判断し、どの成果物で検証可能にしているかは
[AI支援開発について](docs/AI_ASSISTED_DEVELOPMENT.md) に記録しています。AIの提案はそのまま採用せず、
source、test、CI、Issue、文書の整合を確認して取り込みます。

作者の他の制作物は [ポートフォリオ](https://kojima8924.github.io/) にまとめています。

不具合・機能提案にはGitHub Issueを利用できます。秘密情報や脆弱性の詳細は公開Issueへ投稿せず、
[SECURITY.md](SECURITY.md) の報告手順に従ってください。

## ライセンス

[MIT License](LICENSE)
