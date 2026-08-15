# -*- coding: utf-8 -*-
"""再生成API(regeneration-plan / regenerate)のAPIRouter。

main.pyから分離(issue #11)。

設計上の重要事項: store・budget_guard・_registry・_rate_limiter・_run_slots・
_hold_conversation_lock・_claim_conversation_run等は、テストが
「monkeypatch.setattr(main, "store", ...)」のようにmainモジュールのglobalを
差し替える前提のため、本モジュールでは import時に束縛せず、必ず呼出時に
`main.store` のように遅延参照する。mainは本モジュールを末尾でimportして
`app.include_router(router)` する(mainより先に本モジュールをimportしない)。
"""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

import config
import finance
import orchestrator
import policy
import regeneration
import main
from api_models import RegenerationPlanRequest, RegenerationRequest, _valid_request_id
from runs import RunState
from sanitizing import _scrub_public, _scrub_public_dict
from storage import ConversationNotFound, utc_now


logger = logging.getLogger("clage_cook")

router = APIRouter()


def _check_auth(request: Request) -> None:
    """main.check_authへの遅延委譲(認証設定の一元管理を維持)。"""
    main.check_auth(request)


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
        attachment_context=main._attachment_context_for_turn,
        decorate_plan=lambda plan: main.budget_guard.decorate_plan(plan, main.store),
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


