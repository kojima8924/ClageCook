# -*- coding: utf-8 -*-
"""Clage Cook FastAPIサーバー。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import threading
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Awaitable, BinaryIO, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

import config
# 再export: 旧来のmain.PlanRequest等を参照するテスト・呼出元との互換維持。
from api_models import (
    ChatRequest,
    ConversationMemoryUpdateRequest,
    PlanRequest,
    PolicyScanRequest,
    ReconciliationReleaseRequest,
    RegenerationPlanRequest,
    RegenerationRequest,
    RenameRequest,
    RuntimeSettingsUpdateRequest,
    SearchRequest,
    _valid_request_id,
)
# 再export: tests(main._sanitize_event_data / main._scrub_public)と
# ConversationStoreのsanitizer引数の互換維持。
from sanitizing import (
    _sanitize_event_data,
    _sanitize_provider_error,
    _sanitize_turn,
    _scrub_public,
    _scrub_public_dict,
)
import admin_telemetry
import attachments
import finance
import orchestrator
import planning
import policy
import runs
import telemetry
from runs import ConversationLockPool, RunRegistry, RunState, SlidingWindowLimiter
from runs import run_blocking as _blocking_call
from storage import (
    AmbiguousRequestId,
    ConversationNotFound,
    ConversationStore,
    utc_now,
)
from runtime_settings import RuntimeSettingsConflict, RuntimeSettingsError


logger = logging.getLogger("clage_cook")


class _SingleProcessGuard:
    """同一データ領域を複数プロセスが同時利用することを防ぐ。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: BinaryIO | None = None
        self._depth = 0
        self._mutex = threading.Lock()

    def acquire(self) -> None:
        with self._mutex:
            if self._depth:
                self._depth += 1
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
            try:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, IOError) as exc:
                handle.close()
                raise RuntimeError(
                    "同じCLAGE_DATA_DIRを使う複数プロセス起動は安全ではありません。"
                    "uvicornは--workers 1で起動してください。"
                ) from exc
            self._handle = handle
            self._depth = 1

    def release(self) -> None:
        with self._mutex:
            if self._depth == 0:
                return
            self._depth -= 1
            if self._depth:
                return
            handle = self._handle
            self._handle = None
            if handle is None:
                return
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


_single_process_guard = _SingleProcessGuard(config.DATA_DIR / ".server.lock")


def _validate_startup_safety() -> None:
    budget_guard.validate_configuration()
    if (config.LIVE_API_ENABLED or config.ADMIN_TELEMETRY_ENABLED) and not config.AUTH_TOKEN:
        raise RuntimeError(
            "実APIまたは管理telemetryを有効にする場合は、"
            "CLAGE_AUTH_TOKENを必ず設定してください。"
        )


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _validate_startup_safety()
    _single_process_guard.acquire()
    try:
        recovered_budget = budget_guard.recover_orphaned_reservations()
        if recovered_budget:
            logger.warning(
                "recovered %s budget reservation(s) as reconciliation_pending",
                recovered_budget,
            )
        recovered = store.recover_interrupted_turns()
        if recovered:
            logger.warning(
                "recovered %s orphaned running turn(s) as interrupted",
                recovered,
            )
        purged_attachments = await asyncio.to_thread(attachment_store.purge_expired)
        if purged_attachments:
            logger.info(
                "purged %s expired attachment(s) during startup",
                purged_attachments,
            )
        yield
    finally:
        drained = await _registry.shutdown()
        if drained:
            logger.info("drained %s background run(s) during shutdown", drained)
        async with _active_conversation_guard:
            _active_conversation_runs.clear()
        _single_process_guard.release()


app = FastAPI(
    title="Clage Cook",
    version="0.2.0",
    description="BYOK multi-model AI conference API",
    lifespan=_lifespan,
)


@app.exception_handler(RequestValidationError)
async def request_validation_error(
    _request: Request,
    _error: RequestValidationError,
) -> JSONResponse:
    """入力値やvalidatorの詳細を反射しない固定応答を返す。"""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": {
                "code": "invalid_request",
                "message": "リクエスト形式が不正です。",
            }
        },
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=list(config.CORS_ORIGINS),
    allow_origin_regex=config.CORS_ORIGIN_REGEX or None,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Last-Event-ID",
        "X-Request-ID",
    ],
    expose_headers=["X-Conversation-ID", "X-Request-ID"],
)


store = ConversationStore(config.DATA_DIR, sanitizer=_scrub_public)
attachment_store = attachments.AttachmentStore(config.DATA_DIR)
budget_guard = finance.BudgetGuard(config.DATA_DIR)
_run_slots = asyncio.Semaphore(config.MAX_CONCURRENT_RUNS)
_conversation_locks: Any = ConversationLockPool()
_active_conversation_runs: dict[str, str] = {}
_active_conversation_guard = asyncio.Lock()


@asynccontextmanager
async def _hold_conversation_lock(conversation_id: str):
    """旧test fixtureのmapping lockとも互換な内部adapter。"""
    async with runs.hold_conversation_lock(
        _conversation_locks,
        conversation_id,
    ):
        yield


def check_auth(request: Request) -> None:
    if not config.AUTH_TOKEN:
        return
    header = request.headers.get("authorization", "")
    supplied = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not supplied or not secrets.compare_digest(supplied, config.AUTH_TOKEN):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="認証に失敗しました")


def _rate_limit_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="1分あたりの会議開始上限を超えました",
        headers={"Retry-After": "60"},
    )


_rate_limiter = SlidingWindowLimiter(
    config.RATE_LIMIT_PER_MINUTE,
    error_factory=_rate_limit_error,
)


