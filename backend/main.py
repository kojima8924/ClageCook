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

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask

import config
import admin_telemetry
import attachments
import finance
import exporting
import orchestrator
import planning
import policy
import regeneration
import runs
import scrubbing
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


def _scrub_public(value: Any) -> Any:
    """SSE・API応答・永続化へ出す値から秘密候補を再帰除去する。"""
    return scrubbing.scrub_public_data(
        value,
        known_secrets=config.secret_values(),
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


_REQUEST_ID_ALLOWED = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
)


def _valid_request_id(value: str) -> bool:
    return 8 <= len(value) <= 80 and all(char in _REQUEST_ID_ALLOWED for char in value)


class PlanRequest(BaseModel):
    message: str = Field(min_length=1)
    tier: Literal["low", "balanced", "high"] = "balanced"
    reasoning_mode: Literal["auto", "low", "medium", "high"] = "auto"
    debate: bool = False
    providers: list[Literal["claude", "gemini", "chatgpt", "grok"]] | None = None
    synthesize: bool = True
    blind: bool = False
    web_search: bool = False
    conversation_id: str | None = None
    attachment_ids: list[str] = Field(
        default_factory=list,
        max_length=config.ATTACHMENT_MAX_PER_TURN,
    )

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("質問本文が空です")
        if len(value) > config.MAX_MESSAGE_CHARS:
            raise ValueError(f"質問は{config.MAX_MESSAGE_CHARS}文字以下にしてください")
        return value

    @field_validator("attachment_ids")
    @classmethod
    def validate_attachment_ids(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for item in value:
            try:
                canonical = str(uuid.UUID(item))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("attachment IDが不正です") from exc
            if canonical not in result:
                result.append(canonical)
        return result


class ChatRequest(PlanRequest):
    request_id: str | None = Field(default=None, min_length=8, max_length=80)
    confirm_live_api: bool = False
    confirm_sensitive_data: bool = False

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _valid_request_id(value):
            raise ValueError("request_idの長さまたは文字が不正です")
        return value


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ConversationMemoryUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    text: str = Field(default="", max_length=config.CONVERSATION_MEMORY_MAX_CHARS)


class RuntimeSettingsUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    models: dict[str, dict[str, str | None]] = Field(default_factory=dict)
    synthesizer_provider: Literal[
        "auto", "claude", "gemini", "chatgpt", "grok"
    ] | None = None
    synthesizer_models: dict[str, str | None] = Field(default_factory=dict)


class ReconciliationReleaseRequest(BaseModel):
    confirmed_no_unobserved_charge: bool = False
    note: str = Field(default="", max_length=200)


class PolicyScanRequest(BaseModel):
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if len(value) > config.MAX_MESSAGE_CHARS:
            raise ValueError(f"本文は{config.MAX_MESSAGE_CHARS}文字以下にしてください")
        return value


class SearchRequest(BaseModel):
    q: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=30, ge=1, le=100)

    @field_validator("q")
    @classmethod
    def validate_query(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("検索語が空です")
        return cleaned


class RegenerationPlanRequest(BaseModel):
    target: Literal["answer", "synthesis"]
    provider: str | None = None

    @field_validator("provider")
    @classmethod
    def valid_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if cleaned not in config.WORKERS:
            raise ValueError("invalid provider")
        return cleaned


class RegenerationRequest(RegenerationPlanRequest):
    regeneration_id: str = Field(min_length=8, max_length=80)
    confirm_live_api: bool = False
    confirm_sensitive_data: bool = False

    @field_validator("regeneration_id")
    @classmethod
    def valid_regeneration_id(cls, value: str) -> str:
        if not _valid_request_id(value):
            raise ValueError("invalid regeneration id")
        return value


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


def _turn_index_by_request_id(
    conversation: dict[str, Any],
    request_id: str,
) -> int:
    for index, turn in enumerate(conversation.get("turns") or []):
        if isinstance(turn, dict) and turn.get("request_id") == request_id:
            return index
    raise HTTPException(status_code=404, detail="対象ターンが見つかりません")


def _attachment_http_exception(exc: attachments.AttachmentError) -> HTTPException:
    if exc.code in {"attachment_not_found", "attachment_owner_mismatch"}:
        code = status.HTTP_404_NOT_FOUND
    elif exc.code == "attachment_expired":
        code = status.HTTP_410_GONE
    elif exc.code in {
        "attachment_too_large",
        "attachment_total_exceeded",
        "attachment_count_exceeded",
    }:
        code = 413
    elif exc.code in {
        "attachment_type_not_allowed",
        "attachment_mime_mismatch",
    }:
        code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    else:
        code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return HTTPException(
        status_code=code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _attachment_context_for_turn(
    conversation: dict[str, Any],
    turn: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    attachment_ids = turn.get("attachment_ids")
    if not isinstance(attachment_ids, list) or not attachment_ids:
        return "", []
    owner = str(
        turn.get("attachment_conversation_id") or conversation.get("id") or ""
    )
    try:
        return attachment_store.build_context(
            owner,
            [str(item) for item in attachment_ids],
        )
    except attachments.AttachmentError as exc:
        raise _attachment_http_exception(exc) from exc


def _regeneration_target(
    turn: dict[str, Any],
    req: RegenerationPlanRequest,
) -> tuple[str, str, dict[str, Any]]:
    try:
        return regeneration.resolve_target(
            turn,
            target=req.target,
            provider=req.provider,
            workers=config.WORKERS,
            synthesizer=config.synthesizer_name(),
        )
    except regeneration.TargetError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _regeneration_plan(
    conversation: dict[str, Any],
    turn_index: int,
    req: RegenerationPlanRequest,
    *,
    attachment_bundle: tuple[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    dependencies = regeneration.PlanDependencies(
        config=config,
        orchestrator=orchestrator,
        runtime_snapshot=config.runtime_settings.snapshot,
        scan_text=policy.scan_text,
        attachment_context=_attachment_context_for_turn,
        decorate_plan=lambda plan: budget_guard.decorate_plan(plan, store),
    )
    try:
        return regeneration.build_plan(
            conversation,
            turn_index,
            target=req.target,
            provider=req.provider,
            dependencies=dependencies,
            attachment_bundle=attachment_bundle,
        )
    except (regeneration.TargetError, regeneration.PlanError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc




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


def _sanitize_provider_error(
    data: dict[str, Any],
    *,
    fallback: str,
) -> None:
    """許可済み分類だけを固定文言で残し、任意分類を公開しない。"""
    if data.get("error_code") == "billing_or_credit_required":
        data["error"] = (
            "プロバイダの請求設定またはクレジット残高を確認してください"
        )
        return
    if data.get("error_code") == "model_refusal":
        data["error"] = "モデルのポリシー判定により回答が拒否されました"
        return
    data.pop("error_code", None)
    data["error"] = fallback


def _sanitize_event_data(event: str, data: dict[str, Any]) -> dict[str, Any]:
    """任意例外文字列をSSE履歴へ取り込まない。"""
    public = deepcopy(data)
    if event == "answer":
        if "error" in public:
            _sanitize_provider_error(
                public,
                fallback="AIからの回答取得に失敗しました",
            )
        else:
            public.pop("error_code", None)
        if "debate_error" in public:
            public["debate_error"] = "相互批評に失敗しました"
    elif event == "synthesis":
        if "error" in public:
            _sanitize_provider_error(
                public,
                fallback=(
                    "会議がキャンセルされました"
                    if public.get("cancelled")
                    else "統合に失敗しました"
                ),
            )
        else:
            public.pop("error_code", None)
    elif event == "error":
        public = {
            "message": (
                "会議がキャンセルされました"
                if public.get("cancelled")
                else "会議に失敗しました"
            )
        }
        if isinstance(data.get("request_id"), str):
            public["request_id"] = data["request_id"]
        if data.get("cancelled"):
            public["cancelled"] = True
    scrubbed = _scrub_public(public)
    return scrubbed if isinstance(scrubbed, dict) else {}


def _sanitize_turn(turn: dict[str, Any]) -> dict[str, Any]:
    """SSEと同じ基準で、保存する失敗理由から任意文字列を除く。"""
    public = deepcopy(turn)
    answers = public.get("answers")
    if isinstance(answers, dict):
        for answer in answers.values():
            if not isinstance(answer, dict):
                continue
            if "error" in answer:
                _sanitize_provider_error(
                    answer,
                    fallback="AIからの回答取得に失敗しました",
                )
            else:
                answer.pop("error_code", None)
            if "debate_error" in answer:
                answer["debate_error"] = "相互批評に失敗しました"
    synthesis = public.get("synthesis")
    if isinstance(synthesis, dict) and "error" in synthesis:
        _sanitize_provider_error(
            synthesis,
            fallback=(
                "会議がキャンセルされました"
                if synthesis.get("cancelled")
                else "統合に失敗しました"
            ),
        )
    elif isinstance(synthesis, dict):
        synthesis.pop("error_code", None)
    scrubbed = _scrub_public(public)
    return scrubbed if isinstance(scrubbed, dict) else {}


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
                provider_models = {
                    str(item["name"]): str(item["model"])
                    for item in final_plan.get("providers") or []
                    if isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                    and isinstance(item.get("model"), str)
                }
                synthesizer_plan = final_plan.get("synthesizer")
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
                execution_snapshot = {
                    "planned_at": utc_now(),
                    "providers": provider_models,
                    "synthesizer": synthesizer_model,
                    "synthesizer_provider": synthesizer_provider,
                    "provider_execution": {
                        str(item["name"]): {
                            "model": str(item["model"]),
                            "max_output_tokens": int(item["max_output_tokens"]),
                            "reasoning": deepcopy(item.get("reasoning") or {}),
                        }
                        for item in final_plan.get("providers") or []
                        if isinstance(item, dict)
                        and isinstance(item.get("name"), str)
                        and isinstance(item.get("model"), str)
                        and isinstance(item.get("max_output_tokens"), int)
                    },
                    "synthesizer_execution": (
                        {
                            "model": synthesizer_model,
                            "max_output_tokens": int(
                                synthesizer_plan["max_output_tokens"]
                            ),
                            "reasoning": deepcopy(
                                synthesizer_plan.get("reasoning") or {}
                            ),
                        }
                        if isinstance(synthesizer_plan, dict)
                        and isinstance(
                            synthesizer_plan.get("max_output_tokens"), int
                        )
                        else None
                    ),
                }
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
            if dispatch_started:
                await _complete_critical(
                    _blocking_call(
                        budget_guard.settle,
                        state.request_id,
                        usage_reconciled=False,
                    )
                )
            else:
                await _complete_critical(
                    _blocking_call(
                        budget_guard.release_undispatched,
                        state.request_id,
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
            if dispatch_started:
                await _complete_critical(
                    _blocking_call(
                        budget_guard.settle,
                        state.request_id,
                        usage_reconciled=False,
                    )
                )
            else:
                await _complete_critical(
                    _blocking_call(
                        budget_guard.release_undispatched,
                        state.request_id,
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
    public = _scrub_public(result)
    return public if isinstance(public, dict) else {}


@app.get("/api/settings", dependencies=[Depends(check_auth)])
def settings() -> dict[str, Any]:
    result = config.public_settings()
    result["finance"] = budget_guard.public_snapshot(store)
    public = _scrub_public(result)
    return public if isinstance(public, dict) else {}


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
    public = _scrub_public(result)
    return public if isinstance(public, dict) else {}


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
    public = _scrub_public(snapshot)
    return public if isinstance(public, dict) else {}


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
    public = _scrub_public(result)
    return public if isinstance(public, dict) else {}


@app.post(
    "/api/conversations/{conversation_id}/turns/{turn_request_id}/regeneration-plan",
    dependencies=[Depends(check_auth)],
)
async def regeneration_plan(
    conversation_id: str,
    turn_request_id: str,
    req: RegenerationPlanRequest,
) -> dict[str, Any]:
    canonical_id = _canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    if not _valid_request_id(turn_request_id):
        raise HTTPException(status_code=404, detail="対象ターンが見つかりません")
    async with _hold_conversation_lock(canonical_id):
        try:
            conversation_data = await _blocking_call(store.load, canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        turn_index = _turn_index_by_request_id(conversation_data, turn_request_id)
        result = await _blocking_call(
            _regeneration_plan,
            conversation_data,
            turn_index,
            req,
        )
        public = _scrub_public(result)
        return public if isinstance(public, dict) else {}


def _enforce_regeneration_confirmations(
    plan: dict[str, Any],
    req: RegenerationRequest,
) -> None:
    required = regeneration.required_confirmations(
        plan,
        confirm_live_api=req.confirm_live_api,
        confirm_sensitive_data=req.confirm_sensitive_data,
    )
    if required:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail={
                "code": "explicit_confirmation_required",
                "message": "再生成の実API利用には明示確認が必要です。",
                "required": required,
                "plan": plan,
            },
        )


async def _mark_regeneration_interrupted(
    conversation_id: str,
    turn_request_id: str,
    regeneration_id: str,
    *,
    cancelled: bool,
) -> None:
    """cancel・shutdown・通常障害の未完了attemptをdurableに中断確定する。"""
    async with _hold_conversation_lock(conversation_id):
        try:
            conversation_data = await _blocking_call(store.load, conversation_id)
            turn_index = _turn_index_by_request_id(
                conversation_data,
                turn_request_id,
            )
        except (ConversationNotFound, HTTPException):
            return
        turn = conversation_data["turns"][turn_index]
        attempt = regeneration.find_attempt(turn, regeneration_id)
        if attempt is None or not regeneration.interrupt_attempt(
            attempt,
            now=utc_now(),
            cancelled=cancelled,
        ):
            return
        await _blocking_call(store.save, conversation_data)


def _saved_regeneration_replay(
    conversation_id: str,
    turn_request_id: str,
    regeneration_id: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    """保存済みterminal attemptをrate/active claimより先に無課金再生する。"""
    try:
        conversation_data = store.load(conversation_id)
        turn_index = _turn_index_by_request_id(
            conversation_data,
            turn_request_id,
        )
    except (ConversationNotFound, HTTPException):
        return None
    turn = conversation_data["turns"][turn_index]
    existing = regeneration.find_attempt(turn, regeneration_id)
    if existing is None:
        return None
    if existing.get("request_fingerprint") != fingerprint:
        raise HTTPException(
            status_code=409,
            detail="regeneration_idが異なる要求で使用済みです",
        )
    if existing.get("status") not in {"completed", "failed", "interrupted"}:
        return None
    public = _scrub_public(
        {
            "attempt": existing,
            "conversation": conversation_data,
            "replayed": True,
        }
    )
    return public if isinstance(public, dict) else {}


async def _execute_regeneration(
    state: RunState,
    conversation_id: str,
    turn_request_id: str,
    req: RegenerationRequest,
    fingerprint: str,
) -> None:
    """再生成をHTTP接続から独立して実行し、短いlock区間だけで保存する。"""
    reserve_started = False
    dispatch_started = False
    budget_finalized = False
    try:
        async with _run_slots:
            async with _hold_conversation_lock(conversation_id):
                try:
                    conversation_data = await _blocking_call(store.load, conversation_id)
                except ConversationNotFound as exc:
                    raise HTTPException(
                        status_code=404,
                        detail="会話が見つかりません",
                    ) from exc
                turn_index = _turn_index_by_request_id(
                    conversation_data,
                    turn_request_id,
                )
                turn = conversation_data["turns"][turn_index]
                target_key, provider_name, current = _regeneration_target(turn, req)
                existing = regeneration.find_attempt(turn, req.regeneration_id)
                if existing is not None:
                    if existing.get("request_fingerprint") != fingerprint:
                        raise HTTPException(
                            status_code=409,
                            detail="regeneration_idが異なる要求で使用済みです",
                        )
                    if existing.get("status") in {
                        "completed",
                        "failed",
                        "interrupted",
                    }:
                        public = _scrub_public(
                            {
                                "attempt": existing,
                                "conversation": conversation_data,
                                "replayed": True,
                            }
                        )
                        state.result = public if isinstance(public, dict) else {}
                        return
                    raise HTTPException(
                        status_code=409,
                        detail="同じ再生成attemptが実行中です",
                    )

                attachment_bundle = await _blocking_call(
                    _attachment_context_for_turn,
                    conversation_data,
                    turn,
                )
                plan = await _blocking_call(
                    _regeneration_plan,
                    conversation_data,
                    turn_index,
                    req,
                    attachment_bundle=attachment_bundle,
                )
                _enforce_run_limits(plan)
                _enforce_regeneration_confirmations(plan, req)
                if req.target == "synthesis":
                    provider_name = str(plan["synthesizer"]["name"])

                parent_attempt_id = regeneration.ensure_original_attempt(
                    turn,
                    target_key=target_key,
                    target=req.target,
                    provider=(
                        str(current.get("source") or provider_name)
                        if req.target == "synthesis"
                        else provider_name
                    ),
                    current=current,
                    now=utc_now,
                )
                reserve_started = True
                reservation = await _blocking_call(
                    budget_guard.reserve,
                    request_id=req.regeneration_id,
                    request_fingerprint=fingerprint,
                    plan=plan,
                    store=store,
                    reservation_owner=state.execution_id,
                )
                attempt = {
                    "attempt_id": req.regeneration_id,
                    "parent_attempt_id": parent_attempt_id,
                    "request_fingerprint": fingerprint,
                    "target": req.target,
                    "provider": provider_name,
                    "status": "reserved",
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                    "original": False,
                    "cost_estimate": deepcopy(plan.get("cost_estimate") or {}),
                    "budget_reservation": deepcopy(reservation),
                    "execution_snapshot": {
                        "planned_at": utc_now(),
                        "providers": {
                            str(item["name"]): str(item["model"])
                            for item in plan.get("providers") or []
                            if isinstance(item, dict)
                            and isinstance(item.get("name"), str)
                            and isinstance(item.get("model"), str)
                        },
                        "synthesizer": (
                            str(plan["synthesizer"]["model"])
                            if isinstance(plan.get("synthesizer"), dict)
                            and isinstance(plan["synthesizer"].get("model"), str)
                            else None
                        ),
                        "synthesizer_provider": (
                            str(plan["synthesizer"]["name"])
                            if isinstance(plan.get("synthesizer"), dict)
                            and isinstance(plan["synthesizer"].get("name"), str)
                            else None
                        ),
                        "provider_execution": {
                            str(item["name"]): {
                                "model": str(item["model"]),
                                "max_output_tokens": int(
                                    item["max_output_tokens"]
                                ),
                                "reasoning": deepcopy(
                                    item.get("reasoning") or {}
                                ),
                            }
                            for item in plan.get("providers") or []
                            if isinstance(item, dict)
                            and isinstance(item.get("name"), str)
                            and isinstance(item.get("model"), str)
                            and isinstance(item.get("max_output_tokens"), int)
                        },
                        "synthesizer_execution": (
                            {
                                "model": plan["synthesizer"].get("model"),
                                "max_output_tokens": int(
                                    plan["synthesizer"]["max_output_tokens"]
                                ),
                                "reasoning": deepcopy(
                                    plan["synthesizer"].get("reasoning") or {}
                                ),
                            }
                            if isinstance(plan.get("synthesizer"), dict)
                            and isinstance(
                                plan["synthesizer"].get("max_output_tokens"),
                                int,
                            )
                            else None
                        ),
                    },
                    "usage_may_be_incomplete": True,
                }
                turn.setdefault("attempts", []).append(attempt)
                await _blocking_call(store.save, conversation_data)
                attempt["status"] = "dispatching"
                attempt["updated_at"] = utc_now()
                await _blocking_call(store.save, conversation_data)

                tier = str((turn.get("options") or {}).get("tier") or "balanced")
                if tier not in {"low", "balanced", "high"}:
                    tier = "balanced"
                reasoning_mode = config.normalized_reasoning_mode(
                    str(
                        (turn.get("options") or {}).get("reasoning_mode")
                        or "auto"
                    )
                )
                message = str(turn.get("clean_message") or turn.get("message") or "")
                model_message = message + attachment_bundle[0]
                context = deepcopy(conversation_data)
                context["turns"] = deepcopy(
                    conversation_data.get("turns", [])[:turn_index]
                )
                answers = {
                    name: value
                    for name, value in (turn.get("answers") or {}).items()
                    if name in config.WORKERS
                    and isinstance(value, dict)
                    and value.get("ok")
                }
                aliases = (
                    orchestrator._blind_aliases(
                        list(answers),
                        str(turn.get("request_id") or req.regeneration_id),
                    )
                    if (turn.get("options") or {}).get("blind") is True
                    else None
                )

            execution_snapshot = attempt["execution_snapshot"]
            dispatch_started = True
            with orchestrator.freeze_execution_models(
                execution_snapshot["providers"],
                execution_snapshot["synthesizer"],
                execution_snapshot.get("synthesizer_provider"),
            ):
                if req.target == "answer":
                    result = await orchestrator._run_provider(
                        provider_name,
                        orchestrator._worker_prompt(context, model_message),
                        system=(
                            orchestrator.WORKER_SYSTEM
                            + " "
                            + regeneration.ANSWER_REGENERATION_INSTRUCTION
                        ),
                        tier=tier,
                        reasoning_mode=reasoning_mode,
                        round_number=1,
                        prompt_cache_key=orchestrator._prompt_cache_key(
                            conversation_id,
                            provider_name,
                        ),
                        redact_confirm=True,
                        web_search=(
                            bool((turn.get("options") or {}).get("web_search"))
                            and config.WEB_SEARCH_ENABLED
                        ),
                    )
                else:
                    result = await orchestrator._run_synthesis(
                        model_message,
                        answers,
                        tier,
                        reasoning_mode,
                        aliases,
                        conversation_id,
                    )

            result = _scrub_public(result)
            if not isinstance(result, dict):
                result = {
                    "ok": False,
                    "error": "再生成結果を安全に処理できませんでした",
                    "usage": {},
                    "usage_may_be_incomplete": True,
                }
            billing_turn = {
                "answers": (
                    {provider_name: result}
                    if req.target == "answer"
                    else {}
                ),
                "synthesis": result if req.target == "synthesis" else {},
            }

            async def finalize_regeneration_result() -> dict[str, Any]:
                nonlocal budget_finalized
                async with _hold_conversation_lock(conversation_id):
                    conversation_data = await _blocking_call(
                        store.load,
                        conversation_id,
                    )
                    turn_index = _turn_index_by_request_id(
                        conversation_data,
                        turn_request_id,
                    )
                    turn = conversation_data["turns"][turn_index]
                    attempt = regeneration.find_attempt(
                        turn,
                        req.regeneration_id,
                    )
                    if attempt is None:
                        raise RuntimeError(
                            "durable regeneration attempt is missing"
                        )
                    attempt["result"] = deepcopy(result)
                    attempt["status"] = (
                        "completed" if result.get("ok") else "failed"
                    )
                    attempt["completed_at"] = utc_now()
                    attempt["updated_at"] = attempt["completed_at"]
                    attempt["usage_may_be_incomplete"] = bool(
                        result.get("usage_may_be_incomplete")
                    )
                    if result.get("ok"):
                        turn.setdefault("active_attempts", {})[
                            target_key
                        ] = req.regeneration_id
                        if req.target == "answer":
                            turn.setdefault("answers", {})[
                                provider_name
                            ] = deepcopy(result)
                            successful = [
                                {
                                    "source": name,
                                    "text": str(value.get("text") or ""),
                                }
                                for name, value in (
                                    turn.get("answers") or {}
                                ).items()
                                if isinstance(value, dict) and value.get("ok")
                            ]
                            turn["insights"] = orchestrator.analyze_insights(
                                successful
                            )
                            turn["synthesis_stale"] = True
                        else:
                            turn["synthesis"] = deepcopy(result)
                            turn["synthesis_stale"] = False
                    await _blocking_call(store.save, conversation_data)

                await _blocking_call(
                    budget_guard.settle,
                    req.regeneration_id,
                    usage_reconciled=finance.turn_usage_reconciled(
                        billing_turn
                    ),
                    turn=billing_turn,
                )
                budget_finalized = True
                state.terminal_outcome = "completed"
                public = _scrub_public(
                    {
                        "attempt": attempt,
                        "conversation": conversation_data,
                        "replayed": False,
                    }
                )
                return public if isinstance(public, dict) else {}

            # Provider結果後は保存・budget settle・公開結果を一体で完走する。
            state.result, _ = await _complete_critical(
                finalize_regeneration_result()
            )
    except asyncio.CancelledError:
        state.terminal_outcome = "cancelled"
        if reserve_started and not budget_finalized:
            if dispatch_started:
                await _blocking_call(
                    budget_guard.settle,
                    req.regeneration_id,
                    usage_reconciled=False,
                )
            else:
                await _blocking_call(
                    budget_guard.release_undispatched,
                    req.regeneration_id,
                )
        await _mark_regeneration_interrupted(
            conversation_id,
            turn_request_id,
            req.regeneration_id,
            cancelled=True,
        )
        state.failure_status = 409
        state.failure_detail = {
            "code": "regeneration_cancelled",
            "message": "再生成がキャンセルされました。",
        }
    except finance.BudgetViolation as exc:
        state.terminal_outcome = "failed"
        state.failure_status = status.HTTP_422_UNPROCESSABLE_ENTITY
        state.failure_detail = {
            "code": exc.code,
            "message": str(exc),
            "budget": exc.snapshot,
        }
    except HTTPException as exc:
        state.terminal_outcome = "failed"
        state.failure_status = exc.status_code
        state.failure_detail = _scrub_public(exc.detail)

        async def finalize_http_failure() -> None:
            nonlocal budget_finalized
            if reserve_started and not budget_finalized:
                if dispatch_started:
                    await _blocking_call(
                        budget_guard.settle,
                        req.regeneration_id,
                        usage_reconciled=False,
                    )
                else:
                    await _blocking_call(
                        budget_guard.release_undispatched,
                        req.regeneration_id,
                    )
                budget_finalized = True
                await _mark_regeneration_interrupted(
                    conversation_id,
                    turn_request_id,
                    req.regeneration_id,
                    cancelled=False,
                )

        # Provider/validation failureが先に確定した後のcancelは、台帳と
        # durable attemptを分断せずfailedとして完走させる。
        await _complete_critical(finalize_http_failure())
    except Exception as exc:
        state.terminal_outcome = "failed"
        logger.error(
            "regeneration failed regeneration_id=%s exception_type=%s",
            req.regeneration_id,
            type(exc).__name__,
        )
        state.failure_status = 500
        state.failure_detail = "再生成に失敗しました"

        async def finalize_unexpected_failure() -> None:
            nonlocal budget_finalized
            if reserve_started and not budget_finalized:
                if dispatch_started:
                    await _blocking_call(
                        budget_guard.settle,
                        req.regeneration_id,
                        usage_reconciled=False,
                    )
                else:
                    await _blocking_call(
                        budget_guard.release_undispatched,
                        req.regeneration_id,
                    )
                budget_finalized = True
            await _mark_regeneration_interrupted(
                conversation_id,
                turn_request_id,
                req.regeneration_id,
                cancelled=False,
            )

        await _complete_critical(finalize_unexpected_failure())
    finally:
        await _finalize_background_run(state)
        # 再生成は完了attemptをdurable storeから再生できる。会話全体を含む
        # state.resultを共通1時間retentionへ残さず、待機中callerの参照だけにする。
        await _registry.remove(state.request_id, state)


@app.post(
    "/api/conversations/{conversation_id}/turns/{turn_request_id}/regenerate",
    dependencies=[Depends(check_auth)],
)
async def regenerate_turn_result(
    conversation_id: str,
    turn_request_id: str,
    req: RegenerationRequest,
    request: Request,
) -> dict[str, Any]:
    canonical_id = _canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    if not _valid_request_id(turn_request_id):
        raise HTTPException(status_code=404, detail="対象ターンが見つかりません")
    fingerprint = regeneration.fingerprint(
        canonical_id,
        turn_request_id,
        target=req.target,
        provider=req.provider,
    )
    saved_replay = await _blocking_call(
        _saved_regeneration_replay,
        canonical_id,
        turn_request_id,
        req.regeneration_id,
        fingerprint,
    )
    if saved_replay is not None:
        return saved_replay
    await _rate_limiter.check(_rate_limit_key(request), req.regeneration_id)
    state, created = await _registry.claim(
        req.regeneration_id,
        lambda: _new_run_state(
            req.regeneration_id,
            canonical_id,
            fingerprint,
            kind="regeneration",
        ),
    )
    if (
        state.kind != "regeneration"
        or state.request_fingerprint != fingerprint
        or state.conversation_id != canonical_id
    ):
        raise HTTPException(
            status_code=409,
            detail="regeneration_idが異なる要求で使用済みです",
        )

    if created:
        try:
            claimed = await _claim_conversation_run(
                canonical_id,
                req.regeneration_id,
            )
        except BaseException:
            await _release_conversation_run(canonical_id, req.regeneration_id)
            await state.finish()
            await _registry.remove(req.regeneration_id, state)
            raise
        if not claimed:
            state.failure_status = 409
            state.failure_detail = {
                "code": "conversation_busy",
                "message": "この会話では別の生成処理が実行中です。",
            }
            await state.finish()
            await _registry.remove(req.regeneration_id, state)
        else:
            try:
                started = await _registry.start(
                    state,
                    lambda: _execute_regeneration(
                        state,
                        canonical_id,
                        turn_request_id,
                        req,
                        fingerprint,
                    ),
                )
            except BaseException:
                # task作成後はbackground runnerがclaimを所有する。作成前に
                # handlerだけがcancelされた場合に限り、ここで解放する。
                if state.task is None:
                    await _release_conversation_run(
                        canonical_id,
                        req.regeneration_id,
                    )
                    await state.finish()
                    await _registry.remove(req.regeneration_id, state)
                raise
            if not started:
                await _release_conversation_run(canonical_id, req.regeneration_id)
                state.terminal_outcome = "cancelled"
                state.failure_status = 409
                state.failure_detail = {
                    "code": "regeneration_cancelled",
                    "message": "再生成は開始前にキャンセルされました。",
                }
                await state.finish()

    task = state.task
    if task is not None and not state.done:
        await asyncio.shield(task)
    elif not state.done:
        await state.wait_finished()
    if state.failure_status is not None:
        raise HTTPException(
            status_code=state.failure_status,
            detail=state.failure_detail,
        )
    result = deepcopy(state.result) if isinstance(state.result, dict) else {}
    if not created and result:
        result["replayed"] = True
    return result


@app.post("/api/plan", dependencies=[Depends(check_auth)])
def plan(req: PlanRequest) -> dict[str, Any]:
    """課金APIを一切呼ばず、現在の設定による会議の安全側上限を返す。"""
    public = _scrub_public(_plan_from_request(req))
    return public if isinstance(public, dict) else {}


@app.post("/api/policy/scan", dependencies=[Depends(check_auth)])
def scan_policy(req: PolicyScanRequest) -> dict[str, Any]:
    """課金APIや外部サービスを使わず、送信前の文字列をローカル検査する。"""
    public = _scrub_public(policy.scan_text(req.text))
    return public if isinstance(public, dict) else {}


@app.post("/api/conversations", dependencies=[Depends(check_auth)])
def create_draft_conversation() -> dict[str, Any]:
    """添付や編集分岐の送信前draftとして、空の会話を作る。"""
    public = _scrub_public(store.create())
    return public if isinstance(public, dict) else {}


@app.get("/api/conversations", dependencies=[Depends(check_auth)])
def conversations() -> list[dict[str, Any]]:
    public = _scrub_public(store.list())
    return public if isinstance(public, list) else []


@app.post("/api/search", dependencies=[Depends(check_auth)])
def search_conversations(req: SearchRequest) -> dict[str, Any]:
    """検索語をURLや通常のaccess logへ残さず、保存JSONだけを検索する。"""
    public = _scrub_public(
        {"query": req.q, "results": store.search(req.q, req.limit)}
    )
    return public if isinstance(public, dict) else {"query": "", "results": []}


@app.get("/api/conversations/{conversation_id}", dependencies=[Depends(check_auth)])
def conversation(conversation_id: str) -> dict[str, Any]:
    try:
        public = _scrub_public(store.load(conversation_id))
        return public if isinstance(public, dict) else {}
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会話が見つかりません") from exc


@app.get(
    "/api/conversations/{conversation_id}/attachments",
    dependencies=[Depends(check_auth)],
)
def list_attachments(conversation_id: str) -> dict[str, Any]:
    canonical_id = _canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    try:
        store.load(canonical_id)
        items = attachment_store.list(canonical_id)
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
    public = _scrub_public({"items": items})
    return public if isinstance(public, dict) else {"items": []}


@app.post(
    "/api/conversations/{conversation_id}/attachments",
    dependencies=[Depends(check_auth)],
)
async def upload_attachment(
    conversation_id: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    canonical_id = _canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    async with _hold_conversation_lock(canonical_id):
        try:
            await _blocking_call(store.load, canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        try:
            item = await attachment_store.save_upload(canonical_id, file)
        except attachments.AttachmentError as exc:
            raise _attachment_http_exception(exc) from exc
    public = _scrub_public(item)
    return public if isinstance(public, dict) else {}


@app.get(
    "/api/conversations/{conversation_id}/attachments/{attachment_id}",
    dependencies=[Depends(check_auth)],
)
def download_attachment(conversation_id: str, attachment_id: str) -> FileResponse:
    canonical_id = _canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    try:
        metadata = attachment_store.metadata(canonical_id, attachment_id)
        path = attachment_store.content_path(canonical_id, attachment_id)
    except attachments.AttachmentError as exc:
        raise _attachment_http_exception(exc) from exc
    return FileResponse(
        path,
        media_type=str(metadata["mime_type"]),
        filename=str(metadata["name"]),
        headers={"X-Content-Type-Options": "nosniff"},
    )


@app.delete(
    "/api/conversations/{conversation_id}/attachments/{attachment_id}",
    dependencies=[Depends(check_auth)],
)
async def delete_attachment(
    conversation_id: str,
    attachment_id: str,
) -> dict[str, bool]:
    canonical_id = _canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    async with _hold_conversation_lock(canonical_id):
        try:
            await _blocking_call(store.load, canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        try:
            await _blocking_call(
                attachment_store.delete,
                canonical_id,
                attachment_id,
            )
        except attachments.AttachmentError as exc:
            raise _attachment_http_exception(exc) from exc
    return {"ok": True}


@app.get(
    "/api/conversations/{conversation_id}/export", dependencies=[Depends(check_auth)]
)
def export_conversation(conversation_id: str) -> JSONResponse:
    try:
        data = _scrub_public(store.load(conversation_id))
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
    response = JSONResponse(data)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="clage-cook-{conversation_id[:8]}.json"'
    )
    return response


@app.get(
    "/api/conversations/{conversation_id}/export.md",
    dependencies=[Depends(check_auth)],
)
def export_conversation_markdown(conversation_id: str) -> PlainTextResponse:
    try:
        data = _scrub_public(store.load(conversation_id))
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="会話をエクスポートできません")
    response = PlainTextResponse(
        exporting.conversation_markdown(data),
        media_type="text/markdown; charset=utf-8",
    )
    response.headers["Content-Disposition"] = (
        f'attachment; filename="clage-cook-{conversation_id[:8]}.md"'
    )
    return response


@app.get(
    "/api/conversations/{conversation_id}/export.zip",
    dependencies=[Depends(check_auth)],
)
def export_conversation_archive(conversation_id: str) -> FileResponse:
    canonical_id = _canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    try:
        data = _scrub_public(store.load(canonical_id))
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="会話をエクスポートできません")
    try:
        path = exporting.build_conversation_zip(
            data_dir=config.DATA_DIR,
            conversation=data,
            attachment_store=attachment_store,
        )
    except Exception as exc:
        logger.error(
            "conversation export failed conversation_id=%s exception_type=%s",
            canonical_id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="ZIPを作成できません") from exc
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"clage-cook-{canonical_id[:8]}.zip",
        background=BackgroundTask(exporting.remove_export, path),
    )


@app.patch("/api/conversations/{conversation_id}", dependencies=[Depends(check_auth)])
async def rename_conversation(conversation_id: str, req: RenameRequest) -> dict[str, Any]:
    canonical_id = _canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    async with _hold_conversation_lock(canonical_id):
        try:
            data = await _blocking_call(store.load, canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        data["title"] = req.title.strip()
        await _blocking_call(store.save, data)
        public = _scrub_public(store.summary(data))
        return public if isinstance(public, dict) else {}


@app.patch(
    "/api/conversations/{conversation_id}/memory",
    dependencies=[Depends(check_auth)],
)
async def update_conversation_memory(
    conversation_id: str,
    req: ConversationMemoryUpdateRequest,
) -> dict[str, Any]:
    """会話ごとのローカルメモを楽観lock付きで更新する。"""
    canonical_id = _canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    async with _hold_conversation_lock(canonical_id):
        try:
            conversation_data = await _blocking_call(store.load, canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        current = conversation_data.get("memory")
        if not isinstance(current, dict):
            current = {"revision": 0, "text": "", "updated_at": ""}
        revision = current.get("revision")
        revision = revision if isinstance(revision, int) and revision >= 0 else 0
        if req.expected_revision != revision:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "conversation_memory_conflict",
                    "message": "ローカルメモが別の画面で更新されています。再読込してください。",
                    "current_revision": revision,
                },
            )
        text_value = req.text.strip()
        scan = policy.scan_text(text_value)
        stored_text = (
            scan["redacted_text"] if scan["action"] == "block" else text_value
        )
        conversation_data["memory"] = {
            "revision": revision + 1,
            "text": stored_text,
            "updated_at": utc_now(),
            "secret_candidates_redacted": scan["action"] == "block",
        }
        await _blocking_call(store.save, conversation_data)
        public = _scrub_public(conversation_data)
        return public if isinstance(public, dict) else {}


@app.post(
    "/api/conversations/{conversation_id}/turns/{turn_request_id}/fork",
    dependencies=[Depends(check_auth)],
)
async def fork_conversation_at_turn(
    conversation_id: str,
    turn_request_id: str,
) -> dict[str, Any]:
    """親履歴を破壊せず、対象turnの直前から編集を続けるbranchを作る。"""
    canonical_id = _canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    if not _valid_request_id(turn_request_id):
        raise HTTPException(status_code=404, detail="対象ターンが見つかりません")
    async with _hold_conversation_lock(canonical_id):
        await _reject_destructive_change_during_run(canonical_id)
        try:
            parent = await _blocking_call(store.load, canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        turn_index = _turn_index_by_request_id(parent, turn_request_id)
        target = parent["turns"][turn_index]
        if not isinstance(target, dict) or target.get("status") != "completed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="完了済みターンだけを編集分岐できます",
            )
        branch = await _blocking_call(
            store.create_branch,
            parent,
            before_turn_index=turn_index,
            parent_turn_request_id=turn_request_id,
        )
        public = _scrub_public(branch)
        return public if isinstance(public, dict) else {}


@app.delete("/api/conversations/{conversation_id}", dependencies=[Depends(check_auth)])
async def delete_conversation(conversation_id: str) -> dict[str, bool]:
    canonical_id = _canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    async with _hold_conversation_lock(canonical_id):
        await _reject_destructive_change_during_run(canonical_id)
        try:
            await _blocking_call(attachment_store.delete_conversation, canonical_id)
            await _blocking_call(store.delete, canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        return {"ok": True}


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
