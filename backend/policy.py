# -*- coding: utf-8 -*-
"""外部送信前に秘密らしい文字列を検出する決定論的ローカルスキャナ。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Pattern


Severity = Literal["confirm", "block"]


@dataclass(frozen=True, slots=True)
class _Rule:
    rule_id: str
    label: str
    severity: Severity
    pattern: Pattern[str]
    secret_group: int = 0


_RULES = (
    _Rule(
        "private_key",
        "秘密鍵ブロック",
        "block",
        re.compile(
            r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----[\s\S]*?"
            r"(?:-----END(?: [A-Z0-9]+)? PRIVATE KEY-----|\Z)",
            re.IGNORECASE,
        ),
    ),
    _Rule(
        "anthropic_api_key",
        "Anthropic APIキーらしい文字列",
        "block",
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b"),
    ),
    _Rule(
        "openai_api_key",
        "OpenAI APIキーらしい文字列",
        "block",
        re.compile(
            r"\b(?:sk-(?:proj|svcacct)-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9]{32,})\b"
        ),
    ),
    _Rule(
        "google_api_key",
        "Google APIキーらしい文字列",
        "block",
        re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    ),
    _Rule(
        "google_aq_api_key",
        "Google APIキーらしいAQ形式の文字列",
        "block",
        re.compile(r"\bAQ\.[0-9A-Za-z_-]{20,}\b"),
    ),
    _Rule(
        "xai_api_key",
        "xAI APIキーらしい文字列",
        "block",
        re.compile(r"\bxai-[A-Za-z0-9_-]{16,}\b", re.IGNORECASE),
    ),
    _Rule(
        "github_token",
        "GitHubトークンらしい文字列",
        "block",
        re.compile(r"\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{20,}\b", re.IGNORECASE),
    ),
    _Rule(
        "github_fine_grained_token",
        "GitHub fine-grained tokenらしい文字列",
        "block",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", re.IGNORECASE),
    ),
    _Rule(
        "aws_access_key",
        "AWSアクセスキーIDらしい文字列",
        "block",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ),
    _Rule(
        "bearer_token",
        "Bearerトークンらしい文字列",
        "block",
        re.compile(r"\bBearer\s+([A-Za-z0-9._~+/-]{20,}={0,2})", re.IGNORECASE),
        secret_group=1,
    ),
    _Rule(
        "assigned_secret",
        "環境変数へ設定された秘密値らしい文字列",
        "block",
        re.compile(
            r"\b(?:OPENAI|ANTHROPIC|GEMINI|GOOGLE|XAI|CLAUDE|GROK|CLAGE)"
            r"(?:_[A-Z0-9]+)*_(?:API_)?(?:KEY|TOKEN)\s*[:=]\s*[\"']?"
            r"([A-Za-z0-9._~+/-]{16,}={0,2})",
            re.IGNORECASE,
        ),
        secret_group=1,
    ),
    _Rule(
        "generic_assigned_secret",
        "秘密値用の変数へ設定された文字列",
        "block",
        re.compile(
            r"\b(?:AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|DATABASE_URL|"
            r"[A-Z][A-Z0-9_]{1,64}(?:PASSWORD|PASSWD|SECRET|SECRET_KEY|"
            r"PRIVATE_KEY|ACCESS_TOKEN))\s*[:=]\s*[\"']?"
            r"([^\s\"']{8,})",
            re.IGNORECASE,
        ),
        secret_group=1,
    ),
    _Rule(
        "basic_auth",
        "Basic認証情報らしい文字列",
        "block",
        re.compile(r"\bBasic\s+([A-Za-z0-9+/]{16,}={0,2})", re.IGNORECASE),
        secret_group=1,
    ),
    _Rule(
        "email_address",
        "メールアドレスらしい文字列",
        "confirm",
        re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"),
    ),
    _Rule(
        "phone_number",
        "電話番号らしい文字列",
        "confirm",
        re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{8,}\d)(?!\d)"),
    ),
)


def scan_text(text: str) -> dict:
    """秘密・個人情報らしい箇所を検出し、生値を含まない結果を返す。

    この結果は真偽判定ではなく、透明なパターン一致だけを表す。`block` は
    APIキーや秘密鍵など外部送信すべきでない候補、`confirm` は利用者が文脈を
    確認すべき候補である。
    """

    candidates: list[dict] = []
    for priority, rule in enumerate(_RULES):
        for match in rule.pattern.finditer(text):
            start, end = match.span(rule.secret_group)
            if start == end:
                continue
            candidates.append(
                {
                    "rule_id": rule.rule_id,
                    "label": rule.label,
                    "severity": rule.severity,
                    "start": start,
                    "end": end,
                    "_priority": priority,
                }
            )

    findings = _without_overlaps(candidates)
    redacted = text
    for finding in reversed(findings):
        marker = f"⟪REDACTED:{finding['rule_id']}⟫"
        redacted = redacted[: finding["start"]] + marker + redacted[finding["end"] :]

    public_findings = [
        {key: value for key, value in finding.items() if not key.startswith("_")}
        for finding in findings
    ]
    action = (
        "block"
        if any(item["severity"] == "block" for item in public_findings)
        else "confirm"
        if public_findings
        else "allow"
    )
    return {
        "version": "local-patterns-v1",
        "action": action,
        "findings": public_findings,
        "redacted_text": redacted,
        "disclaimer": (
            "ローカルのパターン一致結果です。秘密・個人情報の有無を保証するものではありません。"
        ),
    }


def _without_overlaps(candidates: list[dict]) -> list[dict]:
    """重複候補は重大度、具体的な規則、長い一致の順で1件へ畳む。"""

    severity_rank = {"block": 0, "confirm": 1}
    ordered = sorted(
        candidates,
        key=lambda item: (
            severity_rank[item["severity"]],
            item["_priority"],
            -(item["end"] - item["start"]),
            item["start"],
        ),
    )
    selected: list[dict] = []
    for item in ordered:
        if any(
            item["start"] < existing["end"] and existing["start"] < item["end"]
            for existing in selected
        ):
            continue
        selected.append(item)
    return sorted(selected, key=lambda item: (item["start"], item["end"]))