def _plan_from_request(req: PlanRequest) -> dict[str, Any]:
    conversation: dict[str, Any] | None = None
    attachment_bundle: tuple[str, list[dict[str, Any]]] | None = None
    if req.conversation_id is not None:
        canonical_id = _canonical_conversation_id(req.conversation_id)
        assert canonical_id is not None
        try:
            conversation = store.load(canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail="会話が見つかりません",
            ) from exc
        try:
            attachment_bundle = attachment_store.build_context(
                canonical_id, req.attachment_ids
            )
        except attachments.AttachmentError as exc:
            raise _attachment_http_exception(exc) from exc
    elif req.attachment_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "attachment_conversation_required",
                "message": "添付には保存先の会話が必要です",
            },
        )
    return _plan_from_snapshot(
        req,
        conversation,
        attachment_bundle=attachment_bundle,
        decorate_budget=True,
    )


def _plan_from_snapshot(
    req: PlanRequest,
    conversation: dict[str, Any] | None,
    *,
    attachment_bundle: tuple[str, list[dict[str, Any]]] | None,
    decorate_budget: bool,
) -> dict[str, Any]:
    """実行に使う会話・添付snapshotから、再読込なしでplanを構築する。"""
    history_text = (
        orchestrator._history_text(conversation) if conversation is not None else ""
    )
    memory = conversation.get("memory") if conversation is not None else None
    memory_text = str(memory.get("text") or "") if isinstance(memory, dict) else ""
    attachment_context, attachment_refs = attachment_bundle or ("", [])
    plan = planning.build_run_plan(
        message=req.message + attachment_context,
        tier=req.tier,
        reasoning_mode=req.reasoning_mode,
        debate=req.debate,
        providers=req.providers,
        synthesize=req.synthesize,
        blind=req.blind,
        web_search=req.web_search,
        history_text=history_text,
        memory_text=memory_text,
    )
    plan["attachments"] = {
        "count": len(attachment_refs),
        "items": attachment_refs,
        "text_included_count": sum(
            1 for item in attachment_refs if item.get("included_in_prompt") is True
        ),
    }
    binary_count = sum(
        1 for item in attachment_refs if item.get("included_in_prompt") is not True
    )
    if binary_count:
        plan.setdefault("warnings", []).append(
            {
                "code": "attachment_binary_not_sent",
                "message": (
                    f"{binary_count}件のbinary添付は保存されますが、現在のtext-only会議promptには"
                    "内容を送信しません。"
                ),
            }
        )
    return budget_guard.decorate_plan(plan, store) if decorate_budget else plan


def _execution_snapshot_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """実行直前planからmodel・出力上限・reasoning設定を凍結snapshot化する。

    chat実行と再生成実行が同じ形のexecution_snapshotを保存するための共通実装。
    """
    synthesizer_plan = plan.get("synthesizer")
    synthesizer_model = (
        str(synthesizer_plan["model"])
        if isinstance(synthesizer_plan, dict)
        and isinstance(synthesizer_plan.get("model"), str)
        else None
    )
    synthesizer_provider = (
        str(synthesizer_plan["name"])
        if isinstance(synthesizer_plan, dict)
        and isinstance(synthesizer_plan.get("name"), str)
        else None
    )
    return {
        "planned_at": utc_now(),
        "providers": {
            str(item["name"]): str(item["model"])
            for item in plan.get("providers") or []
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("model"), str)
        },
        "synthesizer": synthesizer_model,
        "synthesizer_provider": synthesizer_provider,
        "provider_execution": {
            str(item["name"]): {
                "model": str(item["model"]),
                "max_output_tokens": int(item["max_output_tokens"]),
                "reasoning": deepcopy(item.get("reasoning") or {}),
            }
            for item in plan.get("providers") or []
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("model"), str)
            and isinstance(item.get("max_output_tokens"), int)
        },
        "synthesizer_execution": (
            {
                "model": synthesizer_model,
                "max_output_tokens": int(synthesizer_plan["max_output_tokens"]),
                "reasoning": deepcopy(synthesizer_plan.get("reasoning") or {}),
            }
            if isinstance(synthesizer_plan, dict)
            and isinstance(synthesizer_plan.get("max_output_tokens"), int)
            else None
        ),
    }


def _enforce_run_limits(plan: dict[str, Any]) -> None:
    if plan["allowed"]:
        return
    reasons = set(plan.get("block_reasons") or [])
    if "policy_blocked" in reasons:
        code = "policy_blocked"
        message = "秘密情報らしい文字列を検出したため、会議を開始しませんでした。"
    elif "invalid_request" in reasons:
        code = "plan_invalid"
        message = "現在の設定ではこの会議を開始できません。"
    elif reasons & {
        "budget_cost_unknown",
        "per_run_budget_exceeded",
        "daily_budget_exceeded",
        "budget_reconciliation_backlog",
    }:
        code = "budget_limit_exceeded"
        message = "金額見積りまたは予算上限により会議を開始できません。"
    else:
        code = "run_limit_exceeded"
        message = "会議の最大実行量が設定上限を超えています。"
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": code,
            "message": message,
            "plan": plan,
        },
    )


