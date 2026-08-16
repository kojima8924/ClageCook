# API エラーコード一覧

Clage Cook backend のHTTPエラーは、**必ず**次の形で返ります。

```json
{
  "detail": {
    "code": "conversation_not_found",
    "message": "会話が見つかりません"
  }
}
```

- `code` — 機械判別用の安定した snake_case 識別子。クライアントはこれで分岐します。
- `message` — 人間向けの日本語説明。表示用であり、**分岐条件には使わないでください**
  (文言は将来変更されます)。
- 一部のエラーは復旧に必要な追加フィールドを同じ `detail` に持ちます
  (`plan` / `budget` / `required` / `max_event_id` / `current_revision` /
  `settings` / `corrupt_conversation_ids` など)。

> **0.2.0 の破壊的変更**: それ以前は `detail` が「構造化オブジェクト」と
> 「生の日本語文字列」の2形態で混在していました。現在は全て上記の形に統一され、
> 生文字列の `detail` は返りません。クライアントは
> `detail is String ? detail : jsonEncode(detail)` のような分岐をやめ、
> `detail["message"]` を表示し `detail["code"]` で分岐してください。

サーバー側の実装は `backend/api_errors.py` の `api_error()` に集約されています。
新しいエラーを追加するときは、必ずこのヘルパー経由にし、本表へ1行足してください。

---

## 認証・リクエスト形式

| code | HTTP | 意味 | クライアントの復旧手順 |
| --- | --- | --- | --- |
| `unauthorized` | 401 | Bearer トークンが未指定か不一致 | `CLAGE_AUTH_TOKEN` と同じ値を `Authorization: Bearer` で送る |
| `invalid_request` | 422 | リクエスト本文がスキーマに合わない | 送信内容を修正する(詳細は反射しません) |
| `invalid_request_id` | 422 | `X-Request-ID` ヘッダの形式が不正 | 使用可能な文字だけのrequest_idを使う |
| `request_id_header_mismatch` | 400 | body の `request_id` と `X-Request-ID` が不一致 | どちらか一方に揃える |
| `invalid_last_event_id` | 400 | `Last-Event-ID` が非負整数でない | ヘッダを外して最初から受信し直す |
| `rate_limit_exceeded` | 429 | 1分あたりの会議開始上限超過 | `Retry-After` 秒待って再送(同じrequest_idなら再課金されません) |

## 会話・ターン・添付

| code | HTTP | 意味 | クライアントの復旧手順 |
| --- | --- | --- | --- |
| `conversation_not_found` | 404 | 指定IDの会話が存在しない | 会話一覧を取り直す |
| `conversation_corrupt` | 422 | **ファイルは存在するが読み取れない** | `detail.conversation_id` のJSONを修復するか退避する。勝手に消しません |
| `turn_not_found` | 404 | 指定 `request_id` のターンが無い | 会話を取り直して対象を選び直す |
| `turn_not_completed` | 409 | 完了済みターンにだけ許される操作 | 実行完了を待つ |
| `conversation_busy` | 409 | 同じ会話で別の生成が実行中 | 完了後に再試行する |
| `conversation_memory_conflict` | 409 | ローカルメモの楽観lock競合 | `detail.current_revision` で読み直してから再送 |
| `attachment_not_found` / `attachment_owner_mismatch` | 404 | 添付が無い/別会話のもの | 添付一覧を取り直す |
| `attachment_expired` | 410 | 添付のTTL切れ | 添付し直す |
| `attachment_too_large` / `attachment_total_exceeded` / `attachment_count_exceeded` / `attachment_turn_count_exceeded` | 413 | 添付の上限超過 | ファイルを減らす・小さくする |
| `attachment_type_not_allowed` / `attachment_mime_mismatch` | 415 | 許可されない形式 | 対応形式へ変換する |
| `attachment_empty` / `attachment_read_failed` / `attachment_text_invalid` / `attachment_pdf_invalid` / `attachment_pdf_timeout` / `attachment_metadata_invalid` / `attachment_unavailable` | 422 | 添付の内容を安全に扱えない | 別のファイルにする |
| `attachment_conversation_required` | 422 | 添付付きの `POST /api/plan` に会話IDが無い | 先に会話を作ってIDを渡す |
| `export_failed` / `export_archive_failed` | 500 | エクスポートを生成できない | 再試行し、続くならサーバーログを確認 |

## request_id の再利用(**課金に直結**)

同じ 409 でも復旧手順が違うため、必ず `code` で区別してください。

