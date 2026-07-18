# -*- coding: utf-8 -*-
"""実行状態、同時実行制御、rate limitの再利用可能な基盤。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, MutableMapping


EventData = dict[str, Any]
EventSanitizer = Callable[[str, EventData], EventData]
EventEncoder = Callable[[str, EventData, int], str]
Clock = Callable[[], float]
TerminalOutcome = Literal["completed", "cancelled", "failed"]


def _copy_event(_event: str, data: EventData) -> EventData:
    return deepcopy(data)


def _encode_event(event: str, data: EventData, event_id: int) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: {event}\ndata: {payload}\n\n"


@dataclass(slots=True)
class RunState:
    """chatとregenerationが共有する、接続から独立した実行状態。"""

    request_id: str
    conversation_id: str
    request_fingerprint: str
    kind: Literal["chat", "regeneration"] = "chat"
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sanitize_event: EventSanitizer = field(default=_copy_event, repr=False)
    encode_event: EventEncoder = field(default=_encode_event, repr=False)
    ping_interval: float = 15.0
    clock: Clock = field(default=time.monotonic, repr=False)
    wall_clock: Clock = field(default=time.time, repr=False)
    events: list[tuple[str, EventData]] = field(default_factory=list)
    done: bool = False
    created_at: float = field(init=False)
    completed_at: float | None = None
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task | None = None
    cancel_requested: bool = False
    result: dict[str, Any] | None = None
    failure_status: int | None = None
    failure_detail: Any = None
    terminal_outcome: TerminalOutcome | None = None

    def __post_init__(self) -> None:
        self.created_at = self.clock()

    async def publish(self, event: str, data: EventData) -> None:
        sanitized = self.sanitize_event(event, data)
        if not isinstance(sanitized, dict):
            raise TypeError("event sanitizer must return a dict")
        async with self.condition:
            self.events.append((event, sanitized))
            self.condition.notify_all()

    async def finish(self, outcome: TerminalOutcome | None = None) -> None:
        async with self.condition:
            if self.done:
                return
            if outcome is not None:
                self.terminal_outcome = outcome
            elif self.terminal_outcome is None:
                for event, data in reversed(self.events):
                    if event != "done":
                        continue
                    if data.get("cancelled") is True:
                        self.terminal_outcome = "cancelled"
                    elif data.get("failed") is True:
                        self.terminal_outcome = "failed"
                    else:
                        self.terminal_outcome = "completed"
                    break
                if self.terminal_outcome is None and self.result is not None:
                    self.terminal_outcome = "completed"
                if self.terminal_outcome is None and self.failure_status is not None:
                    self.terminal_outcome = "failed"
                if self.terminal_outcome is None and self.cancel_requested:
                    self.terminal_outcome = "cancelled"
            self.done = True
            self.completed_at = self.clock()
            self.condition.notify_all()

    async def wait_finished(self, timeout: float | None = None) -> bool:
        async with self.condition:
            if self.done:
                return True
            try:
                await asyncio.wait_for(
                    self.condition.wait_for(lambda: self.done),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                return False
            return True

    async def subscribe(self, start_index: int = 0) -> AsyncIterator[str]:
        index = max(0, start_index)
        while True:
            item: tuple[str, EventData] | None = None
            timed_out = False
            async with self.condition:
                if index < len(self.events):
                    item = self.events[index]
                    index += 1
                elif self.done:
                    break
                else:
                    try:
                        await asyncio.wait_for(
                            self.condition.wait(),
                            timeout=self.ping_interval,
                        )
                    except asyncio.TimeoutError:
                        timed_out = True
            if item is not None:
                yield self.encode_event(item[0], item[1], index)
            elif timed_out:
                yield f": ping {int(self.wall_clock())}\n\n"


class RunRegistry:
    """request_idごとのRunStateをclaim/start/cancelまで原子的に管理する。"""

    def __init__(
        self,
        *,
        retention_sec: float = 300.0,
        clock: Clock = time.monotonic,
        before_runner_enter: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._runs: dict[str, RunState] = {}
        self._lock = asyncio.Lock()
        self._retention_sec = retention_sec
        self._clock = clock
        self._before_runner_enter = before_runner_enter

    async def claim(
        self,
        request_id: str,
        state_factory: Callable[[], RunState],
    ) -> tuple[RunState, bool]:
        async with self._lock:
            self._cleanup_locked()
            existing = self._runs.get(request_id)
            if existing is not None:
                return existing, False
            state = state_factory()
            if state.request_id != request_id:
                raise ValueError("state_factory returned a mismatched request_id")
            self._runs[request_id] = state
            return state, True

    async def lookup(self, request_id: str) -> RunState | None:
        async with self._lock:
            self._cleanup_locked()
            return self._runs.get(request_id)

    async def remove(self, request_id: str, state: RunState) -> None:
        async with self._lock:
            if self._runs.get(request_id) is state:
                self._runs.pop(request_id, None)

    async def start(
        self,
        state: RunState,
        runner: Callable[[], Awaitable[None]],
    ) -> bool:
        """cancel flag確認とrunnerのtry/finally進入をcancelと直列化する。"""
        async with self._lock:
            if self._runs.get(state.request_id) is not state:
                return False
            if state.task is not None:
                return True
            if state.cancel_requested or state.done:
                return False
            entered = asyncio.Event()

            async def guarded_runner() -> None:
                if self._before_runner_enter is not None:
                    await self._before_runner_enter()
                entered.set()
                await runner()

            state.task = asyncio.create_task(guarded_runner())
            try:
                await asyncio.shield(entered.wait())
            except asyncio.CancelledError:
                # runnerが所有する後始末へ入る前にcallerだけが抜けると、
                # 予約やconversation claimを二重解放し得る。進入を確認してから
                # cancellationを伝播し、caller側はstate.taskで所有権を判定する。
                await entered.wait()
                raise
            return True

    async def request_cancel(
        self,
        request_id: str,
    ) -> tuple[RunState | None, asyncio.Task | None, bool]:
        async with self._lock:
            self._cleanup_locked()
            state = self._runs.get(request_id)
            if state is None:
                return None, None, False
            task = state.task
            if state.done or (task is not None and task.done()):
                return state, task, True
            if not state.cancel_requested:
                state.cancel_requested = True
                if task is not None:
                    task.cancel()
            return state, task, False

    async def shutdown(self) -> int:
        """全background runnerへcancelを一度だけ通知し、cleanup完了まで待つ。"""
        async with self._lock:
            states = list(self._runs.values())
            tasks: list[asyncio.Task] = []
            for state in states:
                task = state.task
                if state.done:
                    continue
                if not state.cancel_requested:
                    state.cancel_requested = True
                    if task is not None and not task.done():
                        task.cancel()
                if task is not None and not task.done():
                    tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for state in states:
            if not state.done:
                await state.finish(
                    "cancelled" if state.cancel_requested else "failed"
                )
        return len(tasks)

    def _cleanup_locked(self) -> None:
        now = self._clock()
        stale = [
            request_id
            for request_id, run in self._runs.items()
            if run.done
            and run.completed_at is not None
            and now - run.completed_at > self._retention_sec
        ]
        for request_id in stale:
            self._runs.pop(request_id, None)


@dataclass(slots=True)
class _ConversationLockEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class ConversationLockPool:
    """待機者を含む利用数が0になった会話lockを自動破棄する。"""

    def __init__(self) -> None:
        self._entries: dict[str, _ConversationLockEntry] = {}
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def hold(self, conversation_id: str):
        async with self._guard:
            entry = self._entries.get(conversation_id)
            if entry is None:
                entry = _ConversationLockEntry()
                self._entries[conversation_id] = entry
            entry.users += 1
        acquired = False
        try:
            await entry.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            async with self._guard:
                entry.users -= 1
                if entry.users == 0 and self._entries.get(conversation_id) is entry:
                    self._entries.pop(conversation_id, None)


@asynccontextmanager
async def hold_conversation_lock(pool: Any, conversation_id: str):
    """ConversationLockPoolとlegacy mapping lockを同じinterfaceで扱う。"""
    hold = getattr(pool, "hold", None)
    if callable(hold):
        async with hold(conversation_id):
            yield
        return
    async with pool[conversation_id]:
        yield


class RateLimitExceeded(RuntimeError):
    pass


class SlidingWindowLimiter:
    def __init__(
        self,
        limit: int,
        window_sec: float = 60.0,
        *,
        error_factory: Callable[[], Exception] | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_sec = window_sec
        self._entries: dict[str, deque[tuple[float, str]]] = {}
        self._lock = asyncio.Lock()
        self._next_global_cleanup = 0.0
        self._error_factory = error_factory or (
            lambda: RateLimitExceeded("sliding-window rate limit exceeded")
        )
        self._clock = clock

    async def check(self, key: str, request_id: str) -> None:
        now = self._clock()
        async with self._lock:
            if now >= self._next_global_cleanup:
                for stale_key, stale_entries in tuple(self._entries.items()):
                    while (
                        stale_entries
                        and now - stale_entries[0][0] >= self.window_sec
                    ):
                        stale_entries.popleft()
                    if not stale_entries:
                        self._entries.pop(stale_key, None)
                self._next_global_cleanup = now + self.window_sec
            entries = self._entries.get(key)
            if entries is None:
                entries = deque()
                self._entries[key] = entries
            while entries and now - entries[0][0] >= self.window_sec:
                entries.popleft()
            if any(existing == request_id for _, existing in entries):
                return
            if len(entries) >= self.limit:
                raise self._error_factory()
            entries.append((now, request_id))


async def run_blocking(
    function: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """同期I/Oをevent loop外へ出し、cancel時もworker完了を回収する。"""
    work = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        try:
            await work
        except Exception:
            pass
        raise


async def claim_conversation_run(
    active: MutableMapping[str, str],
    guard: asyncio.Lock,
    conversation_id: str,
    request_id: str,
) -> bool:
    async with guard:
        existing = active.get(conversation_id)
        if existing is not None and existing != request_id:
            return False
        active[conversation_id] = request_id
        return True


async def release_conversation_run(
    active: MutableMapping[str, str],
    guard: asyncio.Lock,
    conversation_id: str,
    request_id: str,
) -> None:
    async with guard:
        if active.get(conversation_id) == request_id:
            active.pop(conversation_id, None)
