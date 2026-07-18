# -*- coding: utf-8 -*-
"""公開データから既知の秘密値と秘密らしい文字列を再帰的に除去する。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

import policy


KNOWN_SECRET_MARKER = "⟪REDACTED:known_secret⟫"


def scrub_public_data(
    value: Any,
    *,
    known_secrets: Iterable[str] | str | None = None,
) -> Any:
    """公開前の値を、元データを変更せず再帰的にスクラブする。

    呼出側が把握している秘密値を最優先で完全一致・部分埋込ともに置換し、
    続いて :func:`policy.scan_text` の決定論的なパターン検査を適用する。
    JSON相当の組込みcontainerは可能な限り元の型を維持する。
    """

    secret_pattern = _known_secret_pattern(known_secrets)
    return _scrub(value, secret_pattern)


def scrub_text(
    value: str,
    *,
    known_secrets: Iterable[str] | str | None = None,
) -> str:
    """単一文字列へ既知secret置換とpolicy検査を順番に適用する。"""

    return _scrub_text(value, _known_secret_pattern(known_secrets))


def _known_secret_pattern(
    known_secrets: Iterable[str] | str | None,
) -> re.Pattern[str] | None:
    if known_secrets is None:
        return None
    candidates = (
        (known_secrets,)
        if isinstance(known_secrets, str)
        else known_secrets
    )
    secrets = sorted(
        {
            candidate
            for candidate in candidates
            if isinstance(candidate, str) and candidate
        },
        key=len,
        reverse=True,
    )
    if not secrets:
        return None
    # 単一regex置換にして、置換markerが別のknown secretで再置換されるのを防ぐ。
    return re.compile("|".join(re.escape(secret) for secret in secrets))


def _scrub(value: Any, secret_pattern: re.Pattern[str] | None) -> Any:
    if isinstance(value, str):
        return _scrub_text(value, secret_pattern)
    if isinstance(value, dict):
        return {
            _scrub(key, secret_pattern): _scrub(item, secret_pattern)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item, secret_pattern) for item in value]
    if isinstance(value, tuple):
        scrubbed = tuple(_scrub(item, secret_pattern) for item in value)
        if type(value) is tuple:
            return scrubbed
        if hasattr(value, "_fields"):
            try:
                return type(value)(*scrubbed)
            except TypeError:
                pass
        try:
            return type(value)(scrubbed)
        except TypeError:
            return scrubbed
    if isinstance(value, set):
        return {_scrub(item, secret_pattern) for item in value}
    if isinstance(value, frozenset):
        return frozenset(_scrub(item, secret_pattern) for item in value)
    return value


def _scrub_text(value: str, secret_pattern: re.Pattern[str] | None) -> str:
    scrubbed = (
        secret_pattern.sub(KNOWN_SECRET_MARKER, value)
        if secret_pattern is not None
        else value
    )
    scan = policy.scan_text(scrubbed)
    # `confirm` は利用者が明示確認すれば扱えるメールアドレス等であり、
    # 会話履歴や回答から一律に消さない。公開経路では `block` の秘密候補だけを
    # 決定論的に置換する。
    findings = scan.get("findings")
    if not isinstance(findings, list):
        return scrubbed
    for finding in reversed(findings):
        if not isinstance(finding, dict) or finding.get("severity") != "block":
            continue
        start = finding.get("start")
        end = finding.get("end")
        rule_id = finding.get("rule_id")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or not isinstance(rule_id, str)
            or not (0 <= start < end <= len(scrubbed))
        ):
            continue
        marker = f"⟪REDACTED:{rule_id}⟫"
        scrubbed = scrubbed[:start] + marker + scrubbed[end:]
    return scrubbed
