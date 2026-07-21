# Clage Cookへのコントリビューション

Clage Cookは公開ベータ段階のOSSであり、AI支援を含む設計・実装・検証の過程も成果として残す
ポートフォリオプロジェクトです。小さな不具合報告、再現手順、テスト、文書改善も歓迎します。

## Issueを作る前に

- 既存Issueと [README.md](README.md) の既知の制限を確認してください。
- 不具合は、OS、app version、実行方式（Direct BYOK / Reference server）、再現手順、期待結果、実際の結果を
  秘密情報を除いて記載してください。
- APIキー、Bearer token、会話本文、keystore、個人情報、Providerの生error bodyをIssueや画像へ含めないでください。
- 脆弱性は公開Issueへ投稿せず、[SECURITY.md](SECURITY.md) の手順で報告してください。

## 開発環境

- Flutter stable
- Python 3.10以降
- Git

Reference serverは初期状態で `SAFE MOCK` です。通常の開発とテストで実APIを有効にしないでください。
`backend/tests/conftest.py` は、ローカルの `backend/.env` が実APIを有効にしていても、pytest開始前に
課金APIと管理telemetryを無効化します。CIにもAPIキーを登録しません。

```powershell
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest -q -p no:cacheprovider
python -m compileall -q .

cd ..\app
flutter pub get
dart format --output=none --set-exit-if-changed lib test
flutter analyze
flutter test
flutter build web --release
```

native buildは対象OS上で追加検証してください。Androidの配布用署名素材はリポジトリへ置かず、
[SECURITY.md](SECURITY.md) の署名方針に従ってください。

## Pull request

1. 変更範囲を絞り、理由と利用者への影響を説明してください。
2. 挙動変更には、可能な限り失敗を再現するテストと修正後の回帰テストを含めてください。
3. `README.md`、`CHANGELOG.md`、`VISION.md`、`HANDOFF.md`、`SECURITY.md` のうち、実装とずれる文書を
   同じPRで更新してください。
4. 実装済み、試作、制限、計画を区別し、未確認のplatform対応や安全性を完了扱いにしないでください。
5. format、analyze、testの実行結果と、実行できなかった検証をPR本文へ記載してください。

後方互換性はまだ保証していません。ただし、保存形式・ネットワーク契約・秘密情報の扱いを変える場合は、
移行不能になる範囲と安全上の影響を明記してください。

## AI支援を使う場合

AIによる実装、レビュー、テスト生成、文書作成を利用できます。採用した内容の正しさ、安全性、ライセンス、
テスト結果には投稿者が責任を持ち、生成物を未確認のまま取り込まないでください。秘密や非公開データを
外部AIへ送らないでください。プロジェクト自身の運用方針は
[AI支援開発について](docs/AI_ASSISTED_DEVELOPMENT.md) にあります。

## 行動規範

[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) を守り、技術的な異論は人ではなく根拠、再現手順、trade-offへ
向けてください。
