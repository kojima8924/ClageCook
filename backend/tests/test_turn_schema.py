# -*- coding: utf-8 -*-
"""schema_version 2: event_log廃止 と status単一ソース化の回帰test。"""

import asyncio
import json
from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

import attachments
import config
import main
import storage
import turn_state
from storage import ConversationSchemaUnsupported, ConversationStore


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


def _events(response):
    events = []
    for block in response.text.split("\n\n"):
        name = payload = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
        if name is not None:
            events.append((name, payload))
    return events


def test_saved_turns_do_not_carry_event_log_or_derived_flags(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)

    response = client.post(
        "/api/chat",
        json={"message": "schema", "request_id": "schema-request"},
    )
    assert response.status_code == 200

    conversation_id = response.headers["x-conversation-id"]
    raw = json.loads((tmp_path / f"{conversation_id}.json").read_text(encoding="utf-8"))

    assert raw["schema_version"] == storage.SCHEMA_VERSION == 2
    turn = raw["turns"][0]
    assert turn["status"] == "completed"
    assert "event_log" not in turn
    for flag in turn_state.DERIVED_TURN_FLAGS:
        assert flag not in turn
    # 完了turnにもresume_requestが残る(以前は完了時にだけ消えていた)。
    assert turn["resume_request"]["tier"] == "balanced"


def test_api_still_exposes_derived_flags_computed_from_status(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    conversation = main.store.create("derived")
    conversation["turns"] = [
        {"request_id": "t-cancelled", "status": "cancelled", "answers": {}},
        {"request_id": "t-interrupted", "status": "interrupted", "answers": {}},
        {"request_id": "t-failed", "status": "failed", "answers": {}},
        {"request_id": "t-completed", "status": "completed", "answers": {}},
    ]
    main.store.save(conversation)

    turns = client.get(f"/api/conversations/{conversation['id']}").json()["turns"]

    by_id = {turn["request_id"]: turn for turn in turns}
    assert by_id["t-cancelled"]["cancelled"] is True
    assert by_id["t-cancelled"]["failed"] is False
    assert by_id["t-interrupted"]["interrupted"] is True
    assert by_id["t-interrupted"]["failed"] is True
    assert by_id["t-failed"]["failed"] is True
    assert by_id["t-failed"]["interrupted"] is False
    assert by_id["t-completed"] == {
        **by_id["t-completed"],
        "cancelled": False,
        "failed": False,
        "interrupted": False,
    }


def test_replay_reflects_regenerated_answer_not_a_stale_event_log(
    tmp_path,
    monkeypatch,
):
    """再生成後のreplayが、再生成前の古い回答を返さないこと。"""
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)

    first = client.post(
        "/api/chat",
        json={
            "message": "元の質問",
            "request_id": "replay-consistency",
            "providers": ["claude"],
        },
    )
    assert first.status_code == 200
    conversation_id = first.headers["x-conversation-id"]

    # 保存済みの回答を「再生成された結果」に見立てて差し替える。
    conversation = main.store.load(conversation_id)
    turn = conversation["turns"][0]
    turn["answers"]["claude"]["text"] = "再生成された新しい回答"
    turn["synthesis"]["text"] = "再生成された新しい統合"
    main.store.save(conversation)

    monkeypatch.setattr(main, "_registry", main.RunRegistry())
    replay = client.post(
        "/api/chat",
        json={
            "message": "元の質問",
            "request_id": "replay-consistency",
            "providers": ["claude"],
        },
    )

    events = dict(_events(replay))
    assert events["answer"]["text"] == "再生成された新しい回答"
    assert events["synthesis"]["text"] == "再生成された新しい統合"
    # GET /api/conversations の内容と食い違わない。
    stored = client.get(f"/api/conversations/{conversation_id}").json()
    assert stored["turns"][0]["answers"]["claude"]["text"] == "再生成された新しい回答"


def test_schema_version_1_is_migrated_on_read(tmp_path):
    store = ConversationStore(tmp_path)
    conversation = store.create("legacy")
    path = tmp_path / f"{conversation['id']}.json"
    legacy = json.loads(path.read_text(encoding="utf-8"))
    legacy["schema_version"] = 1
    legacy["turns"] = [
        {
            "request_id": "legacy-turn",
            "status": "cancelled",
            "cancelled": True,
            "failed": False,
            "interrupted": False,
            "event_log": [{"event": "answer", "data": {"text": "古い記録"}}],
            "answers": {},
        }
    ]
    path.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = store.load(conversation["id"])

    assert loaded["schema_version"] == 2
    turn = loaded["turns"][0]
    assert turn["status"] == "cancelled"
    assert "event_log" not in turn
    assert "cancelled" not in turn


def test_legacy_turn_without_status_recovers_it_from_derived_flags(tmp_path):
    store = ConversationStore(tmp_path)
    conversation = store.create("legacy")
    path = tmp_path / f"{conversation['id']}.json"
    legacy = json.loads(path.read_text(encoding="utf-8"))
    legacy["schema_version"] = 1
    legacy["turns"] = [{"request_id": "no-status", "interrupted": True, "answers": {}}]
    path.write_text(json.dumps(legacy), encoding="utf-8")

    turn = store.load(conversation["id"])["turns"][0]

    assert turn["status"] == "interrupted"


def test_unknown_schema_version_is_refused_not_misread(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    conversation = main.store.create("future")
    path = tmp_path / f"{conversation['id']}.json"
    future = json.loads(path.read_text(encoding="utf-8"))
    future["schema_version"] = 99
    path.write_text(json.dumps(future), encoding="utf-8")

    with pytest.raises(ConversationSchemaUnsupported):
        main.store.load(conversation["id"])

    response = TestClient(main.app).get(f"/api/conversations/{conversation['id']}")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "conversation_schema_unsupported"
    assert detail["schema_version"] == 99

    # 一覧からは「破損」として件数報告され、黙って消えない。
    _conversations, corrupt = main.store.scan_all()
    assert corrupt[0]["reason"] == "unsupported_schema_version"
