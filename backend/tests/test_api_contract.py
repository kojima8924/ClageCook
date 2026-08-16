# -*- coding: utf-8 -*-
"""レスポンス封筒とOpenAPI契約のtest。"""

import asyncio
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


def test_conversation_list_is_an_items_envelope_with_corruption_metadata(
    client,
    tmp_path,
):
    client.post("/api/conversations")
    (tmp_path / "11111111-1111-4111-8111-111111111111.json").write_text(
        "{ broken", encoding="utf-8"
    )

    payload = client.get("/api/conversations").json()

    assert isinstance(payload, dict)
    assert len(payload["items"]) == 1
    assert payload["corrupt_count"] == 1
    assert payload["corrupt"][0]["reason"] == "invalid_json"


def test_search_returns_the_same_items_envelope(client):
    conversation = client.post("/api/conversations").json()
    renamed = client.patch(
        f"/api/conversations/{conversation['id']}",
        json={"title": "検索対象タイトル"},
    )
    assert renamed.status_code == 200

    payload = client.post(
        "/api/conversations/search",
        json={"q": "検索対象タイトル", "limit": 10},
    ).json()

    assert payload["query"] == "検索対象タイトル"
    assert payload["items"][0]["id"] == conversation["id"]


def test_export_format_is_a_query_parameter_not_a_path_extension(client):
    conversation = client.post("/api/conversations").json()
    base = f"/api/conversations/{conversation['id']}/export"

    assert client.get(base).status_code == 200
    assert client.get(f"{base}?format=json").headers["content-type"].startswith(
        "application/json"
    )
    assert client.get(f"{base}?format=md").headers["content-type"].startswith(
        "text/markdown"
    )
    assert client.get(f"{base}?format=zip").headers["content-type"] == (
        "application/zip"
    )
    # 旧URLは残さない(同じリソースの表現違いを別URLにしない)。
    assert client.get(f"{base}.md").status_code == 404
    assert client.get(f"{base}?format=xml").status_code == 422


def test_response_model_does_not_drop_conversation_fields(client):
    """response_modelで宣言していないキーを黙って落とさないこと。"""
    conversation = main.store.create("契約")
    conversation["turns"] = [
        {
            "request_id": "contract-turn",
            "status": "completed",
            "message": "質問",
            "answers": {"claude": {"ok": True, "text": "回答"}},
            "synthesis": {"ok": True, "text": "統合"},
            "insights": {},
            "execution_snapshot": {"providers": {"claude": "mock"}},
            "attempts": [{"attempt_id": "a1", "status": "completed"}],
            "active_attempts": {"answer:claude": "a1"},
            "synthesis_stale": False,
            "budget_reservation": {"state": "reserved"},
        }
    ]
    main.store.save(conversation)

    turn = client.get(f"/api/conversations/{conversation['id']}").json()["turns"][0]

    for key in (
        "execution_snapshot",
        "attempts",
        "active_attempts",
        "synthesis_stale",
        "budget_reservation",
    ):
        assert key in turn, f"{key} was dropped by response_model"
    assert turn["answers"]["claude"]["text"] == "回答"


def test_openapi_declares_response_and_error_schemas(client):
    schema = client.get("/openapi.json").json()

    components = schema["components"]["schemas"]
    assert "ErrorResponse" in components
    assert "ConversationListResponse" in components
    assert "Conversation" in components

    list_route = schema["paths"]["/api/conversations"]["get"]
    ok_schema = list_route["responses"]["200"]["content"]["application/json"]["schema"]
    assert ok_schema["$ref"].endswith("ConversationListResponse")
    error_schema = list_route["responses"]["404"]["content"]["application/json"][
        "schema"
    ]
    assert error_schema["$ref"].endswith("ErrorResponse")
