# -*- coding: utf-8 -*-
"""HTTPエラー応答を1つの形へ統一するヘルパー。

0.2.0 まではエラーの ``detail`` が「``{code, message}`` の構造化」と
「生の日本語文字列」の2形態で混在していた。文字列側は機械判別できず、
復旧手順の異なる2種類の409(会話IDを付け直す / 新しいrequest_idを振る)を
クライアントが日本語文字列マッチでしか区別できなかった。

本モジュールの :func:`api_error` だけを使い、``detail`` は必ず
``{"code": <snake_case>, "message": <人間向け日本語>, ...extra}`` にする。
codeの一覧は ``docs/API_ERRORS.md`` を参照。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def api_error(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> HTTPException:
    """``{code, message, ...}`` 形式のdetailを持つHTTPExceptionを組み立てる。

    `extra` には復旧に必要な機械可読情報だけを入れる(現在revision、再開可能な
    最大event ID、plan、予算snapshot等)。例外メッセージ等の任意文字列は入れない。
    """
    detail: dict[str, Any] = {"code": code, "message": message}
    for key, value in extra.items():
        if value is not None:
            detail[key] = value
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


def error_detail_code(detail: Any) -> str | None:
    """統一形式のdetailからcodeを取り出す(未統一の値ならNone)。"""
    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
        return detail["code"]
    return None
