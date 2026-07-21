# -*- coding: utf-8 -*-
"""再生成attemptのpureな識別・履歴操作。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable


ACTIVE_ATTEMPT_STATUSES = frozenset({"reserved", "running", "dispatching"})
ANSWER_REGENERATION_INSTRUCTION = (
    "前回回答を参照せず、同じ質問へ新しい独立回答を作ってください。"
)


class TargetError(ValueError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class PlanError(ValueError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class PlanDependencies:
    """再生成planが参照する動的設定と副作用境界。"""

    config: Any
    orchestrator: Any
    runtime_snapshot: Callable[[], dict[str, Any]]
    scan_text: Callable[[str], dict[str, Any]]
    attachment_context: Callable[
        [dict[str, Any], dict[str, Any]],
        tuple[str, list[dict[str, Any]]],
    ]
    decorate_plan: Callable[[dict[str, Any]], dict[str, Any]]


def fingerprint(
    conversation_id: str,
    turn_request_id: str,
    *,
    target: str,
    provider: str | None,
) -> str:
    canonical = json.dumps(
        {
            "conversation_id": conversation_id,
            "turn_request_id": turn_request_id,
            "target": target,
            "provider": provider,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_target(
    turn: dict[str, Any],
    *,
    target: str,
    provider: str | None,
    workers: frozenset[str] | set[str] | tuple[str, ...],
    synthesizer: str,
) -> tuple[str, str, dict[str, Any]]:
    if turn.get("status") != "completed":
        raise TargetError(409, "完了済みターンだけを再生成できます")
    if target == "answer":
        source = provider or ""
        current = (turn.get("answers") or {}).get(source)
        if source not in workers or not isinstance(current, dict):
            raise TargetError(404, "対象回答が見つかりません")
        return f"answer:{source}", source, current
    current = turn.get("synthesis")
    if not isinstance(current, dict) or current.get("skipped") is True:
        raise TargetError(404, "対象の統合回答がありません")
    return "synthesis", synthesizer, current


def build_plan(
    conversation: dict[str, Any],
    turn_index: int,
    *,
    target: str,
    provider: str | None,
    dependencies: PlanDependencies,
    attachment_bundle: tuple[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Providerを呼ばず、再生成1回の上限・課金・policy planを構築する。"""
    cfg = dependencies.config
    orch = dependencies.orchestrator
    runtime = dependencies.runtime_snapshot()
    turn = conversation["turns"][turn_index]
    _key, source, _current = resolve_target(
        turn,
        target=target,
        provider=provider,
        workers=cfg.WORKERS,
        synthesizer=cfg.synthesizer_name(runtime=runtime),
    )
    tier = str((turn.get("options") or {}).get("tier") or "balanced")
    if tier not in {"low", "balanced", "high"}:
        tier = "balanced"
    reasoning_mode = cfg.normalized_reasoning_mode(
        str((turn.get("options") or {}).get("reasoning_mode") or "auto")
    )
    message = str(turn.get("clean_message") or turn.get("message") or "")
    attachment_context, attachment_refs = (
        attachment_bundle
        if attachment_bundle is not None
        else dependencies.attachment_context(conversation, turn)
    )
    model_message = message + attachment_context
    policy_result = dependencies.scan_text(model_message)
    context = deepcopy(conversation)
    context["turns"] = deepcopy(conversation.get("turns", [])[:turn_index])
    if target == "answer":
        prompt = orch._worker_prompt(context, model_message)
        system = orch.WORKER_SYSTEM + " " + ANSWER_REGENERATION_INSTRUCTION
        status_snapshot = cfg.provider_status(source, runtime=runtime)
        mode = status_snapshot.mode
        model = (
            cfg.model_for(source, tier, runtime=runtime)
            if mode == "live"
            else "mock"
        )
        providers = [
            {
                "name": source,
                "label": status_snapshot.label,
                "mode": mode,
                "model": model,
                "billable": mode == "live",
                "max_calls": 1,
                "max_output_tokens": cfg.max_output_tokens_for(source, tier),
                "reasoning": cfg.resolve_reasoning(
                    source,
                    model,
                    reasoning_mode,
                    mock=mode != "live",
                ).public_dict(),
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
            "max_output_tokens": 0,
            "reasoning": cfg.resolve_reasoning(
                "synthesizer",
                "mock",
                reasoning_mode,
                mock=True,
            ).public_dict(),
        }
        answer_input = len((prompt + system).encode("utf-8"))
        synthesis_input = 0
        unavailable = mode == "disabled"
    else:
        answers = turn.get("answers") or {}
        successful = {
            name: value
            for name, value in answers.items()
            if name in cfg.WORKERS
            and isinstance(value, dict)
            and value.get("ok")
        }
        if not successful:
            raise PlanError(409, "成功回答がないため統合を再生成できません")
        aliases = (
            orch._blind_aliases(
                list(successful),
                str(turn.get("request_id") or "regeneration"),
            )
            if (turn.get("options") or {}).get("blind") is True
            else None
        )
        prompt = orch._synthesis_prompt(model_message, successful, aliases)
        system = orch.SYNTH_SYSTEM
        synth_name = cfg.synthesizer_name(runtime=runtime)
        mode = "mock" if synth_name == "synthesizer" else "live"
        model = cfg.synthesizer_model_for(
            tier,
            runtime=runtime,
            synthesizer=synth_name,
        )
        providers = []
        synthesizer = {
            "name": synth_name,
            "label": cfg.LABELS.get(synth_name, "Local mock synthesizer"),
            "mode": mode,
            "model": model,
            "enabled": True,
            "billable": mode == "live",
            "max_calls": 1,
            "max_output_tokens": cfg.max_output_tokens_for(synth_name, tier),
            "reasoning": cfg.resolve_reasoning(
                synth_name,
                model,
                reasoning_mode,
                mock=mode != "live",
            ).public_dict(),
        }
        source = synth_name
        answer_input = 0
        synthesis_input = len((prompt + system).encode("utf-8"))
        unavailable = False

    billable = mode == "live"
    attempts = cfg.HTTP_RETRIES + 1
    input_total = (answer_input + synthesis_input) * attempts
    descriptor = providers[0] if providers else synthesizer
    per_call_output = int(descriptor["max_output_tokens"])
    output_total = per_call_output * attempts
    block_reasons = []
    web_search_requested = bool(
        target == "answer" and (turn.get("options") or {}).get("web_search") is True
    )
    web_search_effective = bool(
        web_search_requested and cfg.WEB_SEARCH_ENABLED and mode == "live"
    )
    if unavailable:
        block_reasons.append("invalid_request")
    if policy_result.get("action") == "block":
        block_reasons.append("policy_blocked")
    if input_total > cfg.MAX_INPUT_BYTES_PER_RUN:
        block_reasons.append("input_byte_limit_exceeded")
    if output_total > cfg.MAX_OUTPUT_TOKENS_PER_RUN:
        block_reasons.append("output_token_limit_exceeded")
    if web_search_requested and not cfg.WEB_SEARCH_ENABLED:
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
    if (descriptor.get("reasoning") or {}).get("source") in {
        "unknown_model",
        "model_unsupported",
    }:
        warnings.append(
            {
                "code": "reasoning_provider_default",
                "message": "思考量を安全に固定できないmodelのため、Provider既定値を使います。",
            }
        )
    plan = {
        "allowed": not block_reasons,
        "block_reasons": block_reasons,
        "billable": billable,
        "mode": cfg.mode(),
        "regeneration": {"target": target, "provider": source},
        "options": {
            "tier": tier,
            "reasoning_mode": reasoning_mode,
            "debate_effective": False,
            "synthesize_effective": target == "synthesis",
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
            "configured_max_uses": cfg.WEB_SEARCH_MAX_USES,
            "strict_total_limit": source == "claude",
        },
        "calls": {
            "answers": 1 if target == "answer" else 0,
            "debate": 0,
            "synthesis": 1 if target == "synthesis" else 0,
            "total": 1,
        },
        "retry_envelope": {
            "configured_retries_per_live_call": cfg.HTTP_RETRIES,
            "live_initial_calls": 1 if billable else 0,
            "additional_http_attempts": cfg.HTTP_RETRIES if billable else 0,
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
            "max_per_call": per_call_output,
            "answers": per_call_output if target == "answer" else 0,
            "debate": 0,
            "synthesis": per_call_output if target == "synthesis" else 0,
            "total": per_call_output,
            "live_total": per_call_output if billable else 0,
        },
        "limits": {
            "max_provider_calls_per_run": cfg.MAX_PROVIDER_CALLS_PER_RUN,
            "max_output_tokens_per_run": cfg.MAX_OUTPUT_TOKENS_PER_RUN,
            "max_input_bytes_per_run": cfg.MAX_INPUT_BYTES_PER_RUN,
            "provider_calls_exceeded": False,
            "output_tokens_exceeded": output_total > cfg.MAX_OUTPUT_TOKENS_PER_RUN,
            "input_bytes_exceeded": input_total > cfg.MAX_INPUT_BYTES_PER_RUN,
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
    return dependencies.decorate_plan(plan)


def ensure_original_attempt(
    turn: dict[str, Any],
    *,
    target_key: str,
    target: str,
    provider: str,
    current: dict[str, Any],
    now: Callable[[], str],
) -> str:
    attempts = turn.setdefault("attempts", [])
    active = turn.setdefault("active_attempts", {})
    existing = active.get(target_key)
    if isinstance(existing, str) and existing:
        return existing
    seed = f"{turn.get('request_id')}:{target_key}:original"
    attempt_id = "original-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    created_at = turn.get("created_at") or now()
    attempts.append(
        {
            "attempt_id": attempt_id,
            "parent_attempt_id": None,
            "target": target,
            "provider": provider,
            "status": "completed",
            "created_at": created_at,
            "completed_at": created_at,
            "original": True,
            "result": deepcopy(current),
        }
    )
    active[target_key] = attempt_id
    return attempt_id


def find_attempt(
    turn: dict[str, Any],
    attempt_id: str,
) -> dict[str, Any] | None:
    for attempt in turn.get("attempts") or []:
        if isinstance(attempt, dict) and attempt.get("attempt_id") == attempt_id:
            return attempt
    return None


def required_confirmations(
    plan: dict[str, Any],
    *,
    confirm_live_api: bool,
    confirm_sensitive_data: bool,
) -> list[str]:
    required: list[str] = []
    if plan.get("billable") and not confirm_live_api:
        required.append("confirm_live_api")
    if (
        plan.get("billable")
        and (plan.get("policy") or {}).get("action") == "confirm"
        and not confirm_sensitive_data
    ):
        required.append("confirm_sensitive_data")
    return required


def interrupt_attempt(
    attempt: dict[str, Any],
    *,
    now: str,
    cancelled: bool,
) -> bool:
    if attempt.get("status") not in ACTIVE_ATTEMPT_STATUSES:
        return False
    attempt.update(
        {
            "status": "interrupted",
            "interrupted": True,
            "cancelled": cancelled,
            "usage_may_be_incomplete": True,
            "updated_at": now,
            "completed_at": now,
        }
    )
    return True
