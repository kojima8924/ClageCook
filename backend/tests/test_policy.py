# -*- coding: utf-8 -*-

import pytest

from policy import scan_text


@pytest.mark.parametrize(
    ("rule_id", "secret"),
    [
        ("anthropic_api_key", "sk-ant-" + "a" * 30),
        ("openai_api_key", "sk-proj-" + "b" * 30),
        ("google_api_key", "AIza" + "C" * 35),
        ("xai_api_key", "xai-" + "d" * 30),
        ("github_token", "ghp_" + "e" * 30),
        ("github_fine_grained_token", "github_pat_" + "e" * 30),
        ("aws_access_key", "AKIA" + "F" * 16),
        ("bearer_token", "Bearer " + "g" * 30),
        ("private_key", "-----BEGIN PRIVATE KEY-----"),
    ],
)
def test_known_secret_patterns_are_blocked_and_redacted(rule_id, secret):
    result = scan_text(f"設定値は {secret} です")

    assert result["action"] == "block"
    assert any(item["rule_id"] == rule_id for item in result["findings"])
    assert secret not in result["redacted_text"]
    assert all("value" not in item for item in result["findings"])


def test_assigned_vendor_token_is_blocked_without_returning_value():
    value = "opaque_token_value_123456789"
    result = scan_text(f"CLAGE_AUTH_TOKEN={value}")

    assert result["action"] == "block"
    assert result["findings"] == [
        {
            "rule_id": "assigned_secret",
            "label": "環境変数へ設定された秘密値らしい文字列",
            "severity": "block",
            "start": len("CLAGE_AUTH_TOKEN="),
            "end": len("CLAGE_AUTH_TOKEN=") + len(value),
        }
    ]
    assert value not in result["redacted_text"]


def test_contact_details_require_confirmation_but_do_not_claim_truth():
    result = scan_text("連絡先 foo@example.com / +81 90-1234-5678")

    assert result["action"] == "confirm"
    assert {item["rule_id"] for item in result["findings"]} == {
        "email_address",
        "phone_number",
    }
    assert "保証するものではありません" in result["disclaimer"]


def test_normal_question_is_allowed_unchanged():
    text = "4つの回答の共通点と相違点を整理してください。"
    result = scan_text(text)

    assert result["action"] == "allow"
    assert result["findings"] == []
    assert result["redacted_text"] == text


def test_short_placeholders_are_not_mistaken_for_credentials():
    text = "OPENAI_API_KEY=your-key and Bearer example"

    assert scan_text(text)["action"] == "allow"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AWS_SECRET_ACCESS_KEY", "AbCdEf0123456789AbCdEf0123456789AbCdEf01"),
        ("DATABASE_URL", "postgresql://user:secret@db.example/app"),
        ("APP_PASSWORD", "correct-horse-battery-staple"),
        ("SERVICE_SECRET", "opaque-secret-value-12345"),
    ],
)
def test_generic_assigned_credentials_are_blocked_and_value_is_removed(
    name, value
):
    result = scan_text(f"{name}={value}")

    assert result["action"] == "block"
    assert value not in result["redacted_text"]
    assert result["findings"][0]["rule_id"] == "generic_assigned_secret"


def test_basic_auth_header_is_blocked_without_echoing_credentials():
    encoded = "dXNlcjpwYXNzd29yZC0xMjM0NTY="
    result = scan_text(f"Authorization: Basic {encoded}")

    assert result["action"] == "block"
    assert encoded not in result["redacted_text"]


def test_overlapping_specific_and_assignment_rules_emit_one_finding():
    secret = "sk-proj-" + "z" * 32
    result = scan_text(f"OPENAI_API_KEY={secret}")

    assert result["action"] == "block"
    assert len(result["findings"]) == 1
    assert result["findings"][0]["rule_id"] == "openai_api_key"
    assert secret not in result["redacted_text"]


@pytest.mark.parametrize("with_footer", [True, False])
def test_private_key_redaction_removes_the_entire_pem_body(with_footer):
    body = "super-secret-private-key-material"
    text = f"before\n-----BEGIN PRIVATE KEY-----\n{body}\n"
    if with_footer:
        text += "-----END PRIVATE KEY-----\nafter"

    result = scan_text(text)

    assert result["action"] == "block"
    assert body not in result["redacted_text"]
    assert "BEGIN PRIVATE KEY" not in result["redacted_text"]
    if with_footer:
        assert result["redacted_text"].endswith("after")
