import io
import json
import zipfile

from fastapi.testclient import TestClient

import attachments
import config
import main
from storage import ConversationStore


def _reset(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        main,
        "store",
        ConversationStore(tmp_path, sanitizer=main._scrub_public),
    )
    monkeypatch.setattr(main, "attachment_store", attachments.AttachmentStore(tmp_path))
    monkeypatch.setattr(config, "AUTH_TOKEN", "")


def test_conversation_memory_uses_revision_and_is_included_in_plan(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    created = client.post("/api/conversations").json()
    conversation_id = created["id"]

    updated = client.patch(
        f"/api/conversations/{conversation_id}/memory",
        json={
            "expected_revision": 0,
            "text": "対象読者は実装担当者。決定事項を箇条書きにする。",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["memory"]["revision"] == 1

    conflict = client.patch(
        f"/api/conversations/{conversation_id}/memory",
        json={"expected_revision": 0, "text": "stale update"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "conversation_memory_conflict"

    plan = client.post(
        "/api/plan",
        json={"message": "設計して", "conversation_id": conversation_id},
    )
    assert plan.status_code == 200
    assert plan.json()["input_envelope"]["memory"] > 0


def test_memory_masks_secret_candidate_before_persistence(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    conversation_id = client.post("/api/conversations").json()["id"]
    candidate = "sk-proj-" + "a" * 80

    response = client.patch(
        f"/api/conversations/{conversation_id}/memory",
        json={"expected_revision": 0, "text": f"key={candidate}"},
    )

    assert response.status_code == 200
    encoded = json.dumps(response.json(), ensure_ascii=False)
    assert candidate not in encoded
    assert response.json()["memory"]["secret_candidates_redacted"] is True


def test_zip_export_contains_portable_json_markdown_and_owned_attachments(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    conversation = client.post("/api/conversations").json()
    conversation_id = conversation["id"]
    uploaded = client.post(
        f"/api/conversations/{conversation_id}/attachments",
        files={"file": ("notes.md", b"portable notes", "text/markdown")},
    )
    assert uploaded.status_code == 200
    attachment = uploaded.json()

    stored = main.store.load(conversation_id)
    stored["title"] = "Portable conference"
    stored["turns"] = [
        {
            "request_id": "portable-turn",
            "message": "current facts",
            "clean_message": "current facts",
            "status": "completed",
            "attachments": [attachment],
            "answers": {
                "chatgpt": {
                    "ok": True,
                    "text": "answer",
                    "citations": [
                        {"title": "Example", "url": "https://example.com/source"}
                    ],
                }
            },
            "synthesis": {"ok": True, "text": "final", "skipped": False},
        }
    ]
    main.store.save(stored)

    response = client.get(f"/api/conversations/{conversation_id}/export?format=zip")

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert {"conversation.json", "conversation.md", "manifest.json"} <= names
        attachment_names = [name for name in names if name.startswith("attachments/")]
        assert len(attachment_names) == 1
        assert archive.read(attachment_names[0]) == b"portable notes"
        markdown = archive.read("conversation.md").decode("utf-8")
        assert "Portable conference" in markdown
        assert "https://example.com/source" in markdown
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["attachments"][0]["included"] is True
    export_dir = tmp_path / ".exports"
    assert not export_dir.exists() or list(export_dir.glob("*.zip")) == []