@router.post(
    "/api/conversations/{conversation_id}/turns/{turn_request_id}/regeneration-plan",
    dependencies=[Depends(_check_auth)],
)
async def regeneration_plan(
    conversation_id: str,
    turn_request_id: str,
    req: RegenerationPlanRequest,
) -> dict[str, Any]:
    canonical_id = main._canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    if not _valid_request_id(turn_request_id):
        raise HTTPException(status_code=404, detail="対象ターンが見つかりません")
    async with main._hold_conversation_lock(canonical_id):
        try:
            conversation_data = await main._blocking_call(main.store.load, canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        turn_index = main._turn_index_by_request_id(conversation_data, turn_request_id)
        result = await main._blocking_call(
            _regeneration_plan,
            conversation_data,
            turn_index,
            req,
        )
        return _scrub_public_dict(result)


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
    async with main._hold_conversation_lock(conversation_id):
        try:
            conversation_data = await main._blocking_call(
                main.store.load, conversation_id
            )
            turn_index = main._turn_index_by_request_id(
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
        await main._blocking_call(main.store.save, conversation_data)


def _regeneration_result_payload(
    attempt: dict[str, Any],
    conversation_data: dict[str, Any],
    *,
    replayed: bool,
) -> dict[str, Any]:
    """attemptと会話全体をscrubし、replay有無付きの公開応答へ包む。"""
    return _scrub_public_dict(
        {
            "attempt": attempt,
            "conversation": conversation_data,
            "replayed": replayed,
        }
    )


def _saved_regeneration_replay(
    conversation_id: str,
    turn_request_id: str,
    regeneration_id: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    """保存済みterminal attemptをrate/active claimより先に無課金再生する。"""
    try:
        conversation_data = main.store.load(conversation_id)
        turn_index = main._turn_index_by_request_id(
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
    return _regeneration_result_payload(
        existing,
        conversation_data,
        replayed=True,
    )


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
        async with main._run_slots:
            async with main._hold_conversation_lock(conversation_id):
                try:
                    conversation_data = await main._blocking_call(
                        main.store.load, conversation_id
                    )
                except ConversationNotFound as exc:
                    raise HTTPException(
                        status_code=404,
                        detail="会話が見つかりません",
                    ) from exc
                turn_index = main._turn_index_by_request_id(
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
                        state.result = _regeneration_result_payload(
                            existing,
                            conversation_data,
                            replayed=True,
                        )
                        return
                    raise HTTPException(
                        status_code=409,
                        detail="同じ再生成attemptが実行中です",
                    )

                attachment_bundle = await main._blocking_call(
                    main._attachment_context_for_turn,
                    conversation_data,
                    turn,
                )
                plan = await main._blocking_call(
                    _regeneration_plan,
                    conversation_data,
                    turn_index,
                    req,
                    attachment_bundle=attachment_bundle,
                )
                main._enforce_run_limits(plan)
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
                reservation = await main._blocking_call(
                    main.budget_guard.reserve,
                    request_id=req.regeneration_id,
                    request_fingerprint=fingerprint,
                    plan=plan,
                    store=main.store,
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
                    "execution_snapshot": main._execution_snapshot_from_plan(plan),
                    "usage_may_be_incomplete": True,
                }
                turn.setdefault("attempts", []).append(attempt)
                await main._blocking_call(main.store.save, conversation_data)
                attempt["status"] = "dispatching"
                attempt["updated_at"] = utc_now()
                await main._blocking_call(main.store.save, conversation_data)

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
                async with main._hold_conversation_lock(conversation_id):
                    conversation_data = await main._blocking_call(
                        main.store.load,
                        conversation_id,
                    )
                    turn_index = main._turn_index_by_request_id(
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
                    await main._blocking_call(main.store.save, conversation_data)

                await main._blocking_call(
                    main.budget_guard.settle,
                    req.regeneration_id,
                    usage_reconciled=finance.turn_usage_reconciled(
                        billing_turn
                    ),
                    turn=billing_turn,
                )
                budget_finalized = True
                state.terminal_outcome = "completed"
                return _regeneration_result_payload(
                    attempt,
                    conversation_data,
                    replayed=False,
                )

            # Provider結果後は保存・budget settle・公開結果を一体で完走する。
            state.result, _ = await main._complete_critical(
                finalize_regeneration_result()
            )
    except asyncio.CancelledError:
        state.terminal_outcome = "cancelled"
        if reserve_started and not budget_finalized:
            await main._finalize_budget_after_abort(
                req.regeneration_id,
                dispatch_started=dispatch_started,
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
                await main._finalize_budget_after_abort(
                    req.regeneration_id,
                    dispatch_started=dispatch_started,
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
        await main._complete_critical(finalize_http_failure())
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
                await main._finalize_budget_after_abort(
                    req.regeneration_id,
                    dispatch_started=dispatch_started,
                )
                budget_finalized = True
            await _mark_regeneration_interrupted(
                conversation_id,
                turn_request_id,
                req.regeneration_id,
                cancelled=False,
            )

        await main._complete_critical(finalize_unexpected_failure())
    finally:
        await main._finalize_background_run(state)
        # 再生成は完了attemptをdurable storeから再生できる。会話全体を含む
        # state.resultを共通1時間retentionへ残さず、待機中callerの参照だけにする。
        await main._registry.remove(state.request_id, state)


@router.post(
    "/api/conversations/{conversation_id}/turns/{turn_request_id}/regenerate",
    dependencies=[Depends(_check_auth)],
)
async def regenerate_turn_result(
    conversation_id: str,
    turn_request_id: str,
    req: RegenerationRequest,
    request: Request,
) -> dict[str, Any]:
    canonical_id = main._canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    if not _valid_request_id(turn_request_id):
        raise HTTPException(status_code=404, detail="対象ターンが見つかりません")
    fingerprint = regeneration.fingerprint(
        canonical_id,
        turn_request_id,
        target=req.target,
        provider=req.provider,
    )
    saved_replay = await main._blocking_call(
        _saved_regeneration_replay,
        canonical_id,
        turn_request_id,
        req.regeneration_id,
        fingerprint,
    )
    if saved_replay is not None:
        return saved_replay
    await main._rate_limiter.check(
        main._rate_limit_key(request), req.regeneration_id
    )
    state, created = await main._registry.claim(
        req.regeneration_id,
        lambda: main._new_run_state(
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
            claimed = await main._claim_conversation_run(
                canonical_id,
                req.regeneration_id,
            )
        except BaseException:
            await main._release_conversation_run(canonical_id, req.regeneration_id)
            await state.finish()
            await main._registry.remove(req.regeneration_id, state)
            raise
        if not claimed:
            state.failure_status = 409
            state.failure_detail = {
                "code": "conversation_busy",
                "message": "この会話では別の生成処理が実行中です。",
            }
            await state.finish()
            await main._registry.remove(req.regeneration_id, state)
        else:
            try:
                started = await main._registry.start(
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
                    await main._release_conversation_run(
                        canonical_id,
                        req.regeneration_id,
                    )
                    await state.finish()
                    await main._registry.remove(req.regeneration_id, state)
                raise
            if not started:
                await main._release_conversation_run(
                    canonical_id, req.regeneration_id
                )
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
