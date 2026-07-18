# -*- coding: utf-8 -*-

from datetime import datetime, timezone

import httpx
import pytest

import admin_telemetry
import config


def _configure(monkeypatch, *, enabled=True):
    monkeypatch.setattr(config, "ADMIN_TELEMETRY_ENABLED", enabled)
    monkeypatch.setattr(config, "ADMIN_TELEMETRY_LOOKBACK_DAYS", 7)
    monkeypatch.setattr(config, "ADMIN_TELEMETRY_CACHE_SEC", 300)
    monkeypatch.setattr(config, "ANTHROPIC_ADMIN_KEY", "anthropic-admin-secret")
    monkeypatch.setattr(config, "OPENAI_ADMIN_KEY", "openai-admin-secret")
    monkeypatch.setattr(config, "XAI_MANAGEMENT_KEY", "xai-management-secret")
    monkeypatch.setattr(config, "XAI_TEAM_ID", "")
    admin_telemetry.reset_cache()


@pytest.mark.asyncio
async def test_disabled_admin_telemetry_performs_no_network(monkeypatch):
    _configure(monkeypatch, enabled=False)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await admin_telemetry.snapshot(client=client)

    assert result["enabled"] is False
    assert calls == 0
    assert all(
        item["status"] in {"disabled", "unsupported"}
        for item in result["providers"]
    )


@pytest.mark.asyncio
async def test_admin_telemetry_normalizes_three_provider_management_apis(monkeypatch):
    _configure(monkeypatch)
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        path = request.url.path
        if path == "/v1/organizations/usage_report/messages":
            assert request.headers["x-api-key"] == "anthropic-admin-secret"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "results": [
                                {
                                    "uncached_input_tokens": 100,
                                    "cache_read_input_tokens": 20,
                                    "cache_creation": {
                                        "ephemeral_5m_input_tokens": 10,
                                        "ephemeral_1h_input_tokens": 5,
                                    },
                                    "output_tokens": 30,
                                    "server_tool_use": {"web_search_requests": 2},
                                }
                            ]
                        }
                    ],
                    "has_more": False,
                },
            )
        if path == "/v1/organizations/cost_report":
            return httpx.Response(
                200,
                json={
                    "data": [{"results": [{"amount": "123.45", "currency": "USD"}]}],
                    "has_more": False,
                },
            )
        if path == "/v1/organization/usage/completions":
            assert request.headers["authorization"] == "Bearer openai-admin-secret"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "results": [
                                {
                                    "input_tokens": 200,
                                    "output_tokens": 40,
                                    "input_cached_tokens": 50,
                                    "num_model_requests": 3,
                                }
                            ]
                        }
                    ],
                    "has_more": False,
                },
            )
        if path == "/v1/organization/costs":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "results": [
                                {"amount": {"value": 2.75, "currency": "usd"}}
                            ]
                        }
                    ],
                    "has_more": False,
                },
            )
        if path == "/auth/management-keys/validation":
            assert request.headers["authorization"] == "Bearer xai-management-secret"
            return httpx.Response(
                200,
                json={"scope": "SCOPE_TEAM", "scopeId": "team-from-validation"},
            )
        if path.endswith("/usage"):
            assert request.method == "POST"
            return httpx.Response(
                200,
                json={
                    "timeSeries": [
                        {"dataPoints": [{"values": [0.75]}, {"values": [0.25]}]}
                    ],
                    "limitReached": False,
                },
            )
        if path.endswith("/prepaid/balance"):
            return httpx.Response(200, json={"total": {"val": "-1000"}})
        if path.endswith("/postpaid/invoice/preview"):
            return httpx.Response(
                200,
                json={
                    "coreInvoice": {"totalWithCorr": {"val": "250"}},
                    "effectiveSpendingLimit": "20000",
                    "billingCycle": {"year": 2026, "month": 7},
                },
            )
        if path.endswith("/postpaid/spending-limits"):
            return httpx.Response(
                200,
                json={
                    "spendingLimits": {
                        "effectiveSl": {"val": "20000"},
                        "effectiveHardSl": {"val": "22500"},
                    }
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await admin_telemetry.snapshot(
            client=client,
            now=datetime(2026, 7, 18, 12, tzinfo=timezone.utc),
        )

    providers = {item["name"]: item for item in result["providers"]}
    assert providers["claude"]["status"] == "ok"
    assert providers["claude"]["usage"]["usage"]["input_tokens"] == 135
    assert providers["claude"]["cost"]["amount_usd"] == "1.2345"
    assert providers["chatgpt"]["usage"]["usage"]["requests"] == 3
    assert providers["chatgpt"]["cost"]["amount_usd"] == "2.75"
    assert providers["gemini"]["status"] == "unsupported"
    assert providers["grok"]["usage"]["amount_usd"] == "1"
    assert providers["grok"]["credit_balance"]["provider_reported_usd"] == "-10"
    assert providers["grok"]["spending_limits"]["effective_soft_usd"] == "200"
    assert ("GET", "/v1/billing/teams/team-from-validation/prepaid/balance") in seen
    assert "anthropic-admin-secret" not in str(result)
    assert "openai-admin-secret" not in str(result)
    assert "xai-management-secret" not in str(result)


@pytest.mark.asyncio
async def test_admin_telemetry_keeps_partial_results_and_sanitizes_errors(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(config, "OPENAI_ADMIN_KEY", "")
    monkeypatch.setattr(config, "XAI_MANAGEMENT_KEY", "")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/usage_report/messages"):
            return httpx.Response(401, json={"error": {"message": "secret body"}})
        return httpx.Response(
            200,
            json={"data": [{"results": [{"amount": "50"}]}], "has_more": False},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await admin_telemetry.snapshot(client=client)

    claude = result["providers"][0]
    assert claude["status"] == "partial"
    assert claude["usage"] == {"status": "error", "error_code": "unauthorized"}
    assert claude["cost"]["amount_usd"] == "0.5"
    assert "secret body" not in str(result)
    assert result["providers"][1]["status"] == "not_configured"


def test_admin_credentials_are_scrub_secrets(monkeypatch):
    _configure(monkeypatch)
    secrets = config.secret_values()
    assert "anthropic-admin-secret" in secrets
    assert "openai-admin-secret" in secrets
    assert "xai-management-secret" in secrets


def test_admin_cache_fingerprint_changes_when_a_key_rotates(monkeypatch):
    _configure(monkeypatch)
    first = admin_telemetry._configuration_fingerprint()
    monkeypatch.setattr(config, "OPENAI_ADMIN_KEY", "different-openai-admin-secret")
    second = admin_telemetry._configuration_fingerprint()
    assert first != second
    assert "different-openai-admin-secret" not in repr(second)