def _enforce_explicit_confirmations(plan: dict[str, Any], req: ChatRequest) -> None:
    """UIを経由しない直接API呼出でも、live/PIIを明示確認なしに実行しない。"""
    missing: list[str] = []
    if plan.get("billable") and not req.confirm_live_api:
        missing.append("confirm_live_api")
    policy_result = plan.get("policy") or {}
    if (
        plan.get("billable")
        and policy_result.get("action") == "confirm"
        and not req.confirm_sensitive_data
    ):
        missing.append("confirm_sensitive_data")
    if not missing:
        return
    raise HTTPException(
        status_code=status.HTTP_428_PRECONDITION_REQUIRED,
        detail={
            "code": "explicit_confirmation_required",
            "message": (
                "実APIまたは個人情報らしい文字列の外部送信には、"
                "明示的な確認フラグが必要です。"
            ),
            "required": missing,
            "plan": plan,
        },
    )


def _request_fingerprint(req: ChatRequest) -> str:
    """同じrequest_idで同じ処理だけを再利用するための非秘密fingerprint。"""
    payload = {
        "message": req.message,
        "tier": req.tier,
        "reasoning_mode": req.reasoning_mode,
        "debate": req.debate,
        "providers": sorted(set(req.providers or [])),
        "synthesize": req.synthesize,
        "blind": req.blind,
        "web_search": req.web_search,
        # 添付blockは指定順でpromptへ入るため、順序も実行identityに含める。
        "attachment_ids": list(dict.fromkeys(req.attachment_ids)),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_registry = RunRegistry(retention_sec=config.RUN_RETENTION_SEC)


async def _claim_conversation_run(conversation_id: str, request_id: str) -> bool:
    return await runs.claim_conversation_run(
        _active_conversation_runs,
        _active_conversation_guard,
        conversation_id,
        request_id,
    )


async def _release_conversation_run(conversation_id: str, request_id: str) -> None:
    await runs.release_conversation_run(
        _active_conversation_runs,
        _active_conversation_guard,
        conversation_id,
        request_id,
    )


async def _reject_destructive_change_during_run(conversation_id: str) -> None:
    """Provider実行中にsnapshotや課金結果を破壊する操作を409で止める。"""
    async with _active_conversation_guard:
        active_request_id = _active_conversation_runs.get(conversation_id)
    if active_request_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "conversation_busy",
                "message": "この会話では生成処理中のため、分岐または削除は完了後に再試行してください。",
            },
        )


async def _finalize_background_run(state: RunState) -> None:
    """追加cancelからcleanupを隔離し、claim解放とdone通知を必ず完了する。"""

    async def cleanup() -> None:
        try:
            await _release_conversation_run(
                state.conversation_id,
                state.request_id,
            )
        finally:
            await state.finish()

    cleanup_task = asyncio.create_task(cleanup())
    cancelled = False
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            cancelled = True
    await cleanup_task
    if cancelled and state.terminal_outcome is None:
        raise asyncio.CancelledError


async def _complete_critical(
    awaitable: Awaitable[Any],
) -> tuple[Any, bool]:
    """終端処理を完走し、待機中にcancel要求を受けたかも返す。"""
    task = asyncio.create_task(awaitable)
    cancellation_received = False
    while True:
        try:
            return await asyncio.shield(task), cancellation_received
        except asyncio.CancelledError:
            cancellation_received = True
            if task.done():
                return task.result(), cancellation_received


async def _finalize_budget_after_abort(
    request_id: str,
    *,
    dispatch_started: bool,
) -> None:
    """abort時の予算清算を1箇所に集約する。

    外部Provider呼出し(dispatch)開始後は課金が発生し得るためsettle(未照合)へ、
    開始前なら予約をそのまま解放する。
    """
    if dispatch_started:
        await _blocking_call(
            budget_guard.settle,
            request_id,
            usage_reconciled=False,
        )
    else:
        await _blocking_call(
            budget_guard.release_undispatched,
            request_id,
        )


def _sse(event: str, data: dict[str, Any], event_id: int) -> str:
    payload = json.dumps(
        _scrub_public(data),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {event_id}\nevent: {event}\ndata: {payload}\n\n"


def _new_run_state(
    request_id: str,
    conversation_id: str,
    request_fingerprint: str,
    *,
    kind: Literal["chat", "regeneration"] = "chat",
) -> RunState:
    return RunState(
        request_id=request_id,
        conversation_id=conversation_id,
        request_fingerprint=request_fingerprint,
        kind=kind,
        sanitize_event=_sanitize_event_data,
        encode_event=_sse,
        ping_interval=config.SSE_PING_SEC,
    )


def _events_from_saved_turn(conversation: dict[str, Any], turn: dict[str, Any]) -> list:
    journal = turn.get("event_log")
    if isinstance(journal, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("event"), str)
        and isinstance(item.get("data"), dict)
        for item in journal
    ):
        events = []
        for item in journal:
            event = str(item["event"])
            data = deepcopy(item["data"])
            if event == "meta":
                data["replayed"] = True
            events.append((event, data))
        saved_status = str(turn.get("status") or "completed")
        terminal_failure = turn.get("cancelled") or saved_status in {
            "running",
            "failed",
            "interrupted",
        }
        if terminal_failure:
            cancelled = bool(turn.get("cancelled"))
            interrupted = saved_status in {"running", "interrupted"}
            events.append(
                (
                    "error",
                    {
                        "request_id": turn.get("request_id"),
                        "message": (
                            "会議がキャンセルされました"
                            if cancelled
                            else "前回の会議はサーバー停止またはエラーで完了しませんでした"
                        ),
                        **({"cancelled": True} if cancelled else {}),
                        **({"interrupted": True} if interrupted else {}),
                    },
                )
            )
        events.append(
            (
                "done",
                {
                    "request_id": turn.get("request_id"),
                    "conversation": store.summary(conversation),
                    "replayed": True,
                    **(
                        {
                            "failed": True,
                            **(
                                {"cancelled": True}
                                if turn.get("cancelled")
                                else {}
                            ),
                            **(
                                {"interrupted": True}
                                if saved_status in {"running", "interrupted"}
                                else {}
                            ),
                        }
                        if terminal_failure
                        else {}
                    ),
                },
            )
        )
        return events

    options = turn.get("options") or {}
    events: list[tuple[str, dict[str, Any]]] = [
        (
            "meta",
            {
                "request_id": turn.get("request_id"),
                "conversation_id": conversation["id"],
                "backends": options.get("providers") or list((turn.get("answers") or {}).keys()),
                "mode": config.mode(),
                "tier": options.get("tier", "balanced"),
                "debate": bool(options.get("debate")),
                "blind": bool(options.get("blind")),
                "web_search": bool(options.get("web_search")),
                "synthesizer": config.synthesizer_name(),
                "provider_status": config.statuses(),
                "replayed": True,
            },
        )
    ]
    for source in config.WORKERS:
        answer = (turn.get("answers") or {}).get(source)
        if isinstance(answer, dict):
            events.append(("answer", answer))
    insights = turn.get("insights")
    if isinstance(insights, dict):
        events.append(("insights", insights))
    synthesis = turn.get("synthesis")
    if isinstance(synthesis, dict):
        events.append(("synthesis", synthesis))
    events.append(
        (
            "done",
            {
                "request_id": turn.get("request_id"),
                "conversation": store.summary(conversation),
                "replayed": True,
            },
        )
    )
    return events


