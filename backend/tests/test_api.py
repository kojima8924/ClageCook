import asyncio
import json
import logging
from collections import defaultdict

import httpx
import pytest
from fastapi.testclient import TestClient

import config
import main
from storage import ConversationStore


def _events(response):
    events = []
    current = ""
    for line in response.iter_lines():
        if line.startswith("event:"):
            current = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            events.append((current, json.loads(line.split(":", 1)[1].strip())))
    return events


def _event_records(response):
    records = []
    current_id = None
    current_event = ""
    for line in response.iter_lines():
        if line.startswith("id:"):
            current_id = int(line.split(":", 1)[1].strip())
        elif line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            records.append(
                (
                    current_id,
                    current_event,
                    json.loads(line.split(":", 1)[1].strip()),
                )
            )
    return records


def _reset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main,
        "store",
        ConversationStore(tmp_path, sanitizer=main._scrub_public),
    )
    monkeypatch.setattr(main, "_registry", main.RunRegistry())
    monkeypatch.setattr(main, "_rate_limiter", main.SlidingWindowLimiter(100))
    monkeypatch.setattr(main, "_run_slots", asyncio.Semaphore(8))
    monkeypatch.setattr(main, "_conversation_locks", defaultdict(asyncio.Lock))
    monkeypatch.setattr(main, "_active_conversation_runs", {})
    monkeypatch.setattr(main, "_active_conversation_guard", asyncio.Lock())
    monkeypatch.setattr(config, "MOCK_DELAY_SEC", 0.0)
    monkeypatch.setattr(config, "AUTH_TOKEN", "")


def _turn(raw_message, options, request_id):
    return {
        "request_id": request_id,
        "message": raw_message,
        "clean_message": raw_message,
        "options": options.public_dict(),
        "answers": {},
        "synthesis": {
            "ok": True,
            "text": "completed",
            "source": "local",
            "skipped": False,
        },
    }


