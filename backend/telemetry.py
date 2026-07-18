# -*- coding: utf-8 -*-
"""保存済みrunだけから、課金なしでusageとquota観測を集計する。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable

import config
from storage import ConversationNotFound, ConversationStore, utc_now


_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "reasoning_tokens",
    "tool_tokens",
    "sources_used",
    "server_side_tools_used",
)
_PORTALS = {
    "claude": "https://console.anthropic.com/settings/usage",
    "chatgpt": "https://platform.openai.com/usage",
    "gemini": "https://aistudio.google.com/usage",
    "grok": "https://console.x.ai/team/default/usage",
}


def local_snapshot(
    store: ConversationStore,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Clage Cookが保存したProvider応答だけを集計する。外部通信はしない。"""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    today = current.date()
    accumulators = {
        name: _empty_provider(name)
        for name in config.WORKERS
    }
    conversation_count = 0
    turn_count = 0

    for summary in store.list():
        try:
            conversation = store.load(str(summary.get("id") or ""))
        except ConversationNotFound:
            continue
        conversation_count += 1
        for turn in conversation.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            turn_count += 1
            created = _parse_timestamp(turn.get("created_at"))
            is_today = created is not None and created.date() == today
            for source, entry, phase in _turn_entries(turn):
                if source not in accumulators:
                    continue
                _add_entry(accumulators[source], entry, phase, is_today)

    providers = []
    for name in config.WORKERS:
        item = accumulators[name]
        status = config.provider_status(name)
        item.update(
            {
                "label": status.label,
                "configured": status.configured,
                "mode": status.mode,
                "capabilities": {
                    "per_request_usage": True,
                    "rate_limit_response_headers": name
                    in {"claude", "chatgpt", "grok"},
                    "aggregate_admin_api": name in {"claude", "chatgpt", "grok"},
                    "admin_telemetry_configured": bool(
                        {
                            "claude": config.ANTHROPIC_ADMIN_KEY,
                            "chatgpt": config.OPENAI_ADMIN_KEY,
                            "gemini": "",
                            "grok": config.XAI_MANAGEMENT_KEY,
                        }[name]
                    ),
                    "credit_balance_api": name == "grok",
                    "portal_url": _PORTALS[name],
                },
            }
        )
        providers.append(item)

    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "scope": "local_conversation_store",
        "timezone": "UTC",
        "conversation_count": conversation_count,
        "turn_count": turn_count,
        "providers": providers,
        "limitations": [
            "この集計はClage Cookが保存できたProvider応答だけを対象にします。",
            "timeout・切断・保存前クラッシュ等の利用量は含まれない可能性があります。",
            "quota観測は通常API応答headerのスナップショットで、残高や請求上限ではありません。",
            "金額、組織全体の利用量、入金済みcreditは別のadmin snapshotであり、local集計へ混ぜません。",
        ],
    }


def _empty_period() -> dict[str, Any]:
    return {
        "observed_requests": 0,
        "usage_unknown_requests": 0,
        "usage": {},
        "models": {},
    }


def _empty_provider(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "usage": {
            "all_time": _empty_period(),
            "today": _empty_period(),
        },
        "latest_quota_snapshot": {},
    }


def _turn_entries(
    turn: dict[str, Any],
) -> Iterable[tuple[str, dict[str, Any], str]]:
    represented: set[str] = set()
    attempts = turn.get("attempts")
    if isinstance(attempts, list):
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            result = attempt.get("result")
            target = attempt.get("target")
            provider = attempt.get("provider")
            if not isinstance(result, dict) or target not in {"answer", "synthesis"}:
                continue
            if target == "synthesis":
                source = result.get("source") or provider
                represented.add("synthesis")
            else:
                source = provider
                if isinstance(source, str):
                    represented.add(f"answer:{source}")
            if isinstance(source, str):
                phase = "original" if attempt.get("original") is True else "regeneration"
                yield source, result, phase

    answers = turn.get("answers")
    if isinstance(answers, dict):
        for source, entry in answers.items():
            if (
                isinstance(source, str)
                and isinstance(entry, dict)
                and f"answer:{source}" not in represented
            ):
                yield source, entry, "answer"
    synthesis = turn.get("synthesis")
    if isinstance(synthesis, dict) and "synthesis" not in represented:
        source = synthesis.get("source")
        if isinstance(source, str) and source in config.WORKERS:
            yield source, synthesis, "synthesis"


def _add_entry(
    provider: dict[str, Any],
    entry: dict[str, Any],
    phase: str,
    is_today: bool,
) -> None:
    periods = [provider["usage"]["all_time"]]
    if is_today:
        periods.append(provider["usage"]["today"])
    usage = _safe_usage(entry.get("usage"))
    usage_unknown = entry.get("usage_may_be_incomplete") is True or not usage
    model = str(entry.get("model") or "unknown").strip()[:160] or "unknown"
    for period in periods:
        period["observed_requests"] += 1
        if usage_unknown:
            period["usage_unknown_requests"] += 1
        _merge_usage(period["usage"], usage)
        model_usage = period["models"].setdefault(
            model,
            {
                "observed_requests": 0,
                "usage_unknown_requests": 0,
                "usage": {},
                "phases": {},
            },
        )
        model_usage["observed_requests"] += 1
        if usage_unknown:
            model_usage["usage_unknown_requests"] += 1
        _merge_usage(model_usage["usage"], usage)
        model_usage["phases"][phase] = model_usage["phases"].get(phase, 0) + 1

    snapshot = entry.get("quota_snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        return
    current = provider.get("latest_quota_snapshot")
    if not isinstance(current, dict) or _snapshot_order(snapshot) >= _snapshot_order(
        current
    ):
        provider["latest_quota_snapshot"] = deepcopy(snapshot)


def _safe_usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key in _USAGE_KEYS
        if isinstance((value := raw.get(key)), int)
        and not isinstance(value, bool)
        and value >= 0
    }


def _merge_usage(target: dict[str, int], usage: dict[str, int]) -> None:
    for key, value in usage.items():
        target[key] = target.get(key, 0) + value


def _snapshot_order(snapshot: dict[str, Any]) -> str:
    observed = snapshot.get("observed_at")
    return observed if isinstance(observed, str) else ""


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
