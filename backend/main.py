# -*- coding: utf-8 -*-
"""Clage Cook OSS FastAPIサーバー。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from typing import Any, AsyncIterator, Callable, Literal

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
import scrubbing
import telemetry
from storage import ConversationNotFound, ConversationStore, utc_now
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
        yield
    finally:
        _single_process_guard.release()


app = FastAPI(
    title="Clage Cook OSS",
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
_conversation_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_active_conversation_runs: dict[str, str] = {}
_active_conversation_guard = asyncio.Lock()


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


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_sec: float = 60.0) -> None:
        self.limit = limit
        self.window_sec = window_sec
        self._entries: dict[str, deque[tuple[float, str]]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str, request_id: str) -> None:
        now = time.monotonic()
        async with self._lock:
            entries = self._entries[key]
            while entries and now - entries[0][0] >= self.window_sec:
                entries.popleft()
            if any(existing == request_id for _, existing in entries):
                return
            if len(entries) >= self.limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="1分あたりの会議開始上限を超えました",
                    headers={"Retry-After": "60"},
                )
            entries.append((now, request_id))


_rate_limiter = SlidingWindowLimiter(config.RATE_LIMIT_PER_MINUTE)


def _plan_from_request(req: PlanRequest) -> dict[str, Any]:
    history_text = ""
    memory_text = ""
    attachment_context = ""
    attachment_refs: list[dict[str, Any]] = []
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
        history_text = orchestrator._history_text(conversation)
        memory = conversation.get("memory")
        if isinstance(memory, dict):
            memory_text = str(memory.get("text") or "")
        try:
            attachment_context, attachment_refs = attachment_store.build_context(
                canonical_id,
                req.attachment_ids,
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
    plan = planning.build_run_plan(
        message=req.message + attachment_context,
        tier=req.tier,
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
    return budget_guard.decorate_plan(plan, store)


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
    if turn.get("status") != "completed":
        raise HTTPException(
            status_code=409,
            detail="完了済みターンだけを再生成できます",
        )
    if req.target == "answer":
        source = req.provider or ""
        current = (turn.get("answers") or {}).get(source)
        if source not in config.WORKERS or not isinstance(current, dict):
            raise HTTPException(status_code=404, detail="対象回答が見つかりません")
        return f"answer:{source}", source, current
    current = turn.get("synthesis")
    if not isinstance(current, dict) or current.get("skipped") is True:
        raise HTTPException(status_code=404, detail="対象の統合回答がありません")
    return "synthesis", config.synthesizer_name(), current


def _regeneration_plan(
    conversation: dict[str, Any],
    turn_index: int,
    req: RegenerationPlanRequest,
) -> dict[str, Any]:
    turn = conversation["turns"][turn_index]
    _key, source, _current = _regeneration_target(turn, req)
    tier = str((turn.get("options") or {}).get("tier") or "balanced")
    if tier not in {"low", "balanced", "high"}:
        tier = "balanced"
    message = str(turn.get("clean_message") or turn.get("message") or "")
    attachment_context, attachment_refs = _attachment_context_for_turn(
        conversation,
        turn,
    )
    model_message = message + attachment_context
    policy_result = policy.scan_text(model_message)
    context = deepcopy(conversation)
    context["turns"] = deepcopy(conversation.get("turns", [])[:turn_index])
    if req.target == "answer":
        prompt = orchestrator._worker_prompt(context, model_message)
        system = orchestrator.WORKER_SYSTEM
        status_snapshot = config.provider_status(source)
        mode = status_snapshot.mode
        model = config.model_for(source, tier) if mode == "live" else "mock"
        providers = [
            {
                "name": source,
                "label": status_snapshot.label,
                "mode": mode,
                "model": model,
                "billable": mode == "live",
                "max_calls": 1,
            }
        ]
        synthesizer = {
            "name": "synthesizer",
            "label": "Synthesizer",
            "mode": "mock",
            "model": "mock",
            "enabled": False,
            "billable": False,
            "max_calls": 0,
        }
        answer_input = len((prompt + system).encode("utf-8"))
        synthesis_input = 0
        unavailable = mode == "disabled"
    else:
        answers = turn.get("answers") or {}
        successful = {
            name: value
            for name, value in answers.items()
            if name in config.WORKERS
            and isinstance(value, dict)
            and value.get("ok")
        }
        if not successful:
            raise HTTPException(
                status_code=409,
                detail="成功回答がないため統合を再生成できません",
            )
        aliases = (
            orchestrator._blind_aliases(
                list(successful),
                str(turn.get("request_id") or "regeneration"),
            )
            if (turn.get("options") or {}).get("blind") is True
            else None
        )
        prompt = orchestrator._synthesis_prompt(model_message, successful, aliases)
        system = orchestrator.SYNTH_SYSTEM
        synth_name = config.synthesizer_name()
        mode = "mock" if synth_name == "synthesizer" else "live"
        model = config.synthesizer_model_for(tier)
        providers = []
        synthesizer = {
            "name": synth_name,
            "label": config.LABELS.get(synth_name, "Local mock synthesizer"),
            "mode": mode,
            "model": model,
            "enabled": True,
            "billable": mode == "live",
            "max_calls": 1,
        }
        source = synth_name
        answer_input = 0
        synthesis_input = len((prompt + system).encode("utf-8"))
        unavailable = False

    billable = mode == "live"
    attempts = config.HTTP_RETRIES + 1
    input_total = (answer_input + synthesis_input) * attempts
    output_total = config.MAX_OUTPUT_TOKENS[tier] * attempts
    block_reasons = []
    web_search_requested = bool(
        req.target == "answer"
        and (turn.get("options") or {}).get("web_search") is True
    )
    web_search_effective = bool(
        web_search_requested and config.WEB_SEARCH_ENABLED and mode == "live"
    )
    if unavailable:
        block_reasons.append("invalid_request")
    if policy_result.get("action") == "block":
        block_reasons.append("policy_blocked")
    if input_total > config.MAX_INPUT_BYTES_PER_RUN:
        block_reasons.append("input_byte_limit_exceeded")
    if output_total > config.MAX_OUTPUT_TOKENS_PER_RUN:
        block_reasons.append("output_token_limit_exceeded")
    if web_search_requested and not config.WEB_SEARCH_ENABLED:
        block_reasons.append("web_search_disabled")
    warnings = []
    if billable:
        warnings.append(
            {
                "code": "billable_live_api",
                "message": "再生成は新しい実API呼び出しとして課金される可能性があります。",
            }
        )
    if web_search_effective:
        warnings.append(
            {
                "code": "web_search_billable_tool",
                "message": "再生成でもWeb検索tool分の利用量や料金が追加される場合があります。",
            }
        )
    plan = {
        "allowed": not block_reasons,
        "block_reasons": block_reasons,
        "billable": billable,
        "mode": config.mode(),
        "regeneration": {"target": req.target, "provider": source},
        "options": {
            "tier": tier,
            "debate_effective": False,
            "synthesize_effective": req.target == "synthesis",
            "blind": (turn.get("options") or {}).get("blind") is True,
            "web_search_requested": web_search_requested,
            "web_search_effective": web_search_effective,
        },
        "providers": providers,
        "synthesizer": synthesizer,
        "web_search": {
            "requested": web_search_requested,
            "effective": web_search_effective,
            "initial_answer_only": True,
            "configured_max_uses": config.WEB_SEARCH_MAX_USES,
            "strict_total_limit": source == "claude",
        },
        "calls": {
            "answers": 1 if req.target == "answer" else 0,
            "debate": 0,
            "synthesis": 1 if req.target == "synthesis" else 0,
            "total": 1,
        },
        "retry_envelope": {
            "configured_retries_per_live_call": config.HTTP_RETRIES,
            "live_initial_calls": 1 if billable else 0,
            "additional_http_attempts": config.HTTP_RETRIES if billable else 0,
            "total_provider_executions": attempts,
            "max_output_tokens": output_total,
            "disclaimer": "再生成の再試行も別のHTTP試行として課金される可能性があります。",
        },
        "input_envelope": {
            "unit": "utf8_bytes",
            "history": 0,
            "answer_per_call": answer_input,
            "answers_total": answer_input,
            "debate_per_call": 0,
            "debate_total": 0,
            "synthesis": synthesis_input,
            "total": answer_input + synthesis_input,
            "live_initial_total": answer_input + synthesis_input if billable else 0,
            "live_with_retries": input_total if billable else 0,
            "total_with_retries": input_total,
            "token_count_estimated": False,
            "disclaimer": "UTF-8 byte量を入力tokenの安全側上限として金額予約に使います。",
        },
        "max_output_tokens": {
            "per_call": config.MAX_OUTPUT_TOKENS[tier],
            "answers": config.MAX_OUTPUT_TOKENS[tier]
            if req.target == "answer"
            else 0,
            "debate": 0,
            "synthesis": config.MAX_OUTPUT_TOKENS[tier]
            if req.target == "synthesis"
            else 0,
            "total": config.MAX_OUTPUT_TOKENS[tier],
            "live_total": config.MAX_OUTPUT_TOKENS[tier] if billable else 0,
        },
        "limits": {
            "max_provider_calls_per_run": config.MAX_PROVIDER_CALLS_PER_RUN,
            "max_output_tokens_per_run": config.MAX_OUTPUT_TOKENS_PER_RUN,
            "max_input_bytes_per_run": config.MAX_INPUT_BYTES_PER_RUN,
            "provider_calls_exceeded": False,
            "output_tokens_exceeded": output_total
            > config.MAX_OUTPUT_TOKENS_PER_RUN,
            "input_bytes_exceeded": input_total > config.MAX_INPUT_BYTES_PER_RUN,
        },
        "policy": policy_result,
        "attachments": {
            "count": len(attachment_refs),
            "items": attachment_refs,
            "text_included_count": sum(
                1 for item in attachment_refs if item.get("included_in_prompt") is True
            ),
        },
        "warnings": warnings,
    }
    return budget_guard.decorate_plan(plan, store)


def _regeneration_fingerprint(
    conversation_id: str,
    turn_request_id: str,
    req: RegenerationRequest,
) -> str:
    canonical = json.dumps(
        {
            "conversation_id": conversation_id,
            "turn_request_id": turn_request_id,
            "target": req.target,
            "provider": req.provider,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ensure_original_attempt(
    turn: dict[str, Any],
    *,
    target_key: str,
    target: str,
    provider: str,
    current: dict[str, Any],
) -> str:
    attempts = turn.setdefault("attempts", [])
    active = turn.setdefault("active_attempts", {})
    existing = active.get(target_key)
    if isinstance(existing, str) and existing:
        return existing
    seed = f"{turn.get('request_id')}:{target_key}:original"
    attempt_id = "original-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    attempts.append(
        {
            "attempt_id": attempt_id,
            "parent_attempt_id": None,
            "target": target,
            "provider": provider,
            "status": "completed",
            "created_at": turn.get("created_at") or utc_now(),
            "completed_at": turn.get("created_at") or utc_now(),
            "original": True,
            "result": deepcopy(current),
        }
    )
    active[target_key] = attempt_id
    return attempt_id


def _find_attempt(turn: dict[str, Any], attempt_id: str) -> dict[str, Any] | None:
    for attempt in turn.get("attempts") or []:
        if isinstance(attempt, dict) and attempt.get("attempt_id") == attempt_id:
            return attempt
    return None


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
        "debate": req.debate,
        "providers": sorted(set(req.providers or [])),
        "synthesize": req.synthesize,
        "blind": req.blind,
        "web_search": req.web_search,
        "attachment_ids": sorted(set(req.attachment_ids)),
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


@dataclass(slots=True)
class RunState:
    request_id: str
    conversation_id: str
    request_fingerprint: str
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    done: bool = False
    created_at: float = field(default_factory=time.monotonic)
    completed_at: float | None = None
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task | None = None

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        async with self.condition:
            self.events.append((event, _sanitize_event_data(event, data)))
            self.condition.notify_all()

    async def finish(self) -> None:
        async with self.condition:
            self.done = True
            self.completed_at = time.monotonic()
            self.condition.notify_all()

    async def subscribe(self, start_index: int = 0) -> AsyncIterator[str]:
        index = max(0, start_index)
        while True:
            item: tuple[str, dict[str, Any]] | None = None
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
                            self.condition.wait(), timeout=config.SSE_PING_SEC
                        )
                    except asyncio.TimeoutError:
                        timed_out = True
            if item is not None:
                yield _sse(item[0], item[1], index)
            elif timed_out:
                yield f": ping {int(time.time())}\n\n"


class RunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, RunState] = {}
        self._lock = asyncio.Lock()

    async def claim(
        self,
        request_id: str,
        state_factory: Callable[[], RunState],
    ) -> tuple[RunState, bool]:
        """request_idの状態作成を原子的に一度だけ行う。"""
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

    def _cleanup_locked(self) -> None:
        now = time.monotonic()
        stale = [
            request_id
            for request_id, run in self._runs.items()
            if run.done
            and run.completed_at is not None
            and now - run.completed_at > config.RUN_RETENTION_SEC
        ]
        for request_id in stale:
            self._runs.pop(request_id, None)


_registry = RunRegistry()


async def _claim_conversation_run(conversation_id: str, request_id: str) -> bool:
    async with _active_conversation_guard:
        existing = _active_conversation_runs.get(conversation_id)
        if existing is not None and existing != request_id:
            return False
        _active_conversation_runs[conversation_id] = request_id
        return True


async def _release_conversation_run(conversation_id: str, request_id: str) -> None:
    async with _active_conversation_guard:
        if _active_conversation_runs.get(conversation_id) == request_id:
            _active_conversation_runs.pop(conversation_id, None)


def _sse(event: str, data: dict[str, Any], event_id: int) -> str:
    payload = json.dumps(
        _scrub_public(data),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {event_id}\nevent: {event}\ndata: {payload}\n\n"


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
        "options": effective.public_dict(),
        "resume_request": {
            "tier": req.tier,
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


async def _execute_run(state: RunState, req: ChatRequest) -> None:
    cancelled_summary: dict[str, Any] | None = None
    failed_summary: dict[str, Any] | None = None
    try:
        async with _run_slots:
            lock = _conversation_locks[state.conversation_id]
            async with lock:
                conversation = store.load(state.conversation_id)
                saved = store.find_turn_by_request_id(conversation, state.request_id)
                if saved is not None:
                    for event, data in _events_from_saved_turn(conversation, saved):
                        await state.publish(event, data)
                    return

                options = orchestrator.TurnOptions(
                    tier=req.tier,
                    debate=req.debate,
                    providers=tuple(req.providers or ()),
                    synthesize=req.synthesize,
                    blind=req.blind,
                    web_search=req.web_search,
                )
                attachment_context, attachment_refs = attachment_store.build_context(
                    state.conversation_id,
                    req.attachment_ids,
                )
                pending = _new_pending_turn(req, options, state, attachment_refs)
                if not conversation.get("turns") and pending.get(
                    "clean_message"
                ) not in {"", "!help"}:
                    conversation["title"] = " ".join(
                        pending["clean_message"].split()
                    )[:60]
                turns = conversation.setdefault("turns", [])
                turns.append(pending)
                pending_index = len(turns) - 1
                store.save(conversation)

                async def durable_emit(event: str, data: dict[str, Any]) -> None:
                    await state.publish(event, data)
                    _sync_partial_turn_from_events(
                        pending,
                        state,
                        run_status="running",
                    )
                    store.save(conversation)

                try:
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
                except asyncio.CancelledError:
                    _sync_partial_turn_from_events(
                        pending,
                        state,
                        run_status="cancelled",
                    )
                    pending = _sanitize_turn(pending)
                    turns[pending_index] = pending
                    store.save(conversation)
                    budget_guard.settle(
                        state.request_id,
                        usage_reconciled=False,
                    )
                    cancelled_summary = store.summary(conversation)
                    raise
                except Exception:
                    _sync_partial_turn_from_events(
                        pending,
                        state,
                        run_status="failed",
                    )
                    pending = _sanitize_turn(pending)
                    turns[pending_index] = pending
                    try:
                        store.save(conversation)
                        budget_guard.settle(
                            state.request_id,
                            usage_reconciled=False,
                        )
                        failed_summary = store.summary(conversation)
                    except Exception:
                        pass
                    raise
                else:
                    turn = _sanitize_turn(turn)
                    turn["attachment_ids"] = list(req.attachment_ids)
                    turn["attachment_conversation_id"] = state.conversation_id
                    turn["attachments"] = deepcopy(attachment_refs)
                    turn["request_fingerprint"] = state.request_fingerprint
                    turn["event_log"] = [
                        {"event": event, "data": deepcopy(data)}
                        for event, data in state.events
                    ]
                    turn["status"] = "completed"
                    turn["cancelled"] = False
                    turn["failed"] = False
                    turn["usage_may_be_incomplete"] = False
                    turns[pending_index] = turn
                    store.save(conversation)
                    budget_guard.settle(
                        state.request_id,
                        usage_reconciled=finance.turn_usage_reconciled(turn),
                    )
                await state.publish(
                    "done",
                    {
                        "request_id": state.request_id,
                        "conversation": store.summary(conversation),
                    },
                )
    except asyncio.CancelledError:
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
        await _release_conversation_run(state.conversation_id, state.request_id)
        await state.finish()


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
    return {
        "ok": True,
        "version": app.version,
        "mode": config.mode(),
        "active_workers": config.active_workers(),
        "synthesizer": config.synthesizer_name(),
        "auth_required": bool(config.AUTH_TOKEN),
        "single_process_enforced": True,
    }


@app.get("/api/settings", dependencies=[Depends(check_auth)])
def settings() -> dict[str, Any]:
    result = config.public_settings()
    result["finance"] = budget_guard.public_snapshot(store)
    return result


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
    snapshot = telemetry.local_snapshot(store)
    snapshot["finance"] = budget_guard.public_snapshot(store)
    snapshot["admin"] = await admin_telemetry.snapshot()
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
    async with _conversation_locks[canonical_id]:
        try:
            conversation_data = store.load(canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        turn_index = _turn_index_by_request_id(conversation_data, turn_request_id)
        return _regeneration_plan(conversation_data, turn_index, req)


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
    await _rate_limiter.check(_rate_limit_key(request), req.regeneration_id)
    claimed = await _claim_conversation_run(canonical_id, req.regeneration_id)
    if not claimed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "conversation_busy",
                "message": "この会話では別の生成処理が実行中です。",
            },
        )
    dispatch_started = False
    try:
        async with _run_slots:
            async with _conversation_locks[canonical_id]:
                try:
                    conversation_data = store.load(canonical_id)
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
                fingerprint = _regeneration_fingerprint(
                    canonical_id,
                    turn_request_id,
                    req,
                )
                existing = _find_attempt(turn, req.regeneration_id)
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
                        return public if isinstance(public, dict) else {}
                    raise HTTPException(
                        status_code=409,
                        detail="同じ再生成attemptが実行中です",
                    )

                plan = _regeneration_plan(conversation_data, turn_index, req)
                _enforce_run_limits(plan)
                required = []
                if plan.get("billable") and not req.confirm_live_api:
                    required.append("confirm_live_api")
                if (
                    plan.get("billable")
                    and (plan.get("policy") or {}).get("action") == "confirm"
                    and not req.confirm_sensitive_data
                ):
                    required.append("confirm_sensitive_data")
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

                parent_attempt_id = _ensure_original_attempt(
                    turn,
                    target_key=target_key,
                    target=req.target,
                    provider=(
                        str(current.get("source") or provider_name)
                        if req.target == "synthesis"
                        else provider_name
                    ),
                    current=current,
                )
                reservation = budget_guard.reserve(
                    request_id=req.regeneration_id,
                    request_fingerprint=fingerprint,
                    plan=plan,
                    store=store,
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
                    "usage_may_be_incomplete": True,
                }
                turn.setdefault("attempts", []).append(attempt)
                try:
                    store.save(conversation_data)
                except Exception:
                    budget_guard.release_undispatched(req.regeneration_id)
                    raise

                attempt["status"] = "dispatching"
                attempt["updated_at"] = utc_now()
                store.save(conversation_data)
                dispatch_started = True

                tier = str((turn.get("options") or {}).get("tier") or "balanced")
                if tier not in {"low", "balanced", "high"}:
                    tier = "balanced"
                if req.target == "answer":
                    context = deepcopy(conversation_data)
                    context["turns"] = deepcopy(
                        conversation_data.get("turns", [])[:turn_index]
                    )
                    message = str(
                        turn.get("clean_message") or turn.get("message") or ""
                    )
                    attachment_context, _attachment_refs = _attachment_context_for_turn(
                        conversation_data,
                        turn,
                    )
                    model_message = message + attachment_context
                    result = await orchestrator._run_provider(
                        provider_name,
                        orchestrator._worker_prompt(context, model_message),
                        system=(
                            orchestrator.WORKER_SYSTEM
                            + " 前回回答を参照せず、同じ質問へ新しい独立回答を作ってください。"
                        ),
                        tier=tier,
                        round_number=1,
                        prompt_cache_key=orchestrator._prompt_cache_key(
                            canonical_id,
                            provider_name,
                        ),
                        redact_confirm=True,
                        web_search=(
                            bool((turn.get("options") or {}).get("web_search"))
                            and config.WEB_SEARCH_ENABLED
                        ),
                    )
                else:
                    message = str(
                        turn.get("clean_message") or turn.get("message") or ""
                    )
                    attachment_context, _attachment_refs = _attachment_context_for_turn(
                        conversation_data,
                        turn,
                    )
                    model_message = message + attachment_context
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
                    result = await orchestrator._run_synthesis(
                        model_message,
                        answers,
                        tier,
                        aliases,
                        canonical_id,
                    )

                result = _scrub_public(result)
                if not isinstance(result, dict):
                    result = {
                        "ok": False,
                        "error": "再生成結果を安全に処理できませんでした",
                        "usage": {},
                        "usage_may_be_incomplete": True,
                    }
                attempt["result"] = deepcopy(result)
                attempt["status"] = "completed" if result.get("ok") else "failed"
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
                        turn.setdefault("answers", {})[provider_name] = deepcopy(result)
                        successful = [
                            {"source": name, "text": str(value.get("text") or "")}
                            for name, value in (turn.get("answers") or {}).items()
                            if isinstance(value, dict) and value.get("ok")
                        ]
                        turn["insights"] = orchestrator.analyze_insights(successful)
                        turn["synthesis_stale"] = True
                    else:
                        turn["synthesis"] = deepcopy(result)
                        turn["synthesis_stale"] = False
                store.save(conversation_data)
                budget_guard.settle(
                    req.regeneration_id,
                    usage_reconciled=finance.turn_usage_reconciled(
                        {
                            "answers": {provider_name: result}
                            if req.target == "answer"
                            else {},
                            "synthesis": result
                            if req.target == "synthesis"
                            else {},
                        }
                    ),
                )
                public = _scrub_public(
                    {
                        "attempt": attempt,
                        "conversation": conversation_data,
                        "replayed": False,
                    }
                )
                return public if isinstance(public, dict) else {}
    except asyncio.CancelledError:
        if dispatch_started:
            budget_guard.settle(req.regeneration_id, usage_reconciled=False)
        raise
    except HTTPException:
        raise
    except Exception as exc:
        if dispatch_started:
            budget_guard.settle(req.regeneration_id, usage_reconciled=False)
        logger.error(
            "regeneration failed regeneration_id=%s exception_type=%s",
            req.regeneration_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="再生成に失敗しました",
        ) from exc
    finally:
        await _release_conversation_run(canonical_id, req.regeneration_id)


@app.post("/api/plan", dependencies=[Depends(check_auth)])
def plan(req: PlanRequest) -> dict[str, Any]:
    """課金APIを一切呼ばず、現在の設定による会議の安全側上限を返す。"""
    return _plan_from_request(req)


@app.post("/api/policy/scan", dependencies=[Depends(check_auth)])
def scan_policy(req: PolicyScanRequest) -> dict[str, Any]:
    """課金APIや外部サービスを使わず、送信前の文字列をローカル検査する。"""
    return policy.scan_text(req.text)


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
    try:
        store.load(canonical_id)
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
    )


@app.delete(
    "/api/conversations/{conversation_id}/attachments/{attachment_id}",
    dependencies=[Depends(check_auth)],
)
def delete_attachment(conversation_id: str, attachment_id: str) -> dict[str, bool]:
    canonical_id = _canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    try:
        attachment_store.delete(canonical_id, attachment_id)
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
    async with _conversation_locks[canonical_id]:
        try:
            data = store.load(canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        data["title"] = req.title.strip()
        store.save(data)
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
    async with _conversation_locks[canonical_id]:
        try:
            conversation_data = store.load(canonical_id)
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
        store.save(conversation_data)
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
    async with _conversation_locks[canonical_id]:
        try:
            parent = store.load(canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        turn_index = _turn_index_by_request_id(parent, turn_request_id)
        target = parent["turns"][turn_index]
        if not isinstance(target, dict) or target.get("status") != "completed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="完了済みターンだけを編集分岐できます",
            )
        branch = store.create_branch(
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
    async with _conversation_locks[canonical_id]:
        try:
            attachment_store.delete_conversation(canonical_id)
            store.delete(canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        return {"ok": True}


@app.post("/api/chat", dependencies=[Depends(check_auth)])
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    run_plan = _plan_from_request(req)
    _enforce_run_limits(run_plan)
    _enforce_explicit_confirmations(run_plan, req)
    request_id = _resolve_request_id(req, request)
    start_index = _last_event_index(request)
    requested_conversation_id = _canonical_conversation_id(req.conversation_id)
    fingerprint = _request_fingerprint(req)
    if start_index > 0:
        resumable = await _registry.lookup(request_id)
        if resumable is None and store.find_conversation_by_request_id(request_id) is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "resume_not_available",
                    "message": "指定されたLast-Event-IDを再開できません",
                    "max_event_id": 0,
                },
            )
    await _rate_limiter.check(_rate_limit_key(request), request_id)

    def state_factory() -> RunState:
        found = store.find_conversation_by_request_id(request_id)
        if found is not None:
            conversation_data, saved_turn = found
            if (
                requested_conversation_id is not None
                and requested_conversation_id != conversation_data["id"]
            ):
                raise HTTPException(
                    status_code=409,
                    detail="request_idが別の会話で使用済みです",
                )
            saved_fingerprint = saved_turn.get("request_fingerprint")
            if isinstance(saved_fingerprint, str) and saved_fingerprint != fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail="request_idが異なるリクエストで使用済みです",
                )
        elif requested_conversation_id is not None:
            try:
                conversation_data = store.load(requested_conversation_id)
            except ConversationNotFound as exc:
                raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        else:
            conversation_data = store.create(req.message)
        return RunState(
            request_id=request_id,
            conversation_id=conversation_data["id"],
            request_fingerprint=fingerprint,
        )

    state, created = await _registry.claim(request_id, state_factory)
    if state.request_fingerprint != fingerprint:
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
        conversation_data = store.load(state.conversation_id)
        saved_turn = store.find_turn_by_request_id(conversation_data, request_id)
        if saved_turn is not None:
            for event, data in _events_from_saved_turn(conversation_data, saved_turn):
                await state.publish(event, data)
            await state.finish()
        else:
            claimed = await _claim_conversation_run(
                state.conversation_id,
                state.request_id,
            )
            if not claimed:
                await _registry.remove(request_id, state)
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "conversation_busy",
                        "message": "この会話では別の会議が実行中です。完了後に再試行してください。",
                    },
                )
            try:
                budget_guard.reserve(
                    request_id=state.request_id,
                    request_fingerprint=state.request_fingerprint,
                    plan=run_plan,
                    store=store,
                )
                state.task = asyncio.create_task(_execute_run(state, req))
            except finance.BudgetViolation as exc:
                await _release_conversation_run(
                    state.conversation_id,
                    state.request_id,
                )
                await _registry.remove(request_id, state)
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": exc.code,
                        "message": str(exc),
                        "budget": exc.snapshot,
                    },
                ) from exc
            except Exception:
                budget_guard.release_undispatched(state.request_id)
                await _release_conversation_run(
                    state.conversation_id,
                    state.request_id,
                )
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
    state = await _registry.lookup(request_id)
    if state is None:
        raise HTTPException(status_code=404, detail="実行中の会議が見つかりません")
    if state.done or state.task is None or state.task.done():
        return {"ok": True, "already_done": True, "request_id": request_id}
    state.task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(state.task), timeout=5.0)
    except asyncio.CancelledError:
        pass
    except asyncio.TimeoutError:
        logger.warning("cancel persistence timed out request_id=%s", request_id)
    except Exception:
        logger.exception("cancel persistence failed request_id=%s", request_id)
    return {
        "ok": True,
        "cancellation_requested": True,
        "cancelled": state.done,
        "provider_stop_guaranteed": False,
        "warning": "外部Provider側の処理停止・課金停止は保証されません",
        "request_id": request_id,
    }