def _new_pending_turn(
    req: ChatRequest,
    options: orchestrator.TurnOptions,
    state: RunState,
    attachment_refs: list[dict[str, Any]],
    *,
    budget_reservation: dict[str, Any] | None,
    execution_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """外部呼出より先に保存する、同一request_idのdurable claim。"""
    clean_message, effective, _help_requested = orchestrator.parse_controls(
        req.message,
        options,
    )
    return {
        "request_id": state.request_id,
        "request_fingerprint": state.request_fingerprint,
        "created_at": utc_now(),
        "message": req.message,
        "clean_message": clean_message,
        "attachment_ids": list(req.attachment_ids),
        "attachment_conversation_id": state.conversation_id,
        "attachments": deepcopy(attachment_refs),
        "budget_reservation": deepcopy(budget_reservation),
        "execution_snapshot": deepcopy(execution_snapshot),
        "options": effective.public_dict(),
        "resume_request": {
            "tier": req.tier,
            "reasoning_mode": req.reasoning_mode,
            "debate": req.debate,
            "providers": req.providers,
            "synthesize": req.synthesize,
            "blind": req.blind,
            "web_search": req.web_search,
            "confirm_live_api": req.confirm_live_api,
            "confirm_sensitive_data": req.confirm_sensitive_data,
            "attachment_ids": list(req.attachment_ids),
        },
        "answers": {},
        "insights": orchestrator.analyze_insights([]),
        "synthesis": {
            "ok": False,
            "error": "会議を実行中です",
            "source": "none",
            "usage": {},
            "skipped": False,
            "pending": True,
        },
        "status": "running",
        "usage_may_be_incomplete": True,
        "event_log": [],
    }


def _sync_partial_turn_from_events(
    turn: dict[str, Any],
    state: RunState,
    *,
    run_status: Literal["running", "cancelled", "failed"],
) -> None:
    """sanitized SSE journalを保存turnへ反映し、課金済み部分を失わない。"""
    answers: dict[str, dict[str, Any]] = {}
    insights: dict[str, Any] | None = None
    synthesis: dict[str, Any] | None = None
    for event, data in state.events:
        if event == "answer" and isinstance(data.get("source"), str):
            answers[data["source"]] = deepcopy(data)
        elif event == "insights":
            insights = deepcopy(data)
        elif event == "synthesis":
            synthesis = deepcopy(data)

    if insights is None:
        insights = orchestrator.analyze_insights(
            [
                {"source": source, "text": str(answer.get("text") or "")}
                for source, answer in answers.items()
                if answer.get("ok")
            ]
        )
    if synthesis is None and run_status != "running":
        cancelled = run_status == "cancelled"
        synthesis = {
            "ok": False,
            "error": (
                "会議がキャンセルされました"
                if cancelled
                else "会議の実行中にサーバーエラーが発生しました"
            ),
            "source": "none",
            "usage": {},
            "skipped": False,
            "cancelled": cancelled,
        }
    turn["answers"] = answers
    turn["insights"] = insights
    if synthesis is not None:
        turn["synthesis"] = synthesis
    turn["status"] = run_status
    turn["cancelled"] = run_status == "cancelled"
    turn["failed"] = run_status == "failed"
    turn["usage_may_be_incomplete"] = run_status != "completed"
    turn["event_log"] = [
        {"event": event, "data": deepcopy(data)}
        for event, data in state.events
    ]


async def _persist_incomplete_chat_turn(
    state: RunState,
    *,
    run_status: Literal["cancelled", "failed"],
) -> dict[str, Any] | None:
    """pending保存後のouter例外を、最新conversationへdurableに確定する。"""
    async with _hold_conversation_lock(state.conversation_id):
        try:
            conversation = await _blocking_call(
                store.load,
                state.conversation_id,
            )
            turn_index = _turn_index_by_request_id(
                conversation,
                state.request_id,
            )
        except (ConversationNotFound, HTTPException):
            return None
        current = conversation["turns"][turn_index]
        if current.get("status") == "completed":
            return store.summary(conversation)
        _sync_partial_turn_from_events(
            current,
            state,
            run_status=run_status,
        )
        conversation["turns"][turn_index] = _sanitize_turn(current)
        await _blocking_call(store.save, conversation)
        return store.summary(conversation)


async def _execute_run(state: RunState, req: ChatRequest) -> None:
    cancelled_summary: dict[str, Any] | None = None
    failed_summary: dict[str, Any] | None = None
    dispatch_started = False
    pending_persisted = False
    terminal_persisted = False
    budget_finalized = False
    try:
        async with _run_slots:
            async with _hold_conversation_lock(state.conversation_id):
                conversation = await _blocking_call(store.load, state.conversation_id)
                saved = store.find_turn_by_request_id(conversation, state.request_id)
                if saved is not None:
                    for event, data in _events_from_saved_turn(conversation, saved):
                        await state.publish(event, data)
                    return

                attachment_context, attachment_refs = await _blocking_call(
                    attachment_store.build_context,
                    state.conversation_id,
                    req.attachment_ids,
                )
                final_plan = _plan_from_snapshot(
                    req,
                    conversation,
                    attachment_bundle=(attachment_context, attachment_refs),
                    decorate_budget=False,
                )
                _enforce_run_limits(final_plan)
                _enforce_explicit_confirmations(final_plan, req)
                reservation = await _blocking_call(
                    budget_guard.refresh_reservation,
                    request_id=state.request_id,
                    request_fingerprint=state.request_fingerprint,
                    plan=final_plan,
                    store=store,
                    reservation_owner=state.execution_id,
                )
                execution_snapshot = _execution_snapshot_from_plan(final_plan)
                provider_models = execution_snapshot["providers"]
                synthesizer_model = execution_snapshot["synthesizer"]
                synthesizer_provider = execution_snapshot["synthesizer_provider"]
                selected_providers = tuple(provider_models)
                options = orchestrator.TurnOptions(
                    tier=req.tier,
                    reasoning_mode=req.reasoning_mode,
                    debate=req.debate,
                    providers=selected_providers,
                    synthesize=req.synthesize,
                    blind=req.blind,
                    web_search=req.web_search,
                )
                pending = _new_pending_turn(
                    req,
                    options,
                    state,
                    attachment_refs,
                    budget_reservation=reservation,
                    execution_snapshot=execution_snapshot,
                )
                if not conversation.get("turns") and pending.get(
                    "clean_message"
                ) not in {"", "!help"}:
                    conversation["title"] = " ".join(
                        pending["clean_message"].split()
                    )[:60]
                turns = conversation.setdefault("turns", [])
                turns.append(pending)
                pending_index = len(turns) - 1
                _, cancelled_while_saving_pending = await _complete_critical(
                    _blocking_call(store.save, conversation)
                )
                pending_persisted = True
                if cancelled_while_saving_pending:
                    raise asyncio.CancelledError

                async def durable_emit(event: str, data: dict[str, Any]) -> None:
                    await state.publish(event, data)
                    if event not in {"answer", "synthesis"}:
                        return
                    _sync_partial_turn_from_events(
                        pending,
                        state,
                        run_status="running",
                    )
                    await _blocking_call(store.save, conversation)

                dispatch_started = True
                with orchestrator.freeze_execution_models(
                    provider_models,
                    synthesizer_model,
                    synthesizer_provider,
                ):
                    if attachment_context:
                        turn = await orchestrator.run_turn(
                            conversation,
                            req.message,
                            options,
                            state.request_id,
                            durable_emit,
                            attachment_context=attachment_context,
                        )
                    else:
                        turn = await orchestrator.run_turn(
                            conversation,
                            req.message,
                            options,
                            state.request_id,
                            durable_emit,
                        )

                turn = _sanitize_turn(turn)
                turn["attachment_ids"] = list(req.attachment_ids)
                turn["attachment_conversation_id"] = state.conversation_id
                turn["attachments"] = deepcopy(attachment_refs)
                turn["request_fingerprint"] = state.request_fingerprint
                turn["budget_reservation"] = deepcopy(reservation)
                turn["execution_snapshot"] = deepcopy(execution_snapshot)
                turn["event_log"] = [
                    {"event": event, "data": deepcopy(data)}
                    for event, data in state.events
                ]
                turn["status"] = "completed"
                turn["cancelled"] = False
                turn["failed"] = False
                turn["usage_may_be_incomplete"] = False
                turns[pending_index] = turn

                async def finalize_completed_turn() -> None:
                    nonlocal terminal_persisted, budget_finalized
                    await _blocking_call(store.save, conversation)
                    terminal_persisted = True
                    await _blocking_call(
                        budget_guard.settle,
                        state.request_id,
                        usage_reconciled=finance.turn_usage_reconciled(turn),
                        turn=turn,
                    )
                    budget_finalized = True
                    state.terminal_outcome = "completed"
                    await state.publish(
                        "done",
                        {
                            "request_id": state.request_id,
                            "conversation": store.summary(conversation),
                        },
                    )

                # Provider結果が確定した後は、cancelが終端保存と予算確定を
                # 分断しないよう一連の処理を完走してcompletedを正とする。
                await _complete_critical(finalize_completed_turn())
    except asyncio.CancelledError:
        state.terminal_outcome = "cancelled"
        if pending_persisted and not terminal_persisted:
            cancelled_summary, _ = await _complete_critical(
                _persist_incomplete_chat_turn(
                    state,
                    run_status="cancelled",
                )
            )
            terminal_persisted = cancelled_summary is not None
        if not budget_finalized:
            await _complete_critical(
                _finalize_budget_after_abort(
                    state.request_id,
                    dispatch_started=dispatch_started,
                )
            )
            budget_finalized = True
        await state.publish(
            "error",
            {
                "request_id": state.request_id,
                "message": "会議がキャンセルされました",
                "cancelled": True,
            },
        )
        done_data: dict[str, Any] = {
            "request_id": state.request_id,
            "failed": True,
            "cancelled": True,
        }
        if cancelled_summary is not None:
            done_data["conversation"] = cancelled_summary
        await state.publish("done", done_data)
        raise
    except Exception as exc:
        state.terminal_outcome = "failed"
        if pending_persisted and not terminal_persisted:
            failed_summary, _ = await _complete_critical(
                _persist_incomplete_chat_turn(
                    state,
                    run_status="failed",
                )
            )
            terminal_persisted = failed_summary is not None
        if not budget_finalized:
            await _complete_critical(
                _finalize_budget_after_abort(
                    state.request_id,
                    dispatch_started=dispatch_started,
                )
            )
            budget_finalized = True
        logger.error(
            "run failed request_id=%s conversation_id=%s exception_type=%s",
            state.request_id,
            state.conversation_id,
            type(exc).__name__,
        )
        await state.publish(
            "error",
            {"request_id": state.request_id, "message": "会議に失敗しました"},
        )
        done_data = {"request_id": state.request_id, "failed": True}
        if failed_summary is not None:
            done_data["conversation"] = failed_summary
        await state.publish("done", done_data)
    finally:
        await _finalize_background_run(state)


def _resolve_request_id(req: ChatRequest, request: Request) -> str:
    header_request_id = request.headers.get("x-request-id")
    if header_request_id is not None and not _valid_request_id(header_request_id):
        raise HTTPException(status_code=422, detail="X-Request-IDが不正です")
    if req.request_id and header_request_id and req.request_id != header_request_id:
        raise HTTPException(
            status_code=400,
            detail="request_idとX-Request-IDが一致しません",
        )
    return req.request_id or header_request_id or str(uuid.uuid4())


def _last_event_index(request: Request) -> int:
    raw = request.headers.get("last-event-id")
    if raw is None or raw == "":
        return 0
    try:
        value = int(raw, 10)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Last-Event-IDが不正です") from exc
    if value < 0:
        raise HTTPException(status_code=400, detail="Last-Event-IDが不正です")
    return value


def _canonical_conversation_id(conversation_id: str | None) -> str | None:
    if conversation_id is None:
        return None
    try:
        return str(uuid.UUID(conversation_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=404, detail="会話が見つかりません") from exc


def _rate_limit_key(request: Request) -> str:
    host = request.client.host if request.client else "local"
    return f"client:{host}"


def _stream_response(state: RunState, start_index: int = 0) -> StreamingResponse:
    return StreamingResponse(
        state.subscribe(start_index),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Conversation-ID": state.conversation_id,
            "X-Request-ID": state.request_id,
        },
    )


@app.get("/api/health", dependencies=[Depends(check_auth)])
def health() -> dict[str, Any]:
    result = {
        "ok": True,
        "version": app.version,
        "mode": config.mode(),
        "active_workers": config.active_workers(),
        "synthesizer": config.synthesizer_name(),
        "auth_required": bool(config.AUTH_TOKEN),
        "single_process_enforced": True,
    }
    return _scrub_public_dict(result)


@app.get("/api/settings", dependencies=[Depends(check_auth)])
def settings() -> dict[str, Any]:
    result = config.public_settings()
    result["finance"] = budget_guard.public_snapshot(store)
    return _scrub_public_dict(result)


@app.patch("/api/settings/runtime", dependencies=[Depends(check_auth)])
def update_runtime_settings(req: RuntimeSettingsUpdateRequest) -> dict[str, Any]:
    """secretを扱わず、model IDと統合役だけをoptimistic lock付きで更新する。"""
    try:
        config.runtime_settings.update(
            expected_revision=req.expected_revision,
            models=req.models,
            synthesizer_provider=req.synthesizer_provider,
            synthesizer_models=req.synthesizer_models,
        )
    except RuntimeSettingsConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "runtime_settings_conflict",
                "message": str(exc),
                "settings": config.public_settings(),
            },
        ) from exc
    except RuntimeSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_runtime_settings", "message": str(exc)},
        ) from exc
    result = config.public_settings()
    result["finance"] = budget_guard.public_snapshot(store)
    return _scrub_public_dict(result)


