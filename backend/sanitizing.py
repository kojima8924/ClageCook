# -*- coding: utf-8 -*-
"""SSE・API応答・保存turnのsanitize純粋ヘルパー。

main.pyから分離。scrubbing/configのみに依存し、サーバー内部状態には触れない。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import config
import scrubbing


def _scrub_public(value: Any) -> Any:
    """SSE・API応答・永続化へ出す値から秘密候補を再帰除去する。"""
    return scrubbing.scrub_public_data(
        value,
        known_secrets=config.secret_values(),
    )


def _scrub_public_dict(
    value: Any,
    default: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """scrub結果がdictでなくなった場合も安全な既定dictへ落とす。"""
    public = _scrub_public(value)
    if isinstance(public, dict):
        return public
    return {} if default is None else default


def _sanitize_provider_error(
    data: dict[str, Any],
    *,
    fallback: str,
) -> None:
    """許可済み分類だけを固定文言で残し、任意分類を公開しない。"""
    if data.get("error_code") == "billing_or_credit_required":
        data["error"] = (
            "プロバイダの請求設定またはクレジット残高を確認してください"
        )
        return
    if data.get("error_code") == "model_refusal":
        data["error"] = "モデルのポリシー判定により回答が拒否されました"
        return
    data.pop("error_code", None)
    data["error"] = fallback


def _sanitize_event_data(event: str, data: dict[str, Any]) -> dict[str, Any]:
    """任意例外文字列をSSE履歴へ取り込まない。"""
    public = deepcopy(data)
    if event == "answer":
        if "error" in public:
            _sanitize_provider_error(
                public,
                fallback="AIからの回答取得に失敗しました",
            )
        else:
            public.pop("error_code", None)
        if "debate_error" in public:
            public["debate_error"] = "相互批評に失敗しました"
    elif event == "synthesis":
        if "error" in public:
            _sanitize_provider_error(
                public,
                fallback=(
                    "会議がキャンセルされました"
                    if public.get("cancelled")
                    else "統合に失敗しました"
                ),
            )
        else:
            public.pop("error_code", None)
    elif event == "error":
        public = {
            "message": (
                "会議がキャンセルされました"
                if public.get("cancelled")
                else "会議に失敗しました"
            )
        }
        if isinstance(data.get("request_id"), str):
            public["request_id"] = data["request_id"]
        if data.get("cancelled"):
            public["cancelled"] = True
    return _scrub_public_dict(public)


def _sanitize_turn(turn: dict[str, Any]) -> dict[str, Any]:
    """SSEと同じ基準で、保存する失敗理由から任意文字列を除く。"""
    public = deepcopy(turn)
    answers = public.get("answers")
    if isinstance(answers, dict):
        for answer in answers.values():
            if not isinstance(answer, dict):
                continue
            if "error" in answer:
                _sanitize_provider_error(
                    answer,
                    fallback="AIからの回答取得に失敗しました",
                )
            else:
                answer.pop("error_code", None)
            if "debate_error" in answer:
                answer["debate_error"] = "相互批評に失敗しました"
    synthesis = public.get("synthesis")
    if isinstance(synthesis, dict) and "error" in synthesis:
        _sanitize_provider_error(
            synthesis,
            fallback=(
                "会議がキャンセルされました"
                if synthesis.get("cancelled")
                else "統合に失敗しました"
            ),
        )
    elif isinstance(synthesis, dict):
        synthesis.pop("error_code", None)
    return _scrub_public_dict(public)
