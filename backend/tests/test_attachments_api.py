# -*- coding: utf-8 -*-

import asyncio
import io
import json
import subprocess
from collections import defaultdict

from fastapi.testclient import TestClient
from pypdf import PdfWriter
import pytest

import attachments
import config
import main
from storage import ConversationStore


def _reset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main,
        "store",
        ConversationStore(tmp_path / "conversations", sanitizer=main._scrub_public),
    )
    monkeypatch.setattr(
        main,
        "attachment_store",
        attachments.AttachmentStore(tmp_path / "conversations"),
    )
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
    event = ""
    for line in response.iter_lines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            events.append((event, json.loads(line.split(":", 1)[1].strip())))
    return events


def test_text_attachment_is_opaque_owned_and_included_in_plan_and_turn(
    tmp_path, monkeypatch
):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    conversation = client.post("/api/conversations").json()
    uploaded = client.post(
        f"/api/conversations/{conversation['id']}/attachments",
        files={"file": ("notes.txt", "添付の根拠です。".encode(), "text/plain")},
    )
    assert uploaded.status_code == 200
    item = uploaded.json()
    assert item["name"] == "notes.txt"
    assert item["text_extractable"] is True
    assert ".attachments" not in json.dumps(item)
    downloaded = client.get(
        f"/api/conversations/{conversation['id']}/attachments/{item['id']}"
    )
    assert downloaded.status_code == 200
    assert downloaded.headers["x-content-type-options"] == "nosniff"

    plan = client.post(
        "/api/plan",
        json={
            "message": "資料を要約して",
            "conversation_id": conversation["id"],
            "attachment_ids": [item["id"]],
        },
    )
    assert plan.status_code == 200
    assert plan.json()["attachments"]["text_included_count"] == 1
    assert plan.json()["input_envelope"]["total"] > len("資料を要約して".encode())

    payload = {
        "message": "資料を要約して",
        "conversation_id": conversation["id"],
        "attachment_ids": [item["id"]],
        "request_id": "attachment-run",
    }
    with client.stream("POST", "/api/chat", json=payload) as response:
        assert response.status_code == 200
        assert _events(response)[-1][0] == "done"
    saved = main.store.load(conversation["id"])["turns"][0]
    assert saved["message"] == "資料を要約して"
    assert saved["attachment_ids"] == [item["id"]]
    assert saved["attachments"][0]["included_in_prompt"] is True


