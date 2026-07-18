import pytest

import storage
from scrubbing import scrub_public_data
from storage import ConversationStore


def test_conversation_crud_search_and_atomic_save(tmp_path):
    store = ConversationStore(tmp_path)
    conversation = store.create("First question")
    conversation["turns"].append(
        {
            "request_id": "request-1",
            "message": "ocean question",
            "answers": {"claude": {"ok": True, "text": "blue ocean"}},
            "synthesis": {"ok": True, "text": "ocean summary"},
        }
    )
    store.save(conversation)

    loaded = store.load(conversation["id"])
    assert loaded["turns"][0]["synthesis"]["text"] == "ocean summary"
    assert store.find_turn_by_request_id(loaded, "request-1") is not None
    found = store.find_conversation_by_request_id("request-1")
    assert found is not None
    assert found[0]["id"] == conversation["id"]
    assert found[1]["request_id"] == "request-1"
    assert store.search("ocean summary")[0]["id"] == conversation["id"]
    assert list(tmp_path.glob("*.tmp")) == []

    store.delete(conversation["id"])
    assert store.list() == []


def test_create_branch_copies_only_immutable_prefix(tmp_path):
    store = ConversationStore(tmp_path)
    parent = store.create("parent")
    parent["turns"] = [
        {"request_id": "turn-one", "message": "one", "status": "completed"},
        {"request_id": "turn-two", "message": "two", "status": "completed"},
    ]
    store.save(parent)
    branch = store.create_branch(
        parent,
        before_turn_index=1,
        parent_turn_request_id="turn-two",
    )
    assert branch["id"] != parent["id"]
    assert [turn["message"] for turn in branch["turns"]] == ["one"]
    assert branch["branch"]["parent_turn_request_id"] == "turn-two"
    branch["turns"][0]["message"] = "changed"
    assert store.load(parent["id"])["turns"][0]["message"] == "one"
def test_failed_atomic_replace_preserves_previous_file(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    conversation = store.create("original")
    original = store.load(conversation["id"])
    previous_updated_at = conversation["updated_at"]
    conversation["title"] = "must not replace"

    def fail_replace(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(storage.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        store.save(conversation)

    assert store.load(conversation["id"])["title"] == original["title"]
    assert conversation["updated_at"] == previous_updated_at
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_flushes_file_before_replace(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    conversation = store.create("fsync")
    calls = []
    real_fsync = storage.os.fsync

    def record_fsync(fd):
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(storage.os, "fsync", record_fsync)
    conversation["title"] = "durable"
    store.save(conversation)

    assert calls
    assert store.load(conversation["id"])["title"] == "durable"


def test_save_sanitizer_updates_memory_and_disk_without_secret(tmp_path):
    secret = "opaque-storage-secret"
    store = ConversationStore(
        tmp_path,
        sanitizer=lambda value: scrub_public_data(
            value,
            known_secrets=[secret],
        ),
    )
    conversation = store.create(f"title {secret}")
    conversation["turns"].append(
        {
            "message": "safe",
            "answers": {"claude": {"ok": True, "text": secret}},
        }
    )

    store.save(conversation)

    encoded_memory = str(conversation)
    encoded_disk = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")
    assert secret not in encoded_memory
    assert secret not in encoded_disk
    assert secret not in str(store.load(conversation["id"]))


def test_recover_interrupted_turns_marks_only_running_without_reexecution(tmp_path):
    store = ConversationStore(tmp_path)
    conversation = store.create("recover")
    conversation["turns"] = [
        {
            "request_id": "running-request",
            "message": "unfinished",
            "status": "running",
            "answers": {
                "chatgpt": {
                    "ok": True,
                    "text": "partial answer",
                    "usage": {"total_tokens": 12},
                }
            },
            "synthesis": {"ok": False, "pending": True},
        },
        {
            "request_id": "completed-request",
            "message": "done",
            "status": "completed",
            "answers": {},
            "synthesis": {"ok": True, "text": "complete"},
        },
    ]
    store.save(conversation)

    assert store.recover_interrupted_turns() == 1
    saved = store.load(conversation["id"])
    interrupted, completed = saved["turns"]
    assert interrupted["status"] == "interrupted"
    assert interrupted["interrupted"] is True
    assert interrupted["failed"] is True
    assert interrupted["usage_may_be_incomplete"] is True
    assert interrupted["answers"]["chatgpt"]["usage"]["total_tokens"] == 12
    assert "サーバー停止" in interrupted["synthesis"]["error"]
    assert completed["status"] == "completed"
    assert store.recover_interrupted_turns() == 0


def test_recover_interrupted_regeneration_attempt_without_restarting_it(tmp_path):
    store = ConversationStore(tmp_path)
    conversation = store.create("attempt")
    conversation["turns"] = [
        {
            "request_id": "completed-turn",
            "status": "completed",
            "attempts": [
                {
                    "attempt_id": "pending-attempt",
                    "status": "dispatching",
                    "usage_may_be_incomplete": False,
                }
            ],
        }
    ]
    store.save(conversation)
    assert store.recover_interrupted_turns() == 1
    recovered = store.load(conversation["id"])["turns"][0]
    assert recovered["status"] == "completed"
    assert recovered["attempts"][0]["status"] == "interrupted"
    assert recovered["attempts"][0]["usage_may_be_incomplete"] is True
