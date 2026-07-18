# -*- coding: utf-8 -*-

import asyncio
import threading
import time
from collections import defaultdict
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

import attachments
import config
import main
from storage import ConversationStore


class _FakeBudget:
    def __init__(self) -> None:
        self.reserved: list[str] = []
        self.refreshed: list[tuple[str, dict]] = []
        self.released: list[str] = []
        self.settled: list[tuple[str, bool]] = []

    def decorate_plan(self, plan, _store):
        return deepcopy(plan)

    def reserve(self, *, request_id, **_kwargs):
        self.reserved.append(request_id)
        return {"request_id": request_id, "state": "reserved"}

    def refresh_reservation(self, *, request_id, plan, **_kwargs):
        self.refreshed.append((request_id, deepcopy(plan)))
        return {"request_id": request_id, "state": "reserved"}

    def release_undispatched(self, request_id):
        self.released.append(request_id)

    def settle(self, request_id, *, usage_reconciled, turn=None):
        self.settled.append((request_id, usage_reconciled))

    def public_snapshot(self, _store):
        return {}


def _allowed_plan():
    return {
        "allowed": True,
        "block_reasons": [],
        "billable": False,
        "policy": {"action": "allow"},
    }


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


def _completed_conversation():
    conversation = main.store.create("regeneration")
    conversation["turns"] = [
        {
            "request_id": "original-turn",
            "request_fingerprint": "original-fingerprint",
            "created_at": "2026-01-01T00:00:00.000Z",
            "message": "question",
            "clean_message": "question",
            "status": "completed",
            "options": {
                "tier": "balanced",
                "blind": False,
                "web_search": False,
            },
            "answers": {
                "claude": {
                    "ok": True,
                    "source": "claude",
                    "text": "old answer",
                    "model": "mock",
                    "mock": True,
                    "usage": {},
                }
            },
            "synthesis": {
                "ok": True,
                "source": "claude",
                "text": "old synthesis",
                "model": "mock",
                "mock": True,
                "usage": {},
                "skipped": False,
            },
        }
    ]
    main.store.save(conversation)
    return conversation


@pytest.mark.asyncio
async def test_chat_releases_budget_when_attachment_preparation_fails(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    budget = _FakeBudget()
    monkeypatch.setattr(main, "budget_guard", budget)
    monkeypatch.setattr(main, "_plan_from_request", lambda _req: _allowed_plan())

    def fail_context(*_args, **_kwargs):
        raise attachments.AttachmentError("attachment_expired", "expired")

    monkeypatch.setattr(main.attachment_store, "build_context", fail_context)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/chat",
            json={
                "message": "question",
                "request_id": "attachment-failure",
                "attachment_ids": ["12345678-1234-4234-8234-123456789abc"],
            },
        )

    assert response.status_code == 200
    assert budget.reserved == ["attachment-failure"]
    assert budget.released == ["attachment-failure"]
    assert budget.settled == []


