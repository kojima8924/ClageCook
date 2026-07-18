# -*- coding: utf-8 -*-

import json
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
    monkeypatch.setattr(config, "BUDGET_UTC_OFFSET", "+09:00")
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
            assert request.url.params["starting_at"] == "2026-07-11T00:00:00Z"
            assert request.url.params["ending_at"] == "2026-07-18T12:00:00Z"
            assert request.url.params["bucket_width"] == "1d"
            assert request.url.params["limit"] == "8"
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
            assert request.url.params["starting_at"] == "2026-07-11T00:00:00Z"
            assert request.url.params["ending_at"] == "2026-07-18T12:00:00Z"
            assert request.url.params["bucket_width"] == "1d"
            assert request.url.params["limit"] == "8"
            return httpx.Response(
                200,
                json={
                    "data": [{"results": [{"amount": "123.45", "currency": "USD"}]}],
                    "has_more": False,
                },
            )
        if path == "/v1/organization/usage/completions":
            assert request.headers["authorization"] == "Bearer openai-admin-secret"
            assert int(request.url.params["start_time"]) == int(
                datetime(2026, 7, 11, 15, tzinfo=timezone.utc).timestamp()
            )
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
            body = json.loads(request.content)
            time_range = body["analyticsRequest"]["timeRange"]
            assert time_range["startTime"] == "2026-07-11 15:00:00"
            assert time_range["endTime"] == "2026-07-18 12:00:00"
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
    assert providers["claude"]["window"] == {
        "starting_at": "2026-07-11T00:00:00Z",
        "ending_at": "2026-07-18T12:00:00Z",
        "requested_starting_at": "2026-07-11T15:00:00Z",
        "requested_ending_at": "2026-07-18T12:00:00Z",
        "query_utc_offset": "+00:00",
        "budget_utc_offset": "+09:00",
        "alignment": "provider_utc_day_buckets",
        "bucket_width": "1d",
        "exact_budget_window": False,
        "complete_through": "2026-07-18T00:00:00Z",
    }
    assert providers["chatgpt"]["usage"]["usage"]["requests"] == 3
    assert providers["chatgpt"]["cost"]["amount_usd"] == "2.75"
    assert providers["chatgpt"]["window"]["exact_budget_window"] is True
    assert providers["chatgpt"]["window"]["alignment"] == (
        "requested_budget_window"
    )
    assert providers["gemini"]["status"] == "unsupported"
    assert providers["grok"]["usage"]["amount_usd"] == "1"
    assert providers["grok"]["window"]["exact_budget_window"] is True
    assert providers["grok"]["credit_balance"]["provider_reported_usd"] == "-10"
    assert providers["grok"]["spending_limits"]["effective_soft_usd"] == "200"
    assert result["window"] == {
        "starting_at": "2026-07-11T15:00:00Z",
        "ending_at": "2026-07-18T12:00:00Z",
        "lookback_days": 7,
        "utc_offset": "+09:00",
    }
    assert any("complete_through" in item for item in result["limitations"])
    assert ("GET", "/v1/billing/teams/team-from-validation/prepaid/balance") in seen
    assert "anthropic-admin-secret" not in str(result)
    assert "openai-admin-secret" not in str(result)
    assert "xai-management-secret" not in str(result)


def test_anthropic_daily_window_never_moves_end_into_future():
    requested_start = datetime(2026, 7, 11, 15, tzinfo=timezone.utc)
    requested_end = datetime(2026, 7, 18, 12, 34, tzinfo=timezone.utc)

    query_start, query_end, complete_through = (
        admin_telemetry._anthropic_utc_day_window(
            requested_start,
            requested_end,
        )
    )

    assert query_start == datetime(2026, 7, 11, tzinfo=timezone.utc)
    assert query_end == requested_end
    assert complete_through == datetime(2026, 7, 18, tzinfo=timezone.utc)


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


def test_admin_cache_fingerprint_changes_with_budget_day_offset(monkeypatch):
    _configure(monkeypatch)
    first = admin_telemetry._configuration_fingerprint()
    monkeypatch.setattr(config, "BUDGET_UTC_OFFSET", "+00:00")
    second = admin_telemetry._configuration_fingerprint()

    assert first != second


def test_admin_cache_fingerprint_changes_at_budget_midnight(monkeypatch):
    _configure(monkeypatch)
    before = admin_telemetry._configuration_fingerprint(
        datetime(2026, 7, 18, 14, 59, tzinfo=timezone.utc)
    )
    after = admin_telemetry._configuration_fingerprint(
        datetime(2026, 7, 18, 15, 1, tzinfo=timezone.utc)
    )

    assert before != after


@pytest.mark.asyncio
async def test_admin_cache_does_not_cross_budget_midnight(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(config, "ANTHROPIC_ADMIN_KEY", "")
    monkeypatch.setattr(config, "OPENAI_ADMIN_KEY", "")
    monkeypatch.setattr(config, "XAI_MANAGEMENT_KEY", "")

    before = await admin_telemetry.snapshot(
        now=datetime(2026, 7, 18, 14, 59, tzinfo=timezone.utc)
    )
    after = await admin_telemetry.snapshot(
        now=datetime(2026, 7, 18, 15, 1, tzinfo=timezone.utc)
    )

    assert before["window"]["starting_at"] == "2026-07-11T15:00:00Z"
    assert after["window"]["starting_at"] == "2026-07-12T15:00:00Z"
    assert after["cache"]["hit"] is False
