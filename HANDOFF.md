# 引き継ぎ (HANDOFF)

このOSS版(`C:\code\ClageCookOSS`)の開発を引き継ぐ人向けのメモ。
コンセプト・方針は [VISION.md](VISION.md)、使い方は [README.md](README.md) を参照。

> オリジナル(`C:\code\ClageCook`)の詳細はここでは繰り返さない。設計資産を移植する際は
> そのフォルダを直接精査すること。

## 現状 (2026-07-17 / P1 完了)
- **backend/** (FastAPI): `/api/chat`(SSE)で `meta → 4AI並列回答 → 統合 → done` を流す。
  `providers/`(base + mock)で抽象化、`config.py`でプロバイダ解決(現状は全モック)。
  `curl` でSSE動作確認済み。`/api/health` あり。
- **app/** (Flutter 全6PF): 会議画面(4AIカード + 統合カード + 入力欄, SSE受信)。
  `flutter analyze` = No issues / `flutter test` = pass。
- git初期化 + 初回コミット済み(**ローカルのみ。GitHubリモートは未作成**)。

## 次にやること(優先順)
1. **各社APIプロバイダ実装(最優先)** — `providers/` に `anthropic.py` / `openai.py` /
   `gemini.py` / `xai.py` を追加し `base.Provider` を実装。`config.get_provider` を
   キー有無で実プロバイダ/モックを切り替えるよう更新(`_has_key` は用意済み)。
   orchestrator は変更不要。
2. **`!debate` / 品質tier** — オリジナル `server/orchestrator.py` の設計を移植。
   ただしCLI呼び出しは各社REST APIに置換する。
3. **設定画面(サーバURL / APIキー)** — app側。`main.dart` の `_baseUrl` は現状ハードコード。
   モバイル実機は `10.0.2.2` やLAN IPが要る。
4. **web検索 / 画像生成** — 各社APIで実装(コスト注意: 回数制限を入れる)。
5. **全PFビルド確認** — iOSはMacBook + 実機。Android は SDK 36 が必要(現35)。

## 開発の勘所
- 起動: backend `cd backend && uvicorn main:app --port 8000` / app `cd app && flutter run -d chrome`(or windows等)。
- **モック↔実の切替は `config.py` だけ**。orchestrator/app は触らない(抽象化の効果)。
- **APIキーは `.env`(→ `.env.example` をコピー)。絶対にコミットしない**(`.gitignore`済)。
- SSEイベント `meta` / `answer` / `synthesis` / `done` が、backendの `orchestrator.run_turn`
  と appの `_handle` の契約。増減させるときは両方を合わせる。

## 環境
- Flutter 3.44.6(`C:\dev\flutter`, PATH登録済)。Android SDK 35(→36要更新)。
  Web / Windows は即開発可。
- 設計資産(合議 / 統合プロンプト / debate / 履歴管理 / モデル解決)は
  `C:\code\ClageCook\server\` を精査して移植。CLI(claude/codex/agy/grok)呼び出しは
  各社REST API に置き換える。
