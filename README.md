# Clage Cook (OSS版)

4つのAI(**Claude / Gemini / ChatGPT / Grok**)に同じ質問を並列で投げ、統合役AIが
1つの回答にまとめる **AI会議アプリ**。Flutter で **Web / Windows / macOS / Linux /
Android / iOS** に対応します。

> オリジナル(各AIのサブスクCLIを束ねるローカル専用版)とは別に、各社の公式API
> (BYOK)で誰でも動かせるようにしたOSS版です。

> **現状(P1完了)**: モックで4AI会議＋統合が動作。次は各社APIプロバイダの実装。
> 実装方針は [VISION.md](VISION.md)、開発の引き継ぎは [HANDOFF.md](HANDOFF.md) を参照。

## 特徴
- **複数AIの合議 + 統合役によるオーケストレーション**(このアプリのコア)
- **全6プラットフォーム**を1つのFlutterコードで
- **BYOK**(各社APIキー)。キーが無くても **モックモード** でデモが動く

## 構成
```
backend/   FastAPI。会議オーケストレーション + プロバイダ抽象化(モック/各社API)
app/       Flutter(全PF)。会議UI + SSE受信
```

## セットアップ
### backend
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --port 8000
```
`.env`(→ `.env.example`をコピー)に各社APIキーを入れると実AIに切り替わります。
未設定ならフルモックで動きます。

### app (Flutter)
```bash
cd app
flutter pub get
flutter run -d chrome    # または windows / macos / linux / (実機)
```

## ロードマップ
- [x] モックで4AI会議 + 統合(SSE)
- [ ] 各社公式APIプロバイダ(Anthropic / OpenAI / Gemini / xAI)
- [ ] 相互批評ラウンド(`!debate`)・品質tier
- [ ] web検索・画像生成
- [ ] 全PFビルド + デモ動画

## ライセンス
MIT (予定)