def test_reversing_attachment_order_is_a_different_request_fingerprint(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    conversation = client.post("/api/conversations").json()
    first = client.post(
        f"/api/conversations/{conversation['id']}/attachments",
        files={"file": ("first.txt", b"first", "text/plain")},
    ).json()
    second = client.post(
        f"/api/conversations/{conversation['id']}/attachments",
        files={"file": ("second.txt", b"second", "text/plain")},
    ).json()
    base = {
        "message": "compare in order",
        "conversation_id": conversation["id"],
        "request_id": "attachment-order-request",
    }

    original = client.post(
        "/api/chat",
        json={**base, "attachment_ids": [first["id"], second["id"]]},
    )
    reversed_order = client.post(
        "/api/chat",
        json={**base, "attachment_ids": [second["id"], first["id"]]},
    )

    assert original.status_code == 200
    assert reversed_order.status_code == 409
    assert len(main.store.load(conversation["id"])["turns"]) == 1


def test_attachment_cannot_be_used_from_another_conversation(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    owner = client.post("/api/conversations").json()
    other = client.post("/api/conversations").json()
    item = client.post(
        f"/api/conversations/{owner['id']}/attachments",
        files={"file": ("notes.txt", b"owned", "text/plain")},
    ).json()
    response = client.post(
        "/api/plan",
        json={
            "message": "read",
            "conversation_id": other["id"],
            "attachment_ids": [item["id"]],
        },
    )
    assert response.status_code == 404


def test_binary_attachment_is_saved_but_plan_explicitly_does_not_send_it(
    tmp_path, monkeypatch
):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    conversation = client.post("/api/conversations").json()
    png = b"\x89PNG\r\n\x1a\n" + b"not-a-real-image-but-signature-is-enough-for-storage"
    item = client.post(
        f"/api/conversations/{conversation['id']}/attachments",
        files={"file": ("image.png", png, "image/png")},
    ).json()
    plan = client.post(
        "/api/plan",
        json={
            "message": "describe",
            "conversation_id": conversation["id"],
            "attachment_ids": [item["id"]],
        },
    ).json()
    assert plan["attachments"]["text_included_count"] == 0
    assert "attachment_binary_not_sent" in {
        warning["code"] for warning in plan["warnings"]
    }


def test_upload_enforces_server_side_size_and_mime(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    conversation = client.post("/api/conversations").json()
    monkeypatch.setattr(config, "ATTACHMENT_MAX_BYTES", 4)
    too_large = client.post(
        f"/api/conversations/{conversation['id']}/attachments",
        files={"file": ("large.txt", b"12345", "text/plain")},
    )
    assert too_large.status_code == 413

    monkeypatch.setattr(config, "ATTACHMENT_MAX_BYTES", 1024)
    executable = client.post(
        f"/api/conversations/{conversation['id']}/attachments",
        files={"file": ("run.exe", b"MZbinary", "application/x-msdownload")},
    )
    assert executable.status_code == 415


def test_pdf_attachment_is_signature_checked_and_extracted(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    conversation = client.post("/api/conversations").json()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = io.BytesIO()
    writer.write(output)

    uploaded = client.post(
        f"/api/conversations/{conversation['id']}/attachments",
        files={"file": ("blank.pdf", output.getvalue(), "application/pdf")},
    )
    assert uploaded.status_code == 200
    item = uploaded.json()
    assert item["kind"] == "pdf"
    plan = client.post(
        "/api/plan",
        json={
            "message": "PDFを確認して",
            "conversation_id": conversation["id"],
            "attachment_ids": [item["id"]],
        },
    )
    assert plan.status_code == 200
    assert plan.json()["attachments"]["text_included_count"] == 1


def test_expired_attachments_can_be_purged_without_an_access(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    conversation = client.post("/api/conversations").json()
    item = client.post(
        f"/api/conversations/{conversation['id']}/attachments",
        files={"file": ("old.txt", b"old", "text/plain")},
    ).json()
    metadata_path = (
        main.attachment_store.root
        / conversation["id"]
        / f"{item['id']}.json"
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["expires_at"] = "2000-01-01T00:00:00.000Z"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    assert main.attachment_store.purge_expired() == 1
    assert not metadata_path.exists()
    assert not metadata_path.with_suffix(".blob").exists()


def test_pdf_extraction_is_isolated_and_has_a_hard_timeout(tmp_path, monkeypatch):
    store = attachments.AttachmentStore(tmp_path)
    conversation_id = "12345678-1234-4234-8234-123456789abc"
    attachment_id = "87654321-4321-4321-8321-cba987654321"
    directory = store.root / conversation_id
    directory.mkdir(parents=True)
    (directory / f"{attachment_id}.blob").write_bytes(b"%PDF-1.7\n")
    metadata = {
        "conversation_id": conversation_id,
        "id": attachment_id,
        "kind": "pdf",
    }
    observed = {}

    def timeout(command, **kwargs):
        observed["command"] = command
        observed["timeout"] = kwargs["timeout"]
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(attachments.subprocess, "run", timeout)
    with pytest.raises(attachments.AttachmentError) as caught:
        store._extract_text(metadata)

    assert caught.value.code == "attachment_pdf_timeout"
    assert "-I" in observed["command"]
    assert observed["timeout"] == attachments._PDF_EXTRACT_TIMEOUT_SEC


def test_pdf_subprocess_uses_explicit_utf8_and_discards_stderr(tmp_path, monkeypatch):
    store = attachments.AttachmentStore(tmp_path)
    conversation_id = "12345678-1234-4234-8234-123456789abc"
    attachment_id = "87654321-4321-4321-8321-cba987654321"
    directory = store.root / conversation_id
    directory.mkdir(parents=True)
    (directory / f"{attachment_id}.blob").write_bytes(b"%PDF-1.7\n")
    metadata = {
        "conversation_id": conversation_id,
        "id": attachment_id,
        "kind": "pdf",
    }
    observed = {}

    def complete(command, **kwargs):
        observed["command"] = command
        observed["stdout"] = kwargs["stdout"]
        observed["stderr"] = kwargs["stderr"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="日本語と絵文字🍳".encode("utf-8"),
        )

    monkeypatch.setattr(attachments.subprocess, "run", complete)

    assert store._extract_text(metadata) == "日本語と絵文字🍳"
    assert "sys.stdout.buffer.write" in observed["command"][3]
    assert observed["stdout"] is subprocess.PIPE
    assert observed["stderr"] is subprocess.DEVNULL