def test_chat_sse_persists_and_replays_idempotently(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    payload = {"message": "hello", "request_id": "fixed-request", "providers": ["claude", "grok"]}
    with client.stream("POST", "/api/chat", json=payload) as response:
        assert response.status_code == 200
        conversation_id = response.headers["x-conversation-id"]
        first = _events(response)
    assert first[0][0] == "meta"
    assert first[-1][0] == "done"

    replay_payload = dict(payload, conversation_id=conversation_id)
    with client.stream("POST", "/api/chat", json=replay_payload) as response:
        second = _events(response)
    assert second[-1][0] == "done"
    assert len(main.store.load(conversation_id)["turns"]) == 1

    monkeypatch.setattr(main, "_registry", main.RunRegistry())
    with client.stream("POST", "/api/chat", json=payload) as response:
        restarted = _events(response)
        assert response.headers["x-conversation-id"] == conversation_id
    assert restarted[0][1]["replayed"] is True
    assert len(main.store.list()) == 1
    assert len(main.store.load(conversation_id)["turns"]) == 1


def test_fork_conversation_keeps_parent_and_copies_only_prefix(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    parent = main.store.create("branch parent")
    parent["turns"] = [
        {
            "request_id": "completed-one",
            "message": "one",
            "clean_message": "one",
            "status": "completed",
            "answers": {},
            "synthesis": {"ok": True, "skipped": True},
        },
        {
            "request_id": "completed-two",
            "message": "two",
            "clean_message": "two",
            "status": "completed",
            "answers": {},
            "synthesis": {"ok": True, "skipped": True},
        },
    ]
    main.store.save(parent)
    response = TestClient(main.app).post(
        f"/api/conversations/{parent['id']}/turns/completed-two/fork"
    )
    assert response.status_code == 200
    branch = response.json()
    assert branch["id"] != parent["id"]
    assert [turn["message"] for turn in branch["turns"]] == ["one"]
    assert main.store.load(parent["id"])["turns"][1]["message"] == "two"

@pytest.mark.asyncio
async def test_concurrent_same_request_id_runs_once(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def run_once(conversation, raw_message, options, request_id, emit):
        nonlocal calls
        calls += 1
        await emit(
            "meta",
            {"request_id": request_id, "conversation_id": conversation["id"]},
        )
        started.set()
        await release.wait()
        return _turn(raw_message, options, request_id)

    monkeypatch.setattr(main.orchestrator, "run_turn", run_once)
    payload = {"message": "same", "request_id": "concurrent-request"}
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_task = asyncio.create_task(client.post("/api/chat", json=payload))
        await asyncio.wait_for(started.wait(), timeout=2)
        second_task = asyncio.create_task(client.post("/api/chat", json=payload))
        await asyncio.sleep(0.02)
        release.set()
        first, second = await asyncio.gather(first_task, second_task)

    assert first.status_code == second.status_code == 200
    assert first.headers["x-conversation-id"] == second.headers["x-conversation-id"]
    assert first.text == second.text
    assert calls == 1
    assert len(main.store.list()) == 1
    conversation = main.store.load(first.headers["x-conversation-id"])
    assert len(conversation["turns"]) == 1


@pytest.mark.asyncio
async def test_different_requests_cannot_overlap_in_one_conversation(
    tmp_path, monkeypatch
):
    _reset(tmp_path, monkeypatch)
    conversation = main.store.create("shared")
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def delayed(conversation, raw_message, options, request_id, emit):
        nonlocal calls
        calls += 1
        await emit(
            "meta",
            {"request_id": request_id, "conversation_id": conversation["id"]},
        )
        started.set()
        await release.wait()
        return _turn(raw_message, options, request_id)

    monkeypatch.setattr(main.orchestrator, "run_turn", delayed)
    first_payload = {
        "message": "first",
        "conversation_id": conversation["id"],
        "request_id": "conversation-first",
    }
    second_payload = {
        "message": "second",
        "conversation_id": conversation["id"],
        "request_id": "conversation-second",
    }
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_task = asyncio.create_task(client.post("/api/chat", json=first_payload))
        await asyncio.wait_for(started.wait(), timeout=2)

        busy = await client.post("/api/chat", json=second_payload)
        assert busy.status_code == 409
        assert busy.json()["detail"]["code"] == "conversation_busy"
        assert calls == 1

        release.set()
        first = await asyncio.wait_for(first_task, timeout=2)
        retry = await client.post("/api/chat", json=second_payload)

    assert first.status_code == retry.status_code == 200
    assert calls == 2
    saved = main.store.load(conversation["id"])
    assert [turn["request_id"] for turn in saved["turns"]] == [
        "conversation-first",
        "conversation-second",
    ]


def test_request_id_rejects_different_payload_in_memory_and_after_restart(
    tmp_path, monkeypatch
):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    original = {"message": "first", "request_id": "fingerprint-request"}
    assert client.post("/api/chat", json=original).status_code == 200

    changed = {"message": "different", "request_id": "fingerprint-request"}
    assert client.post("/api/chat", json=changed).status_code == 409
    monkeypatch.setattr(main, "_registry", main.RunRegistry())
    assert client.post("/api/chat", json=changed).status_code == 409
    assert len(main.store.list()) == 1


def test_sse_resume_uses_last_event_id(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    payload = {
        "message": "resume",
        "request_id": "resume-request",
        "providers": ["claude", "grok"],
    }
    first = client.post("/api/chat", json=payload)
    first_records = _event_records(first)
    assert [record[0] for record in first_records] == list(
        range(1, len(first_records) + 1)
    )

    resumed = client.post(
        "/api/chat",
        json=payload,
        headers={"Last-Event-ID": "2"},
    )
    resumed_records = _event_records(resumed)
    assert resumed_records
    assert resumed_records[0][0] == 3
    assert resumed_records[-1][1] == "done"

    monkeypatch.setattr(main, "_registry", main.RunRegistry())
    restarted = client.post(
        "/api/chat",
        json=payload,
        headers={"Last-Event-ID": "2"},
    )
    restarted_records = _event_records(restarted)
    assert [record[0] for record in restarted_records] == [
        record[0] for record in first_records[2:]
    ]
    assert [record[1] for record in restarted_records] == [
        record[1] for record in first_records[2:]
    ]
    assert client.post(
        "/api/chat", json=payload, headers={"Last-Event-ID": "invalid"}
    ).status_code == 400


def test_sse_resume_rejects_unknown_or_future_event_id(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    payload = {"message": "resume", "request_id": "future-event-request"}

    unknown = client.post(
        "/api/chat", json=payload, headers={"Last-Event-ID": "1"}
    )
    assert unknown.status_code == 409
    assert unknown.json()["detail"]["code"] == "resume_not_available"
    assert main.store.list() == []

    completed = client.post("/api/chat", json=payload)
    event_count = len(_event_records(completed))
    future = client.post(
        "/api/chat", json=payload, headers={"Last-Event-ID": "999"}
    )
    assert future.status_code == 409
    assert future.json()["detail"] == {
        "code": "resume_not_available",
        "message": "指定されたLast-Event-IDを再開できません",
        "max_event_id": event_count,
    }
    assert len(main.store.list()) == 1


def test_single_process_guard_rejects_a_second_owner(tmp_path):
    first = main._SingleProcessGuard(tmp_path / ".server.lock")
    second = main._SingleProcessGuard(tmp_path / ".server.lock")
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="--workers 1"):
            second.acquire()
        first.acquire()
        first.release()
    finally:
        first.release()

    second.acquire()
    second.release()


def test_live_startup_requires_authentication(monkeypatch):
    monkeypatch.setattr(config, "LIVE_API_ENABLED", True)
    monkeypatch.setattr(config, "AUTH_TOKEN", "")
    with pytest.raises(RuntimeError, match="CLAGE_AUTH_TOKEN"):
        main._validate_startup_safety()

    monkeypatch.setattr(config, "AUTH_TOKEN", "local-secret")
    main._validate_startup_safety()

    monkeypatch.setattr(config, "LIVE_API_ENABLED", False)
    monkeypatch.setattr(config, "AUTH_TOKEN", "")
    main._validate_startup_safety()


def test_admin_telemetry_startup_requires_authentication(monkeypatch):
    monkeypatch.setattr(config, "LIVE_API_ENABLED", False)
    monkeypatch.setattr(config, "ADMIN_TELEMETRY_ENABLED", True)
    monkeypatch.setattr(config, "AUTH_TOKEN", "")
    with pytest.raises(RuntimeError, match="CLAGE_AUTH_TOKEN"):
        main._validate_startup_safety()

    monkeypatch.setattr(config, "AUTH_TOKEN", "local-secret")
    main._validate_startup_safety()


def test_app_lifespan_holds_and_releases_data_directory_lock(tmp_path, monkeypatch):
    guard = main._SingleProcessGuard(tmp_path / ".server.lock")
    monkeypatch.setattr(main, "_single_process_guard", guard)
    monkeypatch.setattr(config, "LIVE_API_ENABLED", False)
    monkeypatch.setattr(config, "AUTH_TOKEN", "")

    with TestClient(main.app) as client:
        assert client.get("/api/health").status_code == 200
        assert guard._depth == 1
        assert guard._handle is not None

    assert guard._depth == 0
    assert guard._handle is None


def test_request_headers_are_validated(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    assert client.post(
        "/api/chat",
        json={"message": "header"},
        headers={"X-Request-ID": "bad header"},
    ).status_code == 422
    assert client.post(
        "/api/chat",
        json={"message": "header", "request_id": "body-request"},
        headers={"X-Request-ID": "other-request"},
    ).status_code == 400


def test_pydantic_validation_errors_have_fixed_non_reflective_response(monkeypatch):
    monkeypatch.setattr(config, "AUTH_TOKEN", "")
    secret = "sk-proj-" + "v" * 32
    expected = {
        "detail": {
            "code": "invalid_request",
            "message": "リクエスト形式が不正です。",
        }
    }
    invalid_requests = (
        ("/api/chat", {"message": [secret]}),
        ("/api/plan", {"message": "safe", "providers": [secret]}),
        ("/api/policy/scan", {"text": {"secret": secret}}),
        ("/api/search", {"q": [secret]}),
    )

    client = TestClient(main.app)
    for path, payload in invalid_requests:
        response = client.post(path, json=payload)

        assert response.status_code == 422
        assert response.json() == expected
        assert secret not in response.text


def test_search_uses_post_body_and_never_a_query_string(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    conversation = main.store.create("検索対象")
    conversation["turns"].append(
        {
            "request_id": "search-saved-request",
            "message": "機密になり得る検索語",
            "answers": {},
            "synthesis": {"ok": True, "text": "見つかりました"},
        }
    )
    main.store.save(conversation)
    client = TestClient(main.app)

    response = client.post(
        "/api/search",
        json={"q": "  機密になり得る検索語  ", "limit": 10},
    )

    assert response.status_code == 200
    assert response.json()["query"] == "機密になり得る検索語"
    assert response.json()["results"][0]["id"] == conversation["id"]
    assert client.get("/api/search?q=機密").status_code == 405


def test_secret_is_not_retained_or_exposed_by_failures(
    tmp_path, monkeypatch, caplog
):
    _reset(tmp_path, monkeypatch)
    secret = "LEAK-ME-BEARER-AND-EXCEPTION"
    monkeypatch.setattr(config, "AUTH_TOKEN", secret)

    async def fail(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(main.orchestrator, "run_turn", fail)
    client = TestClient(main.app)
    with caplog.at_level(logging.ERROR, logger="clage_cook"):
        response = client.post(
            "/api/chat",
            json={"message": "fail", "request_id": "failure-request"},
            headers={"Authorization": f"Bearer {secret}"},
        )

    assert response.status_code == 200
    assert secret not in response.text
    assert secret not in caplog.text
    assert secret not in repr(main._rate_limiter._entries)
    assert [event for event, _ in _events(response)] == ["error", "done"]

    async def return_failed_results(
        conversation, raw_message, options, request_id, emit
    ):
        answer = {"source": "claude", "ok": False, "error": secret}
        synthesis = {"source": "claude", "ok": False, "error": secret}
        await emit("answer", answer)
        await emit("synthesis", synthesis)
        turn = _turn(raw_message, options, request_id)
        turn["answers"] = {"claude": answer}
        turn["synthesis"] = synthesis
        return turn

    monkeypatch.setattr(main.orchestrator, "run_turn", return_failed_results)
    sanitized = client.post(
        "/api/chat",
        json={"message": "sanitize", "request_id": "sanitize-request"},
        headers={"Authorization": f"Bearer {secret}"},
    )
    saved = main.store.load(sanitized.headers["x-conversation-id"])
    assert secret not in sanitized.text
    assert secret not in json.dumps(saved, ensure_ascii=False)


def test_successful_provider_output_is_scrubbed_from_sse_and_storage(
    tmp_path, monkeypatch
):
    _reset(tmp_path, monkeypatch)
    secret = "opaque-runtime-secret-value"
    monkeypatch.setattr(config, "AUTH_TOKEN", secret)

    async def return_secret_answer(
        conversation, raw_message, options, request_id, emit
    ):
        answer = {
            "source": "claude",
            "ok": True,
            "text": f"answer contains {secret}",
        }
        synthesis = {
            "source": "claude",
            "ok": True,
            "text": f"summary contains {secret}",
        }
        await emit("answer", answer)
        await emit("synthesis", synthesis)
        turn = _turn(raw_message, options, request_id)
        turn["answers"] = {"claude": answer}
        turn["synthesis"] = synthesis
        return turn

    monkeypatch.setattr(main.orchestrator, "run_turn", return_secret_answer)
    client = TestClient(main.app)
    response = client.post(
        "/api/chat",
        json={"message": "safe", "request_id": "successful-scrub-request"},
        headers={"Authorization": f"Bearer {secret}"},
    )

    assert response.status_code == 200
    assert secret not in response.text
    saved = main.store.load(response.headers["x-conversation-id"])
    assert secret not in json.dumps(saved, ensure_ascii=False)


def test_only_allowlisted_provider_error_code_reaches_public_data(monkeypatch):
    monkeypatch.setattr(config, "AUTH_TOKEN", "")
    raw = "raw-vendor-detail-must-not-leak"

    billing = main._sanitize_event_data(
        "answer",
        {
            "source": "claude",
            "ok": False,
            "error": raw,
            "error_code": "billing_or_credit_required",
        },
    )
    arbitrary = main._sanitize_event_data(
        "answer",
        {
            "source": "claude",
            "ok": False,
            "error": raw,
            "error_code": "arbitrary-vendor-code",
        },
    )

    assert billing["error_code"] == "billing_or_credit_required"
    assert billing["error"] == (
        "プロバイダの請求設定またはクレジット残高を確認してください"
    )
    assert raw not in str(billing)
    assert "error_code" not in arbitrary
    assert arbitrary["error"] == "AIからの回答取得に失敗しました"
    assert raw not in str(arbitrary)


def test_existing_unscrubbed_history_is_scrubbed_on_every_public_read(
    tmp_path, monkeypatch
):
    _reset(tmp_path, monkeypatch)
    secret = "opaque-existing-history-secret"
    monkeypatch.setattr(config, "AUTH_TOKEN", secret)
    unsafe_store = ConversationStore(tmp_path)
    conversation = unsafe_store.create("safe title")
    conversation["title"] = secret
    conversation["turns"] = [
        {
            "request_id": "existing-secret-request",
            "message": secret,
            "answers": {"claude": {"ok": True, "text": secret}},
            "synthesis": {"ok": True, "text": secret},
        }
    ]
    unsafe_store.save(conversation)
    monkeypatch.setattr(main, "store", unsafe_store)
    client = TestClient(main.app)
    headers = {"Authorization": f"Bearer {secret}"}

    responses = [
        client.get("/api/conversations", headers=headers),
        client.get(f"/api/conversations/{conversation['id']}", headers=headers),
        client.get(
            f"/api/conversations/{conversation['id']}/export",
            headers=headers,
        ),
        client.post(
            "/api/search",
            json={"q": secret},
            headers=headers,
        ),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert all(secret not in response.text for response in responses)


def test_cors_allows_localhost_but_not_arbitrary_origins(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    local = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    hostile = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert local.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "X-Conversation-ID" in local.headers["access-control-expose-headers"]
    assert "access-control-allow-origin" not in hostile.headers


@pytest.mark.asyncio
async def test_rename_waits_for_active_turn_and_is_not_lost(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    conversation = main.store.create("before")
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed(conversation, raw_message, options, request_id, emit):
        started.set()
        await release.wait()
        return _turn(raw_message, options, request_id)

    monkeypatch.setattr(main.orchestrator, "run_turn", delayed)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        chat_task = asyncio.create_task(
            client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "message": "turn",
                    "request_id": "rename-request",
                },
            )
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        rename_task = asyncio.create_task(
            client.patch(
                f"/api/conversations/{conversation['id']}",
                json={"title": "renamed"},
            )
        )
        await asyncio.sleep(0.02)
        assert not rename_task.done()
        release.set()
        chat_response, rename_response = await asyncio.gather(chat_task, rename_task)

    assert chat_response.status_code == rename_response.status_code == 200
    saved = main.store.load(conversation["id"])
    assert saved["title"] == "renamed"
    assert len(saved["turns"]) == 1


@pytest.mark.asyncio
async def test_cancel_run_emits_error_and_done(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    started = asyncio.Event()

    async def blocked(*_args, **_kwargs):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(main.orchestrator, "run_turn", blocked)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        request = asyncio.create_task(
            client.post(
                "/api/chat",
                json={"message": "cancel", "request_id": "cancel-request"},
            )
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        cancelled = await client.post("/api/runs/cancel-request/cancel")
        response = await asyncio.wait_for(request, timeout=2)

    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is True
    assert cancelled.json()["cancellation_requested"] is True
    assert cancelled.json()["provider_stop_guaranteed"] is False
    assert "課金停止は保証されません" in cancelled.json()["warning"]
    assert [event for event, _ in _events(response)] == ["error", "done"]
    assert "キャンセル" in response.text
    saved = main.store.load(response.headers["x-conversation-id"])
    assert len(saved["turns"]) == 1
    assert saved["turns"][0]["status"] == "cancelled"
    assert saved["turns"][0]["usage_may_be_incomplete"] is True


@pytest.mark.asyncio
async def test_cancel_run_persists_completed_answers_and_usage(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    started = asyncio.Event()

    async def partial(conversation, raw_message, options, request_id, emit):
        await emit(
            "meta",
            {"request_id": request_id, "conversation_id": conversation["id"]},
        )
        await emit(
            "answer",
            {
                "source": "claude",
                "ok": True,
                "text": "completed before cancellation",
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 10,
                    "total_tokens": 30,
                },
            },
        )
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(main.orchestrator, "run_turn", partial)
    payload = {
        "message": "!high\ncancel partial",
        "tier": "balanced",
        "request_id": "cancel-partial",
    }
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        request = asyncio.create_task(client.post("/api/chat", json=payload))
        await asyncio.wait_for(started.wait(), timeout=2)
        cancelled = await client.post("/api/runs/cancel-partial/cancel")
        response = await asyncio.wait_for(request, timeout=2)

    assert cancelled.status_code == 200
    records = _event_records(response)
    assert [record[1] for record in records] == ["meta", "answer", "error", "done"]
    conversation_id = response.headers["x-conversation-id"]
    saved = main.store.load(conversation_id)
    assert len(saved["turns"]) == 1
    turn = saved["turns"][0]
    assert turn["cancelled"] is True
    assert turn["usage_may_be_incomplete"] is True
    assert turn["answers"]["claude"]["usage"]["total_tokens"] == 30
    assert turn["options"]["tier"] == "high"
    assert turn["resume_request"]["tier"] == "balanced"
    assert turn["resume_request"]["confirm_live_api"] is False

    monkeypatch.setattr(main, "_registry", main.RunRegistry())
    replay = TestClient(main.app).post("/api/chat", json=payload)
    replay_records = _event_records(replay)
    assert [record[0] for record in replay_records] == [1, 2, 3, 4]
    assert [record[1] for record in replay_records] == [
        "meta",
        "answer",
        "error",
        "done",
    ]


def test_general_failure_persists_durable_claim_partial_usage_and_replays(
    tmp_path, monkeypatch
):
    _reset(tmp_path, monkeypatch)
    calls = 0

    async def partial_failure(conversation, raw_message, options, request_id, emit):
        nonlocal calls
        calls += 1
        await emit(
            "meta",
            {"request_id": request_id, "conversation_id": conversation["id"]},
        )
        await emit(
            "answer",
            {
                "source": "chatgpt",
                "ok": True,
                "text": "answer completed before server failure",
                "usage": {"input_tokens": 40, "output_tokens": 20, "total_tokens": 60},
            },
        )
        raise RuntimeError("internal failure must not be exposed")

    monkeypatch.setattr(main.orchestrator, "run_turn", partial_failure)
    payload = {"message": "durable", "request_id": "durable-failure"}
    client = TestClient(main.app)

    first = client.post("/api/chat", json=payload)
    conversation_id = first.headers["x-conversation-id"]
    saved = main.store.load(conversation_id)

    assert [event for event, _ in _events(first)] == [
        "meta",
        "answer",
        "error",
        "done",
    ]
    assert calls == 1
    assert len(saved["turns"]) == 1
    assert saved["turns"][0]["status"] == "failed"
    assert saved["turns"][0]["answers"]["chatgpt"]["usage"]["total_tokens"] == 60

    monkeypatch.setattr(main, "_registry", main.RunRegistry())
    replay = client.post("/api/chat", json=payload)

    assert calls == 1
    assert [event for event, _ in _events(replay)] == [
        "meta",
        "answer",
        "error",
        "done",
    ]
    assert len(main.store.load(conversation_id)["turns"]) == 1


def test_cancel_unknown_run_is_404(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    response = TestClient(main.app).post("/api/runs/missing-request/cancel")
    assert response.status_code == 404


def test_startup_marks_orphaned_running_turn_as_interrupted(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main,
        "_single_process_guard",
        main._SingleProcessGuard(tmp_path / ".test-server.lock"),
    )
    conversation = main.store.create("orphaned")
    conversation["turns"].append(
        {
            "request_id": "orphaned-request",
            "message": "orphaned",
            "clean_message": "orphaned",
            "status": "running",
            "usage_may_be_incomplete": True,
            "answers": {},
            "synthesis": {"ok": False, "pending": True},
            "options": {"providers": ["chatgpt"]},
            "event_log": [],
        }
    )
    main.store.save(conversation)

    with TestClient(main.app) as client:
        response = client.get(f"/api/conversations/{conversation['id']}")

    assert response.status_code == 200
    turn = response.json()["turns"][0]
    assert turn["status"] == "interrupted"
    assert turn["interrupted"] is True
    assert turn["failed"] is True
    assert turn["usage_may_be_incomplete"] is True
    assert "サーバー停止" in turn["synthesis"]["error"]


def test_settings_never_returns_keys(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-leak")
    client = TestClient(main.app)
    response = client.get("/api/settings")
    assert response.status_code == 200
    assert "do-not-leak" not in response.text
    assert response.json()["providers"][2]["configured"] is True
    assert response.json()["live_api_enabled"] is False


def test_auth_is_constant_interface(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "AUTH_TOKEN", "token")
    client = TestClient(main.app)
    assert client.get("/api/health").status_code == 401
    assert client.get(
        "/api/health", headers={"Authorization": "Bearer token"}
    ).status_code == 200


def test_answer_and_synthesis_regeneration_use_immutable_attempts(
    tmp_path, monkeypatch
):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    started = client.post(
        "/api/chat",
        json={
            "message": "再生成テスト",
            "request_id": "original-run",
            "providers": ["claude", "chatgpt"],
        },
    )
    assert started.status_code == 200
    conversation_id = started.headers["x-conversation-id"]

    plan = client.post(
        f"/api/conversations/{conversation_id}/turns/original-run/regeneration-plan",
        json={"target": "answer", "provider": "claude"},
    )
    assert plan.status_code == 200
    assert plan.json()["calls"]["total"] == 1
    assert plan.json()["billable"] is False

    path = f"/api/conversations/{conversation_id}/turns/original-run/regenerate"
    regenerated = client.post(
        path,
        json={
            "target": "answer",
            "provider": "claude",
            "regeneration_id": "answer-regeneration-1",
        },
    )
    assert regenerated.status_code == 200
    answer_body = regenerated.json()
    assert answer_body["replayed"] is False
    turn = answer_body["conversation"]["turns"][0]
    assert len(turn["attempts"]) == 2
    assert turn["attempts"][0]["original"] is True
    assert turn["attempts"][1]["parent_attempt_id"] == turn["attempts"][0]["attempt_id"]
    assert turn["active_attempts"]["answer:claude"] == "answer-regeneration-1"
    assert turn["synthesis_stale"] is True

    replayed = client.post(
        path,
        json={
            "target": "answer",
            "provider": "claude",
            "regeneration_id": "answer-regeneration-1",
        },
    )
    assert replayed.status_code == 200
    assert replayed.json()["replayed"] is True
    assert len(replayed.json()["conversation"]["turns"][0]["attempts"]) == 2

    synthesis = client.post(
        path,
        json={
            "target": "synthesis",
            "regeneration_id": "synthesis-regeneration-1",
        },
    )
    assert synthesis.status_code == 200
    final_turn = synthesis.json()["conversation"]["turns"][0]
    assert len(final_turn["attempts"]) == 4
    assert final_turn["active_attempts"]["synthesis"] == "synthesis-regeneration-1"
    assert final_turn["synthesis_stale"] is False


def test_regeneration_id_rejects_different_target(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    client = TestClient(main.app)
    started = client.post(
        "/api/chat",
        json={
            "message": "競合テスト",
            "request_id": "conflict-original",
            "providers": ["claude", "chatgpt"],
        },
    )
    conversation_id = started.headers["x-conversation-id"]
    path = f"/api/conversations/{conversation_id}/turns/conflict-original/regenerate"
    first = client.post(
        path,
        json={
            "target": "answer",
            "provider": "claude",
            "regeneration_id": "shared-regeneration-id",
        },
    )
    assert first.status_code == 200
    conflict = client.post(
        path,
        json={
            "target": "answer",
            "provider": "chatgpt",
            "regeneration_id": "shared-regeneration-id",
        },
    )
    assert conflict.status_code == 409