| code | HTTP | 意味 | クライアントの復旧手順 |
| --- | --- | --- | --- |
| `request_id_conflict_conversation` | 409 | その request_id は**別の会話**で使用済み | **正しい会話IDを付けて**同じrequest_idで再送する(既存結果が再生されます) |
| `request_id_conflict_fingerprint` | 409 | その request_id は**内容の違う要求**で使用済み | **新しいrequest_idを振り直す**(内容を変えるなら別の実行です) |
| `request_id_ambiguous` | 409 | 分岐会話の複数に同じrequest_idが存在 | `conversation_id` を明示して再送する |
| `request_index_incomplete` | 409 | 読めない会話ファイルがあり、**実行済みか判定できない** | `detail.corrupt_conversation_ids` のファイルを修復・退避してから再送。二重課金を避けるため実行していません |
| `resume_not_available` | 409 | `Last-Event-ID` が再開範囲外 | `detail.max_event_id` 以下で再開するか、ヘッダ無しで受信し直す |
| `run_not_found` | 404 | キャンセル対象の実行が無い | 既に終了済み。状態を取り直す |

## 実行前ガード

| code | HTTP | 意味 | クライアントの復旧手順 |
| --- | --- | --- | --- |
| `explicit_confirmation_required` | 428 | 実API送信/個人情報らしい文字列の明示確認が未指定 | `detail.required` の確認フラグを付けて再送 |
| `policy_blocked` | 422 | 秘密情報らしい文字列を検出 | 該当箇所を除いて送信 |
| `plan_invalid` | 422 | 現在の設定でこの会議を開始できない | `detail.plan` を見て条件を変える |
| `run_limit_exceeded` | 422 | 1実行あたりの安全上限を超過 | 参加Provider・tier・添付を減らす |
| `budget_limit_exceeded` | 422 | 金額見積り/予算上限で開始不可 | `detail.plan` と予算設定を確認 |
| `budget_cost_unknown` | 422 | 金額を安全に見積もれない | 価格表を補うか `CLAGE_BUDGET_UNKNOWN_POLICY` を見直す |
| `per_run_budget_exceeded` | 422 | 1回あたり予算超過 | 条件を軽くする |
| `daily_budget_exceeded` | 422 | 日次予算超過 | 翌日または予算設定を見直す |
| `budget_reconciliation_backlog` | 422 | 照合待ち予約が上限 | 未照合の予約を解放する |
| `budget_reservation_conflict` | 422 | request_id が別の予算予約で使用済み | 新しいrequest_idを振り直す |
| `budget_reservation_in_progress` | 422 | 同じrequest_idの予約が別実行で使用中 | 実行完了を待つ |

## 予算の手動照合

| code | HTTP | 意味 | クライアントの復旧手順 |
| --- | --- | --- | --- |
| `reservation_not_found` | 404 | 対象の予算予約が無い | 予約一覧を取り直す |
| `reservation_not_reconcilable` | 409 | 手動照合できる状態ではない | 状態を取り直す |
| `reconciliation_confirmation_required` | 409 | 未観測課金が無いことの明示確認が必要 | `confirmed_no_unobserved_charge` を付けて再送 |

## 再生成

| code | HTTP | 意味 | クライアントの復旧手順 |
| --- | --- | --- | --- |
| `regeneration_target_unavailable` | 404 / 409 | 再生成対象の回答・統合が無い、またはターンが未完了 | 対象を選び直す |
| `regeneration_plan_unavailable` | 409 | plan を組めない(成功回答が無い等) | 先に回答を再生成する |
| `regeneration_id_conflict_fingerprint` | 409 | その regeneration_id は内容の違う要求で使用済み | 新しい regeneration_id を振り直す |
| `regeneration_in_progress` | 409 | 同じ attempt が実行中 | 完了を待つ |
| `regeneration_cancelled` | 409 | 再生成がキャンセルされた | 必要なら新しい regeneration_id で再実行 |
| `regeneration_failed` | 500 | 再生成が想定外の理由で失敗 | 再試行し、続くならサーバーログを確認 |

## 設定

| code | HTTP | 意味 | クライアントの復旧手順 |
| --- | --- | --- | --- |
| `runtime_settings_conflict` | 409 | 実行時設定の楽観lock競合 | `detail.settings` の `revision` で読み直して再送 |
| `invalid_runtime_settings` | 422 | model ID・統合役の指定が不正 | 値を修正する |