@app.get("/api/telemetry", dependencies=[Depends(check_auth)])
async def usage_telemetry() -> dict[str, Any]:
    """local実績と、明示有効時だけ読み取り専用の管理telemetryを返す。"""
    snapshot, finance_snapshot, admin_snapshot = await asyncio.gather(
        asyncio.to_thread(telemetry.local_snapshot, store),
        asyncio.to_thread(budget_guard.public_snapshot, store),
        admin_telemetry.snapshot(),
    )
    snapshot["finance"] = finance_snapshot
    snapshot["admin"] = admin_snapshot
    return _scrub_public_dict(snapshot)


@app.post(
    "/api/budget/reconciliation/{request_id}/release",
    dependencies=[Depends(check_auth)],
)
def release_budget_reconciliation(
    request_id: str,
    req: ReconciliationReleaseRequest,
) -> dict[str, Any]:
    if not _valid_request_id(request_id):
        raise HTTPException(status_code=404, detail="対象の予算予約が見つかりません")
    scrubbed_note = _scrub_public(req.note.strip())
    note = scrubbed_note if isinstance(scrubbed_note, str) else ""
    try:
        reservation = budget_guard.release_after_manual_reconciliation(
            request_id,
            confirmed_no_unobserved_charge=req.confirmed_no_unobserved_charge,
            store=store,
            note=note,
        )
    except finance.ReconciliationError as exc:
        status_code = (
            status.HTTP_404_NOT_FOUND
            if exc.code == "reservation_not_found"
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    result = {
        "ok": True,
        "reservation": reservation,
        "finance": budget_guard.public_snapshot(store),
    }
    return _scrub_public_dict(result)


@app.post("/api/plan", dependencies=[Depends(check_auth)])
def plan(req: PlanRequest) -> dict[str, Any]:
    """課金APIを一切呼ばず、現在の設定による会議の安全側上限を返す。"""
    return _scrub_public_dict(_plan_from_request(req))


@app.post("/api/policy/scan", dependencies=[Depends(check_auth)])
def scan_policy(req: PolicyScanRequest) -> dict[str, Any]:
    """課金APIや外部サービスを使わず、送信前の文字列をローカル検査する。"""
    return _scrub_public_dict(policy.scan_text(req.text))


@app.post("/api/chat", dependencies=[Depends(check_auth)])
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    request_id = _resolve_request_id(req, request)
    start_index = _last_event_index(request)
    requested_conversation_id = _canonical_conversation_id(req.conversation_id)
    fingerprint = _request_fingerprint(req)
    try:
        found = await _blocking_call(
            store.find_conversation_by_request_id,
            request_id,
            preferred_conversation_id=requested_conversation_id,
        )
    except AmbiguousRequestId as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "request_id_ambiguous",
                "message": "request_idが複数の分岐会話に存在します。会話IDを指定してください。",
            },
        ) from exc
    if found is not None:
        found_conversation, found_turn = found
        if (
            requested_conversation_id is not None
            and requested_conversation_id != found_conversation["id"]
        ):
            raise HTTPException(
                status_code=409,
                detail="request_idが別の会話で使用済みです",
            )
        saved_fingerprint = found_turn.get("request_fingerprint")
        if isinstance(saved_fingerprint, str) and saved_fingerprint != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="request_idが異なるリクエストで使用済みです",
            )
        candidate_conversation_id = str(found_conversation["id"])
        create_conversation = False
    elif requested_conversation_id is not None:
        try:
            await _blocking_call(store.load, requested_conversation_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        candidate_conversation_id = requested_conversation_id
        create_conversation = False
    else:
        candidate_conversation_id = str(uuid.uuid4())
        create_conversation = True

    # 保存済み結果や実行中stateは課金を発生させない再生・joinである。
    # 現在の添付TTL、runtime設定、予算、確認フラグで再計画する前に返す。
    if found is not None:
        state, created = await _registry.claim(
            request_id,
            lambda: _new_run_state(
                request_id,
                candidate_conversation_id,
                fingerprint,
            ),
        )
        if state.kind != "chat" or state.request_fingerprint != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="request_idが異なるリクエストで使用済みです",
            )
        if (
            requested_conversation_id is not None
            and requested_conversation_id != state.conversation_id
        ):
            raise HTTPException(
                status_code=409,
                detail="request_idが別の会話で使用済みです",
            )
        if created:
            found_conversation, found_turn = found
            for event, data in _events_from_saved_turn(
                found_conversation,
                found_turn,
            ):
                await state.publish(event, data)
            await state.finish()
        if start_index > len(state.events):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "resume_not_available",
                    "message": "指定されたLast-Event-IDを再開できません",
                    "max_event_id": len(state.events),
                },
            )
        return _stream_response(state, start_index)

    resumable = await _registry.lookup(request_id)
    if resumable is not None:
        if (
            resumable.kind != "chat"
            or resumable.request_fingerprint != fingerprint
        ):
            raise HTTPException(
                status_code=409,
                detail="request_idが異なるリクエストで使用済みです",
            )
        if (
            requested_conversation_id is not None
            and requested_conversation_id != resumable.conversation_id
        ):
            raise HTTPException(
                status_code=409,
                detail="request_idが別の会話で使用済みです",
            )
        if start_index > len(resumable.events):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "resume_not_available",
                    "message": "指定されたLast-Event-IDを再開できません",
                    "max_event_id": len(resumable.events),
                },
            )
        return _stream_response(resumable, start_index)

    if start_index > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "resume_not_available",
                "message": "指定されたLast-Event-IDを再開できません",
                "max_event_id": 0,
            },
        )
    run_plan = await _blocking_call(_plan_from_request, req)
    _enforce_run_limits(run_plan)
    _enforce_explicit_confirmations(run_plan, req)
    await _rate_limiter.check(_rate_limit_key(request), request_id)
    state, created = await _registry.claim(
        request_id,
        lambda: _new_run_state(
            request_id,
            candidate_conversation_id,
            fingerprint,
        ),
    )
    if state.kind != "chat" or state.request_fingerprint != fingerprint:
        raise HTTPException(
            status_code=409,
            detail="request_idが異なるリクエストで使用済みです",
        )
    if (
        requested_conversation_id is not None
        and requested_conversation_id != state.conversation_id
    ):
        raise HTTPException(status_code=409, detail="request_idが別の会話で使用済みです")

    if created:
        try:
            if create_conversation:
                conversation_data = await _blocking_call(
                    store.create,
                    req.message,
                    conversation_id=state.conversation_id,
                )
            else:
                conversation_data = await _blocking_call(
                    store.load,
                    state.conversation_id,
                )
        except BaseException:
            await state.finish()
            await _registry.remove(request_id, state)
            raise
        saved_turn = store.find_turn_by_request_id(conversation_data, request_id)
        if saved_turn is not None:
            for event, data in _events_from_saved_turn(conversation_data, saved_turn):
                await state.publish(event, data)
            await state.finish()
        else:
            try:
                claimed = await _claim_conversation_run(
                    state.conversation_id,
                    state.request_id,
                )
            except BaseException:
                # claim完了境界でcallerがcancelされても、task未割当の
                # registry/会話claimを残さない。
                await _release_conversation_run(
                    state.conversation_id,
                    state.request_id,
                )
                await state.finish()
                await _registry.remove(request_id, state)
                raise
            if not claimed:
                await state.finish()
                await _registry.remove(request_id, state)
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "conversation_busy",
                        "message": "この会話では別の会議が実行中です。完了後に再試行してください。",
                    },
                )
            try:
                if state.cancel_requested:
                    await _release_conversation_run(
                        state.conversation_id,
                        state.request_id,
                    )
                    await state.publish(
                        "error",
                        {
                            "request_id": state.request_id,
                            "message": "会議がキャンセルされました",
                            "cancelled": True,
                        },
                    )
                    await state.publish(
                        "done",
                        {
                            "request_id": state.request_id,
                            "failed": True,
                            "cancelled": True,
                        },
                    )
                    await state.finish()
                    return _stream_response(state, start_index)

                # claim後に再計画し、履歴・memory・添付・予算のTOCTOUを閉じる。
                run_plan = await _blocking_call(_plan_from_request, req)
                _enforce_run_limits(run_plan)
                _enforce_explicit_confirmations(run_plan, req)
                await _blocking_call(
                    budget_guard.reserve,
                    request_id=state.request_id,
                    request_fingerprint=state.request_fingerprint,
                    plan=run_plan,
                    store=store,
                    reservation_owner=state.execution_id,
                )
                started = await _registry.start(
                    state,
                    lambda: _execute_run(state, req),
                )
                if not started:
                    await _blocking_call(
                        budget_guard.release_undispatched,
                        state.request_id,
                    )
                    await _release_conversation_run(
                        state.conversation_id,
                        state.request_id,
                    )
                    await state.publish(
                        "error",
                        {
                            "request_id": state.request_id,
                            "message": "会議がキャンセルされました",
                            "cancelled": True,
                        },
                    )
                    await state.publish(
                        "done",
                        {
                            "request_id": state.request_id,
                            "failed": True,
                            "cancelled": True,
                        },
                    )
                    await state.finish()
            except finance.BudgetViolation as exc:
                await _release_conversation_run(
                    state.conversation_id,
                    state.request_id,
                )
                await state.finish()
                await _registry.remove(request_id, state)
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": exc.code,
                        "message": str(exc),
                        "budget": exc.snapshot,
                    },
                ) from exc
            except BaseException:
                # task作成後は_execute_runが予算・conversation claimを所有する。
                # start handshake中にcallerがcancelされても二重解放しない。
                if state.task is None:
                    await _blocking_call(
                        budget_guard.release_undispatched,
                        state.request_id,
                    )
                    await _release_conversation_run(
                        state.conversation_id,
                        state.request_id,
                    )
                    await state.finish()
                    await _registry.remove(request_id, state)
                raise
    if start_index > len(state.events):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "resume_not_available",
                "message": "指定されたLast-Event-IDを再開できません",
                "max_event_id": len(state.events),
            },
        )
    return _stream_response(state, start_index)


