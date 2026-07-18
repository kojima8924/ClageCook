# -*- coding: utf-8 -*-

import asyncio
from collections import defaultdict

from fastapi.testclient import TestClient

import config
import main
from storage import ConversationStore


def _reset(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", ConversationStore(tmp_path))
    monkeypatch.setattr(main, "_registry", main.RunRegistry())
    monkeypatch.setattr(main, "_rate_limiter", main.SlidingWindowLimiter(100))
    monkeypatch.setattr(main, "_run_slots", asyncio.Semaphore(8))
    monkeypatch.setattr(main, "_conversation_locks", defaultdict(asyncio.Lock))
    monkeypatch.setattr(config, "AUTH_TOKEN", "")


def test_policy_scan_endpoint_is_local_and_returns_no_secret_value(monkeypatch):
    monkeypatch.setattr(config, "AUTH_TOKEN", "")
    secret = "sk-proj-" + "a" * 32

    response = TestClient(main.app).post(
        "/api/policy/scan", json={"text": f"誤って貼った {secret}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "block"
    assert secret not in body["redacted_text"]
    assert secret not in response.text


def test_chat_policy_block_happens_before_storage_or_provider_call(
    tmp_path, monkeypatch
):
    _reset(tmp_path, monkeypatch)
    secret = "xai-" + "b" * 32

    response = TestClient(main.app).post(
        "/api/chat",
        json={"message": f"この値を確認して {secret}", "request_id": "policy-blocked-1"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "policy_blocked"
    assert detail["plan"]["calls"]["total"] > 0
    assert detail["plan"]["policy"]["action"] == "block"
    assert secret not in response.text
    assert main.store.list() == []


def test_plan_rejects_command_only_or_no_provider(monkeypatch):
    monkeypatch.setattr(config, "AUTH_TOKEN", "")
    client = TestClient(main.app)

    command_only = client.post("/api/plan", json={"message": "!high"})
    assert command_only.status_code == 200
    assert command_only.json()["allowed"] is False
    assert "invalid_request" in command_only.json()["block_reasons"]

    monkeypatch.setattr(config, "active_workers", lambda: [])
    no_provider = client.post("/api/plan", json={"message": "question"})
    assert no_provider.status_code == 200
    assert no_provider.json()["allowed"] is False
    assert "invalid_request" in no_provider.json()["block_reasons"]


def test_live_chat_requires_server_side_billing_and_sensitive_data_ack(
    tmp_path, monkeypatch
):
    _reset(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "LIVE_API_ENABLED", True)
    monkeypatch.setenv("OPENAI_API_KEY", "configured-test-key")
    message = "foo@example.com へ送る文面を作って"
    client = TestClient(main.app)

    missing_both = client.post(
        "/api/chat",
        json={
            "message": message,
            "providers": ["chatgpt"],
            "synthesize": False,
        },
    )
    missing_sensitive = client.post(
        "/api/chat",
        json={
            "message": message,
            "providers": ["chatgpt"],
            "synthesize": False,
            "confirm_live_api": True,
        },
    )

    assert missing_both.status_code == 428
    assert set(missing_both.json()["detail"]["required"]) == {
        "confirm_live_api",
        "confirm_sensitive_data",
    }
    assert missing_sensitive.status_code == 428
    assert missing_sensitive.json()["detail"]["required"] == [
        "confirm_sensitive_data"
    ]
    assert main.store.list() == []
