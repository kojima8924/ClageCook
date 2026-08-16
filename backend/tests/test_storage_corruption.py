# -*- coding: utf-8 -*-
"""破損した会話JSONを黙って隠さず、明示して止めることの回帰test(issue #18)。"""

import asyncio
import uuid
from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

import attachments
import config
import main
import storage
from storage import ConversationCorrupt, ConversationStore, RequestIndexIncomplete


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


def _corrupt_file(tmp_path, conversation_id=None):
    conversation_id = conversation_id or str(uuid.uuid4())
    path = tmp_path / f"{conversation_id}.json"
    path.write_text("{ this is not json", encoding="utf-8")
    return conversation_id, path


def test_scan_all_reports_corrupt_files_and_leaves_them_in_place(tmp_path):
    store = ConversationStore(tmp_path)
    healthy = store.create("生きている会話")
    corrupt_id, corrupt_path = _corrupt_file(tmp_path)

    conversations, corrupt = store.scan_all()

    assert [item["id"] for item in conversations] == [healthy["id"]]
    assert len(corrupt) == 1
    assert corrupt[0]["id"] == corrupt_id
    assert corrupt[0]["reason"] == "invalid_json"
    assert store.corrupt_ids() == (corrupt_id,)
    # 正本を勝手に動かさない(隔離ディレクトリへ移動しない)。
    assert corrupt_path.is_file()


def test_load_distinguishes_corrupt_from_missing(tmp_path):
    store = ConversationStore(tmp_path)
    corrupt_id, _ = _corrupt_file(tmp_path)

    with pytest.raises(ConversationCorrupt) as corrupt_error:
        store.load(corrupt_id)
    assert corrupt_error.value.reason == "invalid_json"

    with pytest.raises(storage.ConversationNotFound):
        store.load(str(uuid.uuid4()))


def test_id_mismatch_is_corrupt_not_missing(tmp_path):
    store = ConversationStore(tmp_path)
    other_id = str(uuid.uuid4())
    path = tmp_path / f"{other_id}.json"
    path.write_text('{"id": "not-the-file-name", "turns": []}', encoding="utf-8")

    with pytest.raises(ConversationCorrupt) as corrupt_error:
        store.load(other_id)
    assert corrupt_error.value.reason == "id_mismatch"


def test_unknown_request_id_is_refused_while_index_is_incomplete(tmp_path):
    """indexから落ちたrequest_idを『新規』と誤判定して再課金しない。"""
    store = ConversationStore(tmp_path)
    corrupt_id, corrupt_path = _corrupt_file(tmp_path)
    # 破損ファイルはinstance生成後に作ってもよい(scanは必要時に走り直す)。
    store._rebuild_request_index()

    with pytest.raises(RequestIndexIncomplete) as error:
        store.find_conversation_by_request_id("some-request-id")
    assert error.value.corrupt_ids == (corrupt_id,)

    # 破損ファイルを退避すれば、追加操作なしで通常動作へ戻る。
    corrupt_path.unlink()
    assert store.find_conversation_by_request_id("some-request-id") is None


def test_known_request_id_still_resolves_while_another_file_is_corrupt(tmp_path):
    store = ConversationStore(tmp_path)
    conversation = store.create("健全")
    conversation["turns"] = [{"request_id": "kept-request", "status": "completed"}]
    store.save(conversation)
    _corrupt_file(tmp_path)
    store._rebuild_request_index()

    found = store.find_conversation_by_request_id("kept-request")

    assert found is not None
    assert found[1]["request_id"] == "kept-request"


def test_corrupt_conversation_api_returns_422_not_404(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    corrupt_id, _ = _corrupt_file(tmp_path)
    client = TestClient(main.app)

    response = client.get(f"/api/conversations/{corrupt_id}")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "conversation_corrupt"
    assert detail["conversation_id"] == corrupt_id

    missing = client.get(f"/api/conversations/{uuid.uuid4()}")
    assert missing.status_code == 404


def test_chat_refuses_to_start_while_request_index_is_incomplete(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    _corrupt_file(tmp_path)
    main.store._rebuild_request_index()
    client = TestClient(main.app)

    response = client.post(
        "/api/chat",
        json={"message": "こんにちは", "request_id": "brand-new-request"},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "request_index_incomplete"
    assert detail["corrupt_conversation_ids"]
