# -*- coding: utf-8 -*-
"""HTTPエラーの封筒が {code, message} に統一されていることのtest。"""

import asyncio
import uuid
from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

import attachments
import config
import main
from storage import ConversationStore


def _reset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main,
        "store",
        ConversationStore(tmp_path, sanitizer=main._scrub_public),
    )
    monkeypatch.setattr(main, "attachment_store", attachments.AttachmentStore(tmp_path))
    monkeypatch.setattr(main, "_registry", main.RunRegistry())
    monkeypatch.setattr(main, "_rate_limiter", main.SlidingWindowLimiter(100))
    monkeypatch.setattr(main, "_run_slots", asyncio.Semaphore(8))
    monkeypatch.setattr(main, "_conversation_locks", defaultdict(asyncio.Lock))
    monkeypatch.setattr(main, "_active_conversation_runs", {})
    monkeypatch.setattr(main, "_active_conversation_guard", asyncio.Lock())
    monkeypatch.setattr(config, "MOCK_DELAY_SEC", 0.0)
    monkeypatch.setattr(config, "AUTH_TOKEN", "")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    return TestClient(main.app)


def _detail(response):
    payload = response.json()
    assert "detail" in payload, payload
    detail = payload["detail"]
    assert isinstance(detail, dict), f"detail must be structured, got {detail!r}"
    assert isinstance(detail.get("code"), str) and detail["code"]
    assert isinstance(detail.get("message"), str) and detail["message"]
    return detail


def test_missing_conversation_uses_structured_detail(client):
    response = client.get(f"/api/conversations/{uuid.uuid4()}")

    assert response.status_code == 404
    assert _detail(response)["code"] == "conversation_not_found"


def test_malformed_conversation_id_is_also_structured(client):
    response = client.get("/api/conversations/not-a-uuid")

    assert response.status_code == 404
    assert _detail(response)["code"] == "conversation_not_found"


def test_unauthorized_uses_structured_detail(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "AUTH_TOKEN", "secret-token")
    client = TestClient(main.app)

    response = client.get("/api/health")

    assert response.status_code == 401
    assert _detail(response)["code"] == "unauthorized"


def test_invalid_last_event_id_uses_structured_detail(client):
    response = client.post(
        "/api/chat",
        json={"message": "hello"},
        headers={"Last-Event-ID": "not-a-number"},
    )

    assert response.status_code == 400
    assert _detail(response)["code"] == "invalid_last_event_id"


def test_request_id_header_mismatch_uses_structured_detail(client):
    response = client.post(
        "/api/chat",
        json={"message": "hello", "request_id": "request-aaa"},
        headers={"X-Request-ID": "request-bbb"},
    )

    assert response.status_code == 400
    assert _detail(response)["code"] == "request_id_header_mismatch"


def test_missing_turn_uses_structured_detail(client):
    conversation = client.post("/api/conversations").json()

    response = client.post(
        f"/api/conversations/{conversation['id']}/turns/missing-turn/fork",
    )

    assert response.status_code == 404
    assert _detail(response)["code"] == "turn_not_found"


def test_cancel_unknown_run_uses_structured_detail(client):
    response = client.post("/api/runs/unknown-run/cancel")

    assert response.status_code == 404
    assert _detail(response)["code"] == "run_not_found"


def test_reservation_release_uses_structured_detail(client):
    response = client.post(
        "/api/budget/reconciliation/not a valid id/release",
        json={"confirmed_no_unobserved_charge": True},
    )

    assert response.status_code == 404
    assert _detail(response)["code"] == "reservation_not_found"


def test_two_request_id_conflicts_are_machine_distinguishable(client):
    """復旧手順が違う2種の409を、日本語文字列マッチなしで区別できる。"""
    first = client.post(
        "/api/chat",
        json={"message": "最初の質問", "request_id": "shared-request"},
    )
    assert first.status_code == 200
    conversation_id = first.headers["X-Conversation-ID"]

    # 同じrequest_idを別の会話IDで送る → 会話IDを付け直せば復旧できる。
    other = client.post("/api/conversations").json()
    wrong_conversation = client.post(
        "/api/chat",
        json={
            "message": "最初の質問",
            "request_id": "shared-request",
            "conversation_id": other["id"],
        },
    )
    assert wrong_conversation.status_code == 409
    assert (
        _detail(wrong_conversation)["code"] == "request_id_conflict_conversation"
    )

    # 同じrequest_idを違う内容で送る → 新しいrequest_idが必要。
    wrong_content = client.post(
        "/api/chat",
        json={
            "message": "別の質問",
            "request_id": "shared-request",
            "conversation_id": conversation_id,
        },
    )
    assert wrong_content.status_code == 409
    assert _detail(wrong_content)["code"] == "request_id_conflict_fingerprint"


def test_validation_error_keeps_the_same_envelope(client):
    response = client.post("/api/chat", json={})

    assert response.status_code == 422
    assert _detail(response)["code"] == "invalid_request"
