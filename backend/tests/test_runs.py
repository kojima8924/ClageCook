# -*- coding: utf-8 -*-

import asyncio

import pytest

import runs


@pytest.mark.asyncio
async def test_run_state_sanitizes_encodes_and_replays_events():
    sanitized = []

    def sanitize(event, data):
        sanitized.append(event)
        return {"value": data["value"].upper()}

    state = runs.RunState(
        request_id="request-1",
        conversation_id="conversation-1",
        request_fingerprint="fingerprint-1",
        sanitize_event=sanitize,
        encode_event=lambda event, data, event_id: (
            f"{event_id}:{event}:{data['value']}"
        ),
    )
    await state.publish("answer", {"value": "safe"})
    await state.finish()

    replay = [item async for item in state.subscribe()]

    assert sanitized == ["answer"]
    assert replay == ["1:answer:SAFE"]
    assert state.done is True
    assert await state.wait_finished(timeout=0.01) is True


@pytest.mark.asyncio
async def test_registry_start_waits_for_runner_ownership_before_cancelling_caller():
    hook_entered = asyncio.Event()
    allow_runner = asyncio.Event()
    runner_entered = asyncio.Event()
    runner_finished = asyncio.Event()

    async def before_runner_enter():
        hook_entered.set()
        await allow_runner.wait()

    async def runner():
        runner_entered.set()
        try:
            await asyncio.Future()
        finally:
            runner_finished.set()

    registry = runs.RunRegistry(before_runner_enter=before_runner_enter)
    state, created = await registry.claim(
        "request-2",
        lambda: runs.RunState(
            request_id="request-2",
            conversation_id="conversation-2",
            request_fingerprint="fingerprint-2",
        ),
    )
    assert created is True

    start_task = asyncio.create_task(registry.start(state, runner))
    await asyncio.wait_for(hook_entered.wait(), timeout=1)
    start_task.cancel()
    await asyncio.sleep(0)
    allow_runner.set()

    with pytest.raises(asyncio.CancelledError):
        await start_task
    assert runner_entered.is_set()
    assert state.task is not None
    assert not state.task.done()

    state.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await state.task
    assert runner_finished.is_set()


@pytest.mark.asyncio
async def test_registry_claim_cancel_and_retention_are_atomic():
    clock_value = 100.0
    registry = runs.RunRegistry(retention_sec=5, clock=lambda: clock_value)
    factory_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return runs.RunState(
            request_id="same-request",
            conversation_id="conversation",
            request_fingerprint="fingerprint",
            clock=lambda: clock_value,
        )

    first, created = await registry.claim("same-request", factory)
    duplicate, duplicate_created = await registry.claim("same-request", factory)
    assert created is True
    assert duplicate_created is False
    assert duplicate is first
    assert factory_calls == 1

    cancelled, task, already_done = await registry.request_cancel("same-request")
    assert cancelled is first
    assert task is None
    assert already_done is False
    assert first.cancel_requested is True

    await first.finish()
    assert first.terminal_outcome == "cancelled"
    clock_value = 106.0
    assert await registry.lookup("same-request") is None


@pytest.mark.asyncio
async def test_conversation_pool_active_claim_and_limiter_cleanup():
    pool = runs.ConversationLockPool()
    async with pool.hold("conversation"):
        assert "conversation" in pool._entries
    assert pool._entries == {}

    active = {}
    guard = asyncio.Lock()
    assert await runs.claim_conversation_run(active, guard, "c1", "r1") is True
    assert await runs.claim_conversation_run(active, guard, "c1", "r2") is False
    await runs.release_conversation_run(active, guard, "c1", "r2")
    assert active == {"c1": "r1"}
    await runs.release_conversation_run(active, guard, "c1", "r1")
    assert active == {}

    now = 10.0
    limiter = runs.SlidingWindowLimiter(
        1,
        window_sec=5,
        clock=lambda: now,
        error_factory=lambda: ValueError("limited"),
    )
    await limiter.check("client", "request-1")
    await limiter.check("client", "request-1")
    with pytest.raises(ValueError, match="limited"):
        await limiter.check("client", "request-2")
    now = 16.0
    await limiter.check("fresh", "request-3")
    assert set(limiter._entries) == {"fresh"}
