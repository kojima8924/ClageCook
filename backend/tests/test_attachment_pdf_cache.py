# -*- coding: utf-8 -*-

import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import attachments
import config
import pytest


_CONVERSATION_ID = "12345678-1234-4234-8234-123456789abc"


def _pdf_metadata(
    store: attachments.AttachmentStore,
    index: int,
    content: bytes,
) -> dict[str, str]:
    attachment_id = f"00000000-0000-4000-8000-{index:012x}"
    directory = store.root / _CONVERSATION_ID
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{attachment_id}.blob").write_bytes(content)
    return {
        "conversation_id": _CONVERSATION_ID,
        "id": attachment_id,
        "kind": "pdf",
    }


def _completed(command, text: str = "extracted"):
    return subprocess.CompletedProcess(
        command,
        0,
        stdout=text.encode("utf-8"),
    )


def test_pdf_cache_key_tracks_content_and_extraction_limits(tmp_path, monkeypatch):
    store = attachments.AttachmentStore(tmp_path)
    first = _pdf_metadata(store, 1, b"%PDF-first")
    same_content = _pdf_metadata(store, 2, b"%PDF-first")
    calls = []

    def complete(command, **_kwargs):
        calls.append(command)
        return _completed(command, f"result-{len(calls)}")

    monkeypatch.setattr(attachments.subprocess, "run", complete)
    original_pages = config.ATTACHMENT_PDF_MAX_PAGES
    original_chars = config.ATTACHMENT_TEXT_MAX_CHARS

    assert store._extract_text(first) == "result-1"
    assert store._extract_text(first) == "result-1"
    assert store._extract_text(same_content) == "result-1"
    assert len(calls) == 1

    monkeypatch.setattr(config, "ATTACHMENT_PDF_MAX_PAGES", original_pages + 1)
    assert store._extract_text(first) == "result-2"
    monkeypatch.setattr(config, "ATTACHMENT_PDF_MAX_PAGES", original_pages)
    monkeypatch.setattr(config, "ATTACHMENT_TEXT_MAX_CHARS", original_chars + 1)
    assert store._extract_text(first) == "result-3"

    monkeypatch.setattr(config, "ATTACHMENT_TEXT_MAX_CHARS", original_chars)
    path = store.root / _CONVERSATION_ID / f"{first['id']}.blob"
    path.write_bytes(b"%PDF-content-changed")
    assert store._extract_text(first) == "result-4"
    assert len(calls) == 4


def test_pdf_cache_has_ttl_and_lru_entry_bound(tmp_path, monkeypatch):
    now = 100.0
    store = attachments.AttachmentStore(
        tmp_path,
        pdf_cache_max_entries=2,
        pdf_cache_ttl_sec=10.0,
        pdf_cache_clock=lambda: now,
    )
    first = _pdf_metadata(store, 1, b"%PDF-one")
    second = _pdf_metadata(store, 2, b"%PDF-two")
    third = _pdf_metadata(store, 3, b"%PDF-three")
    call_count = 0

    def complete(command, **_kwargs):
        nonlocal call_count
        call_count += 1
        return _completed(command, f"result-{call_count}")

    monkeypatch.setattr(attachments.subprocess, "run", complete)

    assert store._extract_text(first) == "result-1"
    assert store._extract_text(second) == "result-2"
    assert store._extract_text(first) == "result-1"
    assert store._extract_text(third) == "result-3"
    assert len(store._pdf_cache._entries) == 2

    # firstを直前に参照したため、LRUではsecondが追い出される。
    assert store._extract_text(second) == "result-4"
    assert len(store._pdf_cache._entries) == 2

    now = 111.0
    assert store._extract_text(third) == "result-5"
    assert len(store._pdf_cache._entries) <= 2


def test_same_pdf_concurrent_requests_use_one_subprocess(tmp_path, monkeypatch):
    store = attachments.AttachmentStore(tmp_path)
    metadata = _pdf_metadata(store, 1, b"%PDF-single-flight")
    entered = threading.Event()
    release = threading.Event()
    call_count = 0
    lock = threading.Lock()

    def blocked(command, **_kwargs):
        nonlocal call_count
        with lock:
            call_count += 1
        entered.set()
        assert release.wait(2)
        return _completed(command)

    monkeypatch.setattr(attachments.subprocess, "run", blocked)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(store._extract_text, metadata) for _ in range(6)]
        try:
            assert entered.wait(1)
            time.sleep(0.05)
            assert call_count == 1
        finally:
            release.set()
        assert [future.result(timeout=1) for future in futures] == ["extracted"] * 6


def test_pdf_single_flight_failure_unblocks_waiters_and_allows_retry(
    tmp_path,
    monkeypatch,
):
    store = attachments.AttachmentStore(tmp_path)
    metadata = _pdf_metadata(store, 1, b"%PDF-failure")
    entered = threading.Event()
    release = threading.Event()
    call_count = 0
    lock = threading.Lock()

    def fail(command, **_kwargs):
        nonlocal call_count
        with lock:
            call_count += 1
        entered.set()
        assert release.wait(2)
        return subprocess.CompletedProcess(command, 1, stdout=b"")

    monkeypatch.setattr(attachments.subprocess, "run", fail)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(store._extract_text, metadata) for _ in range(4)]
        try:
            assert entered.wait(1)
            time.sleep(0.05)
            assert call_count == 1
        finally:
            release.set()
        for future in futures:
            with pytest.raises(attachments.AttachmentError) as caught:
                future.result(timeout=1)
            assert caught.value.code == "attachment_pdf_invalid"

    monkeypatch.setattr(
        attachments.subprocess,
        "run",
        lambda command, **_kwargs: _completed(command, "retry-success"),
    )
    assert store._extract_text(metadata) == "retry-success"


def test_pdf_subprocess_concurrency_is_bounded(tmp_path, monkeypatch):
    store = attachments.AttachmentStore(
        tmp_path,
        pdf_extract_semaphore=threading.BoundedSemaphore(2),
    )
    metadata = [
        _pdf_metadata(store, index, f"%PDF-{index}".encode())
        for index in range(1, 7)
    ]
    saturated = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0
    calls = 0

    def blocked(command, **_kwargs):
        nonlocal active, peak, calls
        with lock:
            active += 1
            calls += 1
            peak = max(peak, active)
            if active == 2:
                saturated.set()
        try:
            assert release.wait(2)
            return _completed(command)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(attachments.subprocess, "run", blocked)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(store._extract_text, item) for item in metadata]
        try:
            assert saturated.wait(1)
            time.sleep(0.05)
            assert peak == 2
            assert calls == 2
        finally:
            release.set()
        assert [future.result(timeout=1) for future in futures] == ["extracted"] * 6

    assert calls == 6
    assert peak == 2