@app.post("/api/runs/{request_id}/cancel", dependencies=[Depends(check_auth)])
async def cancel_run(request_id: str) -> dict[str, Any]:
    if not _valid_request_id(request_id):
        raise HTTPException(status_code=404, detail="実行中の会議が見つかりません")
    state, task, already_done = await _registry.request_cancel(request_id)
    if state is None:
        raise HTTPException(status_code=404, detail="実行中の会議が見つかりません")
    if already_done:
        return {
            "ok": True,
            "already_done": True,
            "request_id": request_id,
            "cancelled": state.terminal_outcome == "cancelled",
            "terminal_outcome": state.terminal_outcome,
        }
    try:
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        else:
            await state.wait_finished(timeout=5.0)
    except asyncio.CancelledError:
        pass
    except asyncio.TimeoutError:
        logger.warning("cancel persistence timed out request_id=%s", request_id)
    except Exception:
        logger.exception("cancel persistence failed request_id=%s", request_id)
    return {
        "ok": True,
        "cancellation_requested": True,
        "cancelled": state.terminal_outcome == "cancelled",
        "terminal_outcome": state.terminal_outcome,
        "provider_stop_guaranteed": False,
        "warning": "外部Provider側の処理停止・課金停止は保証されません",
        "request_id": request_id,
    }


# 再生成API(issue #11)はregeneration_api.pyへ、会話CRUD・添付・エクスポートAPIは
# conversations_api.pyへ、それぞれAPIRouterとして分離。両モジュールはmainの
# global(store等)を呼出時に遅延参照するため、全共有ヘルパー定義後の末尾で
# import・登録する(mainより先にimportしないこと)。
import regeneration_api  # noqa: E402
import conversations_api  # noqa: E402

# 再export: main内の_plan_from_request等と、regeneration_apiがmain.<名前>経由で
# 参照する共有ヘルパー(移設先はconversations_api)。
from conversations_api import (  # noqa: E402
    _attachment_context_for_turn,
    _attachment_http_exception,
    _turn_index_by_request_id,
)

app.include_router(regeneration_api.router)
app.include_router(conversations_api.router)