@pytest.mark.asyncio
async def test_chat_releases_budget_when_cancelled_while_waiting_for_run_slot(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    budget = _FakeBudget()
    monkeypatch.setattr(main, "budget_guard", budget)
    monkeypatch.setattr(main, "_plan_from_request", lambda _req: _allowed_plan())
    monkeypatch.setattr(main, "_run_slots", asyncio.Semaphore(0))
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        chat_task = asyncio.create_task(
            client.post(
                "/api/chat",
                json={"message": "queued", "request_id": "queued-cancellation"},
            )
        )
        for _ in range(100):
            state = await main._registry.lookup("queued-cancellation")
            if state is not None and state.task is not None:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("chat task was not registered")
        cancelled = await client.post("/api/runs/queued-cancellation/cancel")
        response = await asyncio.wait_for(chat_task, timeout=2)

    assert cancelled.status_code == 200
    assert response.status_code == 200
    assert budget.reserved == ["queued-cancellation"]
    assert budget.released == ["queued-cancellation"]
    assert budget.settled == []


@pytest.mark.asyncio
async def test_cancel_during_pending_save_persists_cancelled_turn_and_releases_budget(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    budget = _FakeBudget()
    monkeypatch.setattr(main, "budget_guard", budget)
    entered = threading.Event()
    release = threading.Event()
    original_save = main.store.save
    gated = False

    def gated_save(conversation):
        nonlocal gated
        target = next(
            (
                turn
                for turn in conversation.get("turns") or []
                if turn.get("request_id") == "pending-save-cancel"
            ),
            None,
        )
        if not gated and target is not None and target.get("status") == "running":
            gated = True
            entered.set()
            assert release.wait(2)
        return original_save(conversation)

    async def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("pre-dispatch cancellation must not call a provider")

    monkeypatch.setattr(main.store, "save", gated_save)
    monkeypatch.setattr(main.orchestrator, "run_turn", must_not_dispatch)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        chat_task = asyncio.create_task(
            client.post(
                "/api/chat",
                json={
                    "message": "cancel pending save",
                    "request_id": "pending-save-cancel",
                },
            )
        )
        assert await asyncio.to_thread(entered.wait, 2)
        cancel_task = asyncio.create_task(
            client.post("/api/runs/pending-save-cancel/cancel")
        )
        await asyncio.sleep(0.02)
        release.set()
        cancelled, response = await asyncio.gather(cancel_task, chat_task)

    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is True
    assert cancelled.json()["terminal_outcome"] == "cancelled"
    assert response.status_code == 200
    saved = main.store.list()
    assert len(saved) == 1
    turn = main.store.load(saved[0]["id"])["turns"][0]
    assert turn["status"] == "cancelled"
    assert turn["cancelled"] is True
    assert budget.released == ["pending-save-cancel"]
    assert budget.settled == []


@pytest.mark.asyncio
async def test_cancel_during_chat_final_save_keeps_completed_result_and_settlement(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    budget = _FakeBudget()
    monkeypatch.setattr(main, "budget_guard", budget)
    entered = threading.Event()
    release = threading.Event()
    original_save = main.store.save
    gated = False

    def gated_save(conversation):
        nonlocal gated
        target = next(
            (
                turn
                for turn in conversation.get("turns") or []
                if turn.get("request_id") == "final-save-cancel"
            ),
            None,
        )
        if not gated and target is not None and target.get("status") == "completed":
            gated = True
            entered.set()
            assert release.wait(2)
        return original_save(conversation)

    monkeypatch.setattr(main.store, "save", gated_save)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        chat_task = asyncio.create_task(
            client.post(
                "/api/chat",
                json={
                    "message": "complete before final cancel",
                    "request_id": "final-save-cancel",
                },
            )
        )
        assert await asyncio.to_thread(entered.wait, 2)
        cancel_task = asyncio.create_task(
            client.post("/api/runs/final-save-cancel/cancel")
        )
        await asyncio.sleep(0.02)
        release.set()
        cancelled, response = await asyncio.gather(cancel_task, chat_task)

    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is False
    assert cancelled.json()["terminal_outcome"] == "completed"
    assert response.status_code == 200
    saved = main.store.list()
    turn = main.store.load(saved[0]["id"])["turns"][0]
    assert turn["status"] == "completed"
    assert turn["cancelled"] is False
    assert budget.settled == [("final-save-cancel", True)]
    assert budget.released == []
    assert '"cancelled":true' not in response.text


@pytest.mark.asyncio
async def test_cancel_during_chat_settlement_keeps_completed_result(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    budget = _FakeBudget()
    monkeypatch.setattr(main, "budget_guard", budget)
    entered = threading.Event()
    release = threading.Event()
    settled_once = False

    def gated_settle(request_id, *, usage_reconciled, turn=None):
        nonlocal settled_once
        if request_id == "settle-cancel" and not settled_once:
            settled_once = True
            entered.set()
            assert release.wait(2)
        budget.settled.append((request_id, usage_reconciled))

    budget.settle = gated_settle
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        chat_task = asyncio.create_task(
            client.post(
                "/api/chat",
                json={
                    "message": "complete before settlement cancel",
                    "request_id": "settle-cancel",
                },
            )
        )
        assert await asyncio.to_thread(entered.wait, 2)
        cancel_task = asyncio.create_task(
            client.post("/api/runs/settle-cancel/cancel")
        )
        await asyncio.sleep(0.02)
        release.set()
        cancelled, response = await asyncio.gather(cancel_task, chat_task)

    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is False
    assert cancelled.json()["terminal_outcome"] == "completed"
    assert response.status_code == 200
    saved = main.store.list()
    turn = main.store.load(saved[0]["id"])["turns"][0]
    assert turn["status"] == "completed"
    assert budget.settled == [("settle-cancel", True)]
    assert budget.released == []
    assert '"cancelled":true' not in response.text


@pytest.mark.asyncio
async def test_cancel_during_regeneration_final_save_keeps_completed_attempt(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    conversation = _completed_conversation()
    budget = _FakeBudget()
    monkeypatch.setattr(main, "budget_guard", budget)
    entered = threading.Event()
    release = threading.Event()
    original_save = main.store.save
    gated = False

    async def successful_provider(*_args, **_kwargs):
        return {
            "ok": True,
            "source": "claude",
            "text": "completed regeneration",
            "model": "mock",
            "mock": True,
            "usage": {},
        }

    def gated_save(data):
        nonlocal gated
        attempts = data["turns"][0].get("attempts") or []
        target = regeneration_attempt = attempts[-1] if attempts else None
        if (
            not gated
            and target is not None
            and regeneration_attempt.get("attempt_id") == "regen-final-cancel"
            and regeneration_attempt.get("status") == "completed"
        ):
            gated = True
            entered.set()
            assert release.wait(2)
        return original_save(data)

    monkeypatch.setattr(main.orchestrator, "_run_provider", successful_provider)
    monkeypatch.setattr(main.store, "save", gated_save)
    path = (
        f"/api/conversations/{conversation['id']}"
        "/turns/original-turn/regenerate"
    )
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        regeneration_task = asyncio.create_task(
            client.post(
                path,
                json={
                    "target": "answer",
                    "provider": "claude",
                    "regeneration_id": "regen-final-cancel",
                },
            )
        )
        assert await asyncio.to_thread(entered.wait, 2)
        cancel_task = asyncio.create_task(
            client.post("/api/runs/regen-final-cancel/cancel")
        )
        await asyncio.sleep(0.02)
        release.set()
        cancelled, response = await asyncio.gather(
            cancel_task,
            regeneration_task,
        )

    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is False
    assert cancelled.json()["terminal_outcome"] == "completed"
    assert response.status_code == 200
    attempt = main.store.load(conversation["id"])["turns"][0]["attempts"][-1]
    assert attempt["status"] == "completed"
    assert attempt.get("cancelled") is not True
    assert attempt["result"]["text"] == "completed regeneration"
    assert budget.settled == [("regen-final-cancel", True)]
    assert budget.released == []


@pytest.mark.asyncio
async def test_cancel_during_regeneration_failure_cleanup_keeps_failed_outcome(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    conversation = _completed_conversation()
    budget = _FakeBudget()
    monkeypatch.setattr(main, "budget_guard", budget)
    entered = threading.Event()
    release = threading.Event()

    async def failed_provider(*_args, **_kwargs):
        raise RuntimeError("provider failure before cleanup")

    def gated_settle(request_id, *, usage_reconciled, turn=None):
        entered.set()
        assert release.wait(2)
        budget.settled.append((request_id, usage_reconciled))

    budget.settle = gated_settle
    monkeypatch.setattr(main.orchestrator, "_run_provider", failed_provider)
    path = (
        f"/api/conversations/{conversation['id']}"
        "/turns/original-turn/regenerate"
    )
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        regeneration_task = asyncio.create_task(
            client.post(
                path,
                json={
                    "target": "answer",
                    "provider": "claude",
                    "regeneration_id": "regen-failure-cancel",
                },
            )
        )
        assert await asyncio.to_thread(entered.wait, 2)
        cancel_task = asyncio.create_task(
            client.post("/api/runs/regen-failure-cancel/cancel")
        )
        await asyncio.sleep(0.02)
        release.set()
        cancelled, response = await asyncio.gather(
            cancel_task,
            regeneration_task,
        )

    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is False
    assert cancelled.json()["terminal_outcome"] == "failed"
    assert response.status_code == 500
    attempt = main.store.load(conversation["id"])["turns"][0]["attempts"][-1]
    assert attempt["status"] == "interrupted"
    assert attempt.get("cancelled") is not True
    assert budget.settled == [("regen-failure-cancel", False)]
    assert budget.released == []
    assert await main._registry.lookup("regen-failure-cancel") is None


@pytest.mark.asyncio
async def test_cancel_between_registry_claim_and_task_assignment_is_not_already_done(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    budget = _FakeBudget()
    reserve_entered = threading.Event()
    allow_reserve = threading.Event()
    provider_calls = 0

    def blocked_reserve(*, request_id, **_kwargs):
        budget.reserved.append(request_id)
        reserve_entered.set()
        assert allow_reserve.wait(2)
        return {"request_id": request_id, "state": "reserved"}

    async def must_not_run(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("cancelled startup must not dispatch")

    budget.reserve = blocked_reserve
    monkeypatch.setattr(main, "budget_guard", budget)
    monkeypatch.setattr(main, "_plan_from_request", lambda _req: _allowed_plan())
    monkeypatch.setattr(main.orchestrator, "run_turn", must_not_run)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        chat_task = asyncio.create_task(
            client.post(
                "/api/chat",
                json={"message": "cancel", "request_id": "cancel-before-task"},
            )
        )
        assert await asyncio.to_thread(reserve_entered.wait, 2)
        cancel_task = asyncio.create_task(
            client.post("/api/runs/cancel-before-task/cancel")
        )
        await asyncio.sleep(0.02)
        allow_reserve.set()
        cancelled, response = await asyncio.gather(cancel_task, chat_task)

    assert cancelled.status_code == 200
    assert cancelled.json().get("already_done") is not True
    assert cancelled.json()["cancellation_requested"] is True
    assert response.status_code == 200
    assert provider_calls == 0
    assert budget.released == ["cancel-before-task"]


@pytest.mark.asyncio
async def test_chat_handler_cancel_before_task_assignment_releases_claim(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    budget = _FakeBudget()
    monkeypatch.setattr(main, "budget_guard", budget)
    monkeypatch.setattr(main, "_plan_from_request", lambda _req: _allowed_plan())
    real_claim = main._claim_conversation_run
    claim_entered = asyncio.Event()

    async def claim_then_wait(conversation_id, request_id):
        claimed = await real_claim(conversation_id, request_id)
        claim_entered.set()
        await asyncio.Event().wait()
        return claimed

    monkeypatch.setattr(main, "_claim_conversation_run", claim_then_wait)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        request_task = asyncio.create_task(
            client.post(
                "/api/chat",
                json={"message": "cancel handler", "request_id": "handler-cancel"},
            )
        )
        await asyncio.wait_for(claim_entered.wait(), timeout=1)
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task

    assert main._active_conversation_runs == {}
    assert await main._registry.lookup("handler-cancel") is None
    assert budget.reserved == []


@pytest.mark.asyncio
async def test_regeneration_handler_cancel_before_task_assignment_releases_claim(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    conversation = _completed_conversation()
    real_claim = main._claim_conversation_run
    claim_entered = asyncio.Event()

    async def claim_then_wait(conversation_id, request_id):
        claimed = await real_claim(conversation_id, request_id)
        claim_entered.set()
        await asyncio.Event().wait()
        return claimed

    monkeypatch.setattr(main, "_claim_conversation_run", claim_then_wait)
    transport = httpx.ASGITransport(app=main.app)
    path = (
        f"/api/conversations/{conversation['id']}/turns/original-turn/regenerate"
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        request_task = asyncio.create_task(
            client.post(
                path,
                json={
                    "target": "answer",
                    "provider": "claude",
                    "regeneration_id": "regen-handler-cancel",
                },
            )
        )
        await asyncio.wait_for(claim_entered.wait(), timeout=1)
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task

    assert main._active_conversation_runs == {}
    assert await main._registry.lookup("regen-handler-cancel") is None


@pytest.mark.asyncio
async def test_fork_and_delete_are_rejected_while_generation_is_active(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    conversation = _completed_conversation()
    main._active_conversation_runs[conversation["id"]] = "active-regeneration"
    transport = httpx.ASGITransport(app=main.app)
    base = f"/api/conversations/{conversation['id']}"

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        forked = await client.post(f"{base}/turns/original-turn/fork")
        deleted = await client.delete(base)

    assert forked.status_code == 409
    assert forked.json()["detail"]["code"] == "conversation_busy"
    assert deleted.status_code == 409
    assert deleted.json()["detail"]["code"] == "conversation_busy"
    assert main.store.load(conversation["id"])["id"] == conversation["id"]


@pytest.mark.asyncio
async def test_repeated_cancel_does_not_cancel_cleanup_or_leave_state_active():
    registry = main.RunRegistry()
    state, created = await registry.claim(
        "double-cancel-run",
        lambda: main.RunState(
            "double-cancel-run",
            "conversation",
            "fingerprint",
        ),
    )
    assert created is True
    cleanup_entered = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def runner():
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
        finally:
            cleanup_entered.set()
            await release_cleanup.wait()
            await state.finish()

    assert await registry.start(state, runner) is True
    await registry.request_cancel(state.request_id)
    await cleanup_entered.wait()
    await registry.request_cancel(state.request_id)
    release_cleanup.set()
    assert state.task is not None
    await state.task

    assert state.done is True
    assert state.task.cancelled() is False


@pytest.mark.asyncio
async def test_registry_shutdown_drains_background_runners():
    registry = main.RunRegistry()
    state, _created = await registry.claim(
        "shutdown-run",
        lambda: main.RunState("shutdown-run", "conversation", "fingerprint"),
    )
    cleanup_completed = asyncio.Event()

    async def runner():
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cleanup_completed.set()
        finally:
            await state.finish()

    assert await registry.start(state, runner) is True
    assert await registry.shutdown() == 1

    assert cleanup_completed.is_set()
    assert state.done is True


@pytest.mark.asyncio
async def test_cancelled_run_can_be_claimed_again_after_retention():
    clock = [10.0]
    registry = main.RunRegistry(retention_sec=1.0, clock=lambda: clock[0])
    state, created = await registry.claim(
        "retained-cancel",
        lambda: main.RunState(
            "retained-cancel",
            "conversation",
            "fingerprint",
            clock=lambda: clock[0],
        ),
    )
    assert created is True
    state.cancel_requested = True
    await state.finish()

    retained, created = await registry.claim(
        "retained-cancel",
        lambda: pytest.fail("retained state must be replayed"),
    )
    assert retained is state
    assert created is False

    clock[0] += 1.01
    replacement, created = await registry.claim(
        "retained-cancel",
        lambda: main.RunState(
            "retained-cancel",
            "conversation",
            "fingerprint",
            clock=lambda: clock[0],
        ),
    )
    assert created is True
    assert replacement.execution_id != state.execution_id


@pytest.mark.asyncio
async def test_regeneration_survives_request_disconnect_and_releases_conversation_lock(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    conversation = _completed_conversation()
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_provider(*_args, **_kwargs):
        started.set()
        await release.wait()
        return {
            "ok": True,
            "source": "claude",
            "text": "new answer",
            "model": "mock",
            "mock": True,
            "usage": {},
        }

    monkeypatch.setattr(main.orchestrator, "_run_provider", delayed_provider)
    path = (
        f"/api/conversations/{conversation['id']}"
        "/turns/original-turn/regenerate"
    )
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        request_task = asyncio.create_task(
            client.post(
                path,
                json={
                    "target": "answer",
                    "provider": "claude",
                    "regeneration_id": "disconnect-regeneration",
                },
            )
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        renamed = await asyncio.wait_for(
            client.patch(
                f"/api/conversations/{conversation['id']}",
                json={"title": "renamed while provider runs"},
            ),
            timeout=0.5,
        )
        assert renamed.status_code == 200
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task
        state = await main._registry.lookup("disconnect-regeneration")
        assert state is not None
        release.set()
        assert await state.wait_finished(timeout=2)
        assert await main._registry.lookup("disconnect-regeneration") is None

    saved = main.store.load(conversation["id"])
    assert saved["title"] == "renamed while provider runs"
    attempt = saved["turns"][0]["attempts"][-1]
    assert attempt["status"] == "completed"
    assert attempt["result"]["text"] == "new answer"


@pytest.mark.asyncio
async def test_saved_regeneration_replay_bypasses_rate_and_active_run_claim(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    conversation = _completed_conversation()
    budget = _FakeBudget()
    monkeypatch.setattr(main, "budget_guard", budget)
    provider_calls = 0

    async def successful_provider(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return {
            "ok": True,
            "source": "claude",
            "text": "durable regeneration replay",
            "model": "mock",
            "mock": True,
            "usage": {},
        }

    monkeypatch.setattr(main.orchestrator, "_run_provider", successful_provider)
    path = (
        f"/api/conversations/{conversation['id']}"
        "/turns/original-turn/regenerate"
    )
    body = {
        "target": "answer",
        "provider": "claude",
        "regeneration_id": "durable-regeneration-replay",
    }
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(path, json=body)
        assert first.status_code == 200
        assert await main._registry.lookup("durable-regeneration-replay") is None

        async def must_not_rate_limit(*_args, **_kwargs):
            raise AssertionError("durable replay must bypass the rate limiter")

        monkeypatch.setattr(main._rate_limiter, "check", must_not_rate_limit)
        main._active_conversation_runs[conversation["id"]] = "different-live-run"
        replay = await client.post(path, json=body)
        mismatch = await client.post(
            path,
            json={
                "target": "synthesis",
                "regeneration_id": "durable-regeneration-replay",
            },
        )

    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["attempt"]["result"]["text"] == "durable regeneration replay"
    assert mismatch.status_code == 409
    assert provider_calls == 1


@pytest.mark.asyncio
async def test_cancel_endpoint_stops_background_regeneration_and_persists_it(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    conversation = _completed_conversation()
    budget = _FakeBudget()
    monkeypatch.setattr(main, "budget_guard", budget)
    started = asyncio.Event()
    provider_cancelled = asyncio.Event()

    async def blocked_provider(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            provider_cancelled.set()
            raise

    monkeypatch.setattr(main.orchestrator, "_run_provider", blocked_provider)
    path = (
        f"/api/conversations/{conversation['id']}"
        "/turns/original-turn/regenerate"
    )
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        request_task = asyncio.create_task(
            client.post(
                path,
                json={
                    "target": "answer",
                    "provider": "claude",
                    "regeneration_id": "cancel-regeneration",
                },
            )
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        cancelled = await client.post("/api/runs/cancel-regeneration/cancel")
        response = await asyncio.wait_for(request_task, timeout=2)

    assert cancelled.status_code == 200
    assert cancelled.json()["cancellation_requested"] is True
    assert provider_cancelled.is_set()
    assert response.status_code == 409
    attempt = main.store.load(conversation["id"])["turns"][0]["attempts"][-1]
    assert attempt["status"] == "interrupted"
    assert attempt["cancelled"] is True
    assert budget.reserved == ["cancel-regeneration"]
    assert budget.settled == [("cancel-regeneration", False)]


@pytest.mark.asyncio
async def test_regeneration_failure_is_not_recorded_as_user_cancellation(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    conversation = _completed_conversation()
    budget = _FakeBudget()
    monkeypatch.setattr(main, "budget_guard", budget)

    async def failed_provider(*_args, **_kwargs):
        raise RuntimeError("provider implementation failure")

    monkeypatch.setattr(main.orchestrator, "_run_provider", failed_provider)
    path = (
        f"/api/conversations/{conversation['id']}"
        "/turns/original-turn/regenerate"
    )
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            path,
            json={
                "target": "answer",
                "provider": "claude",
                "regeneration_id": "failed-regeneration",
            },
        )

    assert response.status_code == 500
    attempt = main.store.load(conversation["id"])["turns"][0]["attempts"][-1]
    assert attempt["status"] == "interrupted"
    assert attempt["cancelled"] is False
    assert budget.settled == [("failed-regeneration", False)]


@pytest.mark.asyncio
async def test_chat_replans_after_claim_before_reservation(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    calls = 0
    provider_calls = 0

    def changing_plan(_req):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _allowed_plan()
        return {
            "allowed": False,
            "block_reasons": ["policy_blocked"],
            "billable": False,
            "policy": {"action": "block"},
        }

    async def must_not_run(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError

    monkeypatch.setattr(main, "_plan_from_request", changing_plan)
    monkeypatch.setattr(main.orchestrator, "run_turn", must_not_run)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/chat",
            json={"message": "question", "request_id": "toctou-request"},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "policy_blocked"
    assert calls == 2
    assert provider_calls == 0


@pytest.mark.asyncio
async def test_chat_persists_dispatch_snapshot_and_refreshes_budget(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    budget = _FakeBudget()
    monkeypatch.setattr(main, "budget_guard", budget)
    conversation = main.store.create("snapshot")
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/chat",
            json={
                "conversation_id": conversation["id"],
                "message": "snapshot question",
                "providers": ["claude"],
                "synthesize": False,
                "request_id": "snapshot-request",
            },
        )

    assert response.status_code == 200
    assert [item[0] for item in budget.refreshed] == ["snapshot-request"]
    refreshed_plan = budget.refreshed[0][1]
    planned_models = {
        item["name"]: item["model"] for item in refreshed_plan.get("providers") or []
    }
    saved_turn = main.store.load(conversation["id"])["turns"][-1]
    assert saved_turn["budget_reservation"]["request_id"] == "snapshot-request"
    assert saved_turn["execution_snapshot"]["providers"] == planned_models
    assert saved_turn["status"] == "completed"


@pytest.mark.asyncio
async def test_lock_pool_and_rate_limiter_drop_inactive_keys():
    pool = main.ConversationLockPool()
    for index in range(50):
        async with pool.hold(f"conversation-{index}"):
            pass
    assert pool._entries == {}

    now = 10.0
    limiter = main.SlidingWindowLimiter(
        100,
        window_sec=1.0,
        clock=lambda: now,
    )
    for index in range(50):
        await limiter.check(f"client-{index}", f"request-{index:03d}")
    now = 12.0
    await limiter.check("fresh-client", "fresh-request")
    assert set(limiter._entries) == {"fresh-client"}


@pytest.mark.asyncio
async def test_telemetry_full_scans_do_not_block_event_loop(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)

    def slow_local(_store):
        time.sleep(0.08)
        return {"providers": []}

    class SlowBudget(_FakeBudget):
        def public_snapshot(self, _store):
            time.sleep(0.08)
            return {}

    async def admin_snapshot():
        return {}

    monkeypatch.setattr(main.telemetry, "local_snapshot", slow_local)
    monkeypatch.setattr(main, "budget_guard", SlowBudget())
    monkeypatch.setattr(main.admin_telemetry, "snapshot", admin_snapshot)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        request_task = asyncio.create_task(client.get("/api/telemetry"))
        start = time.monotonic()
        await asyncio.sleep(0.01)
        elapsed = time.monotonic() - start
        response = await request_task

    assert elapsed < 0.05
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_chat_conversation_save_does_not_block_event_loop(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "budget_guard", _FakeBudget())
    conversation = main.store.create("slow save")
    original_save = main.store.save

    def slow_save(data):
        time.sleep(0.08)
        original_save(data)

    monkeypatch.setattr(main.store, "save", slow_save)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        request_task = asyncio.create_task(
            client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "message": "event loop remains responsive",
                    "request_id": "slow-save-request",
                },
            )
        )
        started = time.monotonic()
        await asyncio.sleep(0.01)
        elapsed = time.monotonic() - started
        response = await request_task

    assert elapsed < 0.05
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_upload_and_conversation_delete_are_serialized(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    conversation = main.store.create("upload race")
    upload_entered = asyncio.Event()
    release_upload = asyncio.Event()
    original_save_upload = main.attachment_store.save_upload

    async def delayed_save_upload(conversation_id, upload):
        upload_entered.set()
        await release_upload.wait()
        return await original_save_upload(conversation_id, upload)

    monkeypatch.setattr(main.attachment_store, "save_upload", delayed_save_upload)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        upload_task = asyncio.create_task(
            client.post(
                f"/api/conversations/{conversation['id']}/attachments",
                files={"file": ("note.txt", b"race-safe", "text/plain")},
            )
        )
        await asyncio.wait_for(upload_entered.wait(), timeout=2)
        delete_task = asyncio.create_task(
            client.delete(f"/api/conversations/{conversation['id']}")
        )
        await asyncio.sleep(0.02)
        assert delete_task.done() is False
        release_upload.set()
        upload_response, delete_response = await asyncio.gather(
            upload_task,
            delete_task,
        )

    assert upload_response.status_code == 200
    assert delete_response.status_code == 200
    assert not (main.attachment_store.root / conversation["id"]).exists()


@pytest.mark.asyncio
async def test_non_billing_events_do_not_rewrite_the_whole_conversation(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    conversation = main.store.create("durability")
    original_save = main.store.save
    save_calls = 0

    def counted_save(data):
        nonlocal save_calls
        save_calls += 1
        original_save(data)

    async def emits_non_billing_events(
        conversation_data,
        raw_message,
        options,
        request_id,
        emit,
    ):
        await emit(
            "meta",
            {"request_id": request_id, "conversation_id": conversation_data["id"]},
        )
        await emit("insights", {"agreement_score": 0.0})
        return {
            "request_id": request_id,
            "message": raw_message,
            "clean_message": raw_message,
            "options": options.public_dict(),
            "answers": {},
            "insights": {"agreement_score": 0.0},
            "synthesis": {
                "ok": True,
                "source": "none",
                "skipped": True,
                "usage": {},
            },
        }

    monkeypatch.setattr(main.store, "save", counted_save)
    monkeypatch.setattr(main.orchestrator, "run_turn", emits_non_billing_events)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/chat",
            json={
                "conversation_id": conversation["id"],
                "message": "question",
                "request_id": "save-throttling",
            },
        )

    assert response.status_code == 200
    # pending claimと最終turnだけ。meta/insightsでは会話全体を再保存しない。
    assert save_calls == 2


def test_health_settings_plan_and_policy_scan_use_public_scrubber(
    tmp_path,
    monkeypatch,
):
    _reset(tmp_path, monkeypatch)
    secret = "opaque-central-scrubber-secret"
    monkeypatch.setattr(config, "secret_values", lambda: (secret,))
    monkeypatch.setattr(config, "mode", lambda: secret)
    monkeypatch.setattr(config, "public_settings", lambda: {"leak": secret})
    monkeypatch.setattr(main, "budget_guard", _FakeBudget())
    monkeypatch.setattr(
        main,
        "_plan_from_request",
        lambda _req: {**_allowed_plan(), "leak": secret},
    )
    monkeypatch.setattr(
        main.policy,
        "scan_text",
        lambda _text: {"action": "allow", "leak": secret, "findings": []},
    )
    sync_client = TestClient(main.app)
    responses = [
        sync_client.get("/api/health"),
        sync_client.get("/api/settings"),
        sync_client.post("/api/plan", json={"message": "safe"}),
        sync_client.post("/api/policy/scan", json={"text": "safe"}),
    ]
    assert all(response.status_code == 200 for response in responses)
    assert all(secret not in response.text for response in responses)
