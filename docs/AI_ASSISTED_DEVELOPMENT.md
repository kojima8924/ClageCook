# AI支援開発について

Clage Cookは、AIを単なるコード補完ではなく、設計批評、実装、回帰テスト、セキュリティレビュー、
UI検証まで一貫して使う開発ポートフォリオです。同時に、AIが生成したという事実を品質保証の代わりには
しません。

## 人間とAIの役割

人間が製品目標、利用者体験、課金・秘密情報に関する許容範囲、採用するtrade-off、公開判断を決めます。
AI coding agentと複数モデルによる批評は、次の作業を支援します。

- 非公開のオリジナル版とOSS版のUI・挙動差分の観察と整理
- Flutter / FastAPIの設計案、実装、refactor
- failure、race、部分成功、課金境界、保存境界を対象にした回帰testの作成
- source、設定、文書、Issueを横断したコードレビューとセキュリティ監査
- Android emulatorや実機を使った操作確認、build・install・動作確認
- README、CHANGELOG、設計文書、handoffの実装追従

AIの提案は、最終的に人間が決めた要件とリポジトリ内の証拠へ照合します。複数AIの一致も正しさの証明とは
扱いません。

## 検証可能な成果物

開発能力は会話ログの量ではなく、第三者が確認できる次の成果物で示します。

| 観点 | 主な証拠 |
| --- | --- |
| 製品設計 | [VISION.md](../VISION.md)、[README.md](../README.md) |
| 実装 | `app/lib/`、`backend/` |
| 回帰検証 | `app/test/`、`backend/tests/`、GitHub Actions CI |
| 安全境界 | [SECURITY.md](../SECURITY.md)、SAFE MOCK、test環境のfail-closed設定 |
| 変更の追跡 | [CHANGELOG.md](../CHANGELOG.md)、Issue、Pull request、commit history |
| 制限と次の判断 | [HANDOFF.md](../HANDOFF.md)、公開Issue |

API vendorの仕様や価格は変化します。model名、endpoint、tool仕様に関する変更は、可能な限り公式文書と
実際のresponse shapeを確認し、未確認事項を実装済みとして記載しません。

## 品質と安全のルール

- AIが書いたcodeもformat、静的解析、単体・widget・integration test、必要なbuildを通す。
- 実APIを使うsmoke testと、通常の自動testを分離する。CIとpytestはAPIキーを持たず、課金APIを無効にする。
- APIキー、Bearer token、conversation、keystore、個人情報をprompt、Issue、commit、test fixtureへ含めない。
- Providerのerror bodyや任意exceptionをそのままUI、保存JSON、logへ反射しない。
- AIの提案で文書と実装に差が生じた場合、同じ変更単位で文書を修正する。
- 未検証platform、未実装の復旧、料金見積もり、AI回答の正しさを保証しない。

## 実行時のAI利用との区別

Clage Cookが利用者のAPIキーでClaude、Gemini、ChatGPT、Grokを呼ぶことと、本プロジェクトの開発に
AI coding agentを使うことは別です。実行時の通信・保存・課金境界は [README.md](../README.md) と
[SECURITY.md](../SECURITY.md) に記載し、開発支援の手法を理由に権限を広げません。
