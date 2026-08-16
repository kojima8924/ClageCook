# -*- coding: utf-8 -*-
"""価格表による純粋なコスト計算・課金entry集計・設定パーサ。

finance.pyから分離したモジュールレベル純粋関数群。台帳I/O
(BudgetGuard/PriceCatalog)には依存せず、durable状態を持たない。
finance.pyが全て同名で再exportし、既存のfinance.*参照と互換を保つ。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import TYPE_CHECKING, Any, Iterable

import config

if TYPE_CHECKING:
    from finance import PriceCatalog
    from storage import ConversationStore


class FinanceConfigurationError(RuntimeError):
    pass


def _positive_plan_int(value: Any) -> int | None:
    """planの整数フィールドを読む。欠落・不正・非正なら見積り不能としてNone。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def estimate_plan_cost(plan: dict[str, Any], catalog: PriceCatalog) -> dict[str, Any]:
    if not plan.get("billable"):
        return {
            "available": True,
            "complete": True,
            "currency": "USD",
            "total_micros": 0,
            "total_usd": "0.000000",
            "known_subtotal_micros": 0,
            "known_subtotal_usd": "0.000000",
            "price_version": catalog.version or None,
            "items": [],
            "method": "no_live_calls",
        }
    if not catalog.loaded:
        return {
            "available": False,
            "complete": False,
            "currency": "USD",
            "total_micros": None,
            "total_usd": None,
            "known_subtotal_micros": 0,
            "known_subtotal_usd": "0.000000",
            "price_version": None,
            "items": [],
            "missing": ["price_table"],
            "method": "configured_price_table",
        }
    envelope = plan.get("input_envelope") or {}
    attempts = (
        int(
            (plan.get("retry_envelope") or {}).get(
                "configured_retries_per_live_call",
                0,
            )
        )
        + 1
    )
    debate_effective = bool((plan.get("options") or {}).get("debate_effective"))
    items: list[dict[str, Any]] = []
    # planから出力上限が欠落していた場合は「0 token=無料」ではなく
    # 「見積り不能」として扱う。0で埋めると出力コストを過小に見積もり、
    # CLAGE_BUDGET_UNKNOWN_POLICY の「欠落は見積り不能」という思想と矛盾する。
    plan_missing: set[str] = set()
    for provider in plan.get("providers") or []:
        if not isinstance(provider, dict) or not provider.get("billable"):
            continue
        input_tokens = int(envelope.get("answer_per_call") or 0)
        if debate_effective:
            input_tokens += int(envelope.get("debate_per_call") or 0)
        calls = int(provider.get("max_calls") or 0)
        per_call_output = _positive_plan_int(provider.get("max_output_tokens"))
        if per_call_output is None:
            plan_missing.add("plan_max_output_tokens")
            per_call_output = 0
        items.append(
            _estimate_max_item(
                catalog,
                str(provider.get("name") or ""),
                str(provider.get("model") or ""),
                input_tokens * attempts,
                calls * per_call_output * attempts,
                phase="participant",
            )
        )
    synthesizer = plan.get("synthesizer") or {}
    if isinstance(synthesizer, dict) and synthesizer.get("billable"):
        calls = int(synthesizer.get("max_calls") or 0)
        per_call_output = _positive_plan_int(synthesizer.get("max_output_tokens"))
        if per_call_output is None:
            plan_missing.add("plan_max_output_tokens")
            per_call_output = 0
        items.append(
            _estimate_max_item(
                catalog,
                str(synthesizer.get("name") or ""),
                str(synthesizer.get("model") or ""),
                int(envelope.get("synthesis") or 0) * attempts,
                calls * per_call_output * attempts,
                phase="synthesis",
            )
        )
    missing = {
        missing
        for item in items
        for missing in item.get("missing", [])
    }
    missing |= plan_missing
    web_search = plan.get("web_search")
    options = plan.get("options")
    web_search_effective = (
        isinstance(web_search, dict) and web_search.get("effective") is True
    ) or (isinstance(options, dict) and options.get("web_search_effective") is True)
    if web_search_effective:
        # Providerのserver-side検索はtokenとは別に課金され、1 request内の
        # tool利用回数を全社で安全側に上限化できない。token price tableだけで
        # 金額見積りを完全とせず、unknown-cost policyへ委ねる。
        missing.add("web_search_tool_pricing")
    known_subtotal = sum(
        int(item["known_micros"])
        for item in items
        if isinstance(item.get("known_micros"), int)
    )
    complete = bool(items) and all(item["complete"] for item in items) and not missing
    total = known_subtotal if complete else None
    return {
        "available": complete,
        "complete": complete,
        "currency": "USD",
        "total_micros": total,
        "total_usd": _format_micros(total),
        "known_subtotal_micros": known_subtotal,
        "known_subtotal_usd": _format_micros(known_subtotal),
        "price_version": catalog.version,
        "items": items,
        "missing": sorted(missing),
        "method": "utf8_bytes_as_conservative_input_token_upper_bound",
        "includes_retry_envelope": True,
        "disclaimer": "価格表は利用者設定値です。実際の請求額やcredit残高ではありません。",
    }


def _reservation_amount(estimate: dict[str, Any]) -> int:
    """完全額が不明でも、価格判明済みのsubtotalは必ず予算拘束する。"""
    total = estimate.get("total_micros")
    if isinstance(total, int) and total >= 0:
        return total
    known = estimate.get("known_subtotal_micros")
    return known if isinstance(known, int) and known >= 0 else 0


def actual_cost_snapshot(
    store: ConversationStore,
    catalog: PriceCatalog,
    *,
    budget_day: str,
    budget_timezone: timezone,
) -> dict[str, Any]:
    total = 0
    unpriced = 0
    by_provider: dict[str, int] = {}
    by_reservation: dict[str, int] = {}
    incomplete_reservations: set[str] = set()
    represented_entries: set[tuple[str, ...]] = set()
    for conversation in store.load_all():
        for turn in conversation.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            for provider, entry, attempt, identity in _billing_entries(turn):
                if entry.get("mock") is True or entry.get("skipped") is True:
                    continue
                if identity is not None:
                    if identity in represented_entries:
                        continue
                    represented_entries.add(identity)
                if _billing_day(turn, attempt, budget_timezone) != budget_day:
                    continue
                result = estimate_usage_cost(
                    catalog,
                    provider,
                    str(entry.get("model") or ""),
                    entry.get("usage"),
                )
                if not result["complete"]:
                    unpriced += 1
                    reservation_id = _billing_reservation_id(turn, attempt)
                    if reservation_id is not None:
                        incomplete_reservations.add(reservation_id)
                    continue
                value = int(result["micros"])
                total += value
                by_provider[provider] = by_provider.get(provider, 0) + value
                reservation_id = _billing_reservation_id(turn, attempt)
                if reservation_id is not None:
                    by_reservation[reservation_id] = (
                        by_reservation.get(reservation_id, 0) + value
                    )
    return {
        "total_micros": total,
        "total_usd": _format_micros(total),
        "unpriced_requests": unpriced,
        "by_provider_micros": by_provider,
        "by_reservation_micros": by_reservation,
        "incomplete_reservation_ids": sorted(incomplete_reservations),
    }


def estimate_usage_cost(
    catalog: PriceCatalog,
    provider: str,
    model: str,
    raw_usage: Any,
) -> dict[str, Any]:
    price = catalog.price_for(provider, model)
    usage = _safe_usage(raw_usage)
    if price is None or not usage:
        return {"complete": False, "micros": None, "missing": [f"{provider}:{model}"]}
    cached = usage.get("cached_input_tokens", 0)
    input_tokens = usage.get("input_tokens")
    output_tokens = _billable_output_tokens(provider, usage)
    cache_write = usage.get("cache_creation_input_tokens", 0)
    if input_tokens is None or output_tokens is None:
        return {"complete": False, "micros": None, "missing": ["usage_tokens"]}
    uncached_input = _uncached_input_tokens(provider, input_tokens, cached)
    categories = (
        ("input_per_million_usd", uncached_input),
        ("output_per_million_usd", output_tokens),
        ("cached_input_per_million_usd", cached),
        ("cache_write_per_million_usd", cache_write),
    )
    missing = [field for field, tokens in categories if tokens > 0 and field not in price]
    if missing:
        return {"complete": False, "micros": None, "missing": missing}
    micros = _cost_micros(categories, price)
    return {
        "complete": True,
        "micros": micros,
        "usd": _format_micros(micros),
        "price_version": catalog.version,
        "price_snapshot": {key: str(value) for key, value in price.items()},
    }


def _uncached_input_tokens(provider: str, input_tokens: int, cached: int) -> int:
    """Provider間で異なるcache tokenの包含関係を課金区分へ正規化する。

    Anthropic usageの ``input_tokens`` はcache read/write tokenを含まない独立区分。
    他Providerの正規化済み ``input_tokens`` はcached inputを含むため差し引く。
    """
    if provider == "claude":
        return input_tokens
    return max(0, input_tokens - cached)


def _billable_output_tokens(
    provider: str,
    usage: dict[str, int],
) -> int | None:
    """reasoningの内数/外数をProvider shapeに応じて課金outputへ正規化する。"""
    output = usage.get("output_tokens")
    if output is None:
        return None
    explicit = usage.get("billable_output_tokens")
    if explicit is not None:
        return max(output, explicit)
    if provider in {"gemini", "grok"}:
        total = usage.get("total_tokens")
        input_tokens = usage.get("input_tokens")
        if total is not None and input_tokens is not None:
            # 旧保存shapeでも、input外のtokenをoutput単価側へ安全に含める。
            return max(output, total - input_tokens)
    return output


def turn_usage_reconciled(turn: dict[str, Any]) -> bool:
    entries = list(_turn_entries(turn))
    if not entries:
        return False
    for _provider, entry in entries:
        if entry.get("mock") is True or entry.get("skipped") is True:
            continue
        if entry.get("usage_may_be_incomplete") is True:
            return False
        audit = entry.get("request_audit")
        attempts = audit.get("http_attempts") if isinstance(audit, dict) else 0
        usage = _safe_usage(entry.get("usage"))
        if isinstance(attempts, int) and attempts > 0 and not usage:
            return False
    return True


def turn_cost_micros(
    turn: dict[str, Any],
    catalog: PriceCatalog,
) -> int | None:
    """1回のchat/regenerationで照合済みusageを実測token換算する。"""
    total = 0
    billable_entries = 0
    for provider, entry in _turn_entries(turn):
        if entry.get("mock") is True or entry.get("skipped") is True:
            continue
        billable_entries += 1
        estimate = estimate_usage_cost(
            catalog,
            provider,
            str(entry.get("model") or ""),
            entry.get("usage"),
        )
        if not estimate.get("complete") or not isinstance(estimate.get("micros"), int):
            return None
        total += int(estimate["micros"])
    return total if billable_entries else 0


def _estimate_max_item(
    catalog: PriceCatalog,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    phase: str,
) -> dict[str, Any]:
    price = catalog.price_for(provider, model)
    categories = (
        ("input_per_million_usd", input_tokens),
        ("output_per_million_usd", output_tokens),
    )
    missing: list[str] = []
    if price is None:
        missing.append(f"{provider}:{model}")
    else:
        for field, tokens in categories:
            if tokens > 0 and field not in price:
                missing.append(field)
    complete = price is not None and not missing
    known_categories = tuple(
        (field, tokens)
        for field, tokens in categories
        if tokens > 0 and price is not None and field in price
    )
    known_micros = _cost_micros(known_categories, price or {})
    micros = known_micros if complete else None
    return {
        "provider": provider,
        "model": model,
        "phase": phase,
        "input_token_upper_bound": input_tokens,
        "output_token_upper_bound": output_tokens,
        "complete": complete,
        "micros": micros,
        "usd": _format_micros(micros),
        "known_micros": known_micros,
        "known_usd": _format_micros(known_micros),
        "missing": missing,
        "price_snapshot": (
            {key: str(value) for key, value in (price or {}).items()}
            if price is not None
            else None
        ),
    }


def _cost_micros(
    categories: Iterable[tuple[str, int]],
    price: dict[str, Decimal],
) -> int:
    amount = sum(
        (Decimal(tokens) * price[field] for field, tokens in categories if tokens > 0),
        Decimal(0),
    )
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def _turn_entries(turn: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    answers = turn.get("answers")
    if isinstance(answers, dict):
        for provider, entry in answers.items():
            if provider in config.WORKERS and isinstance(entry, dict):
                yield provider, entry
    synthesis = turn.get("synthesis")
    if isinstance(synthesis, dict):
        provider = synthesis.get("source")
        if provider in config.WORKERS:
            yield str(provider), synthesis


def _billing_entries(
    turn: dict[str, Any],
) -> Iterable[
    tuple[
        str,
        dict[str, Any],
        dict[str, Any] | None,
        tuple[str, ...] | None,
    ]
]:
    """active表示ではなく、実際に作られた全attemptを課金集計へ渡す。"""
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
            if isinstance(source, str) and source in config.WORKERS:
                yield source, result, attempt, _billing_identity(
                    turn,
                    attempt,
                    target=str(target),
                    provider=source,
                )

    answers = turn.get("answers")
    if isinstance(answers, dict):
        for provider, entry in answers.items():
            if (
                provider in config.WORKERS
                and isinstance(entry, dict)
                and f"answer:{provider}" not in represented
            ):
                yield provider, entry, None, _billing_identity(
                    turn,
                    None,
                    target="answer",
                    provider=provider,
                )
    synthesis = turn.get("synthesis")
    if isinstance(synthesis, dict) and "synthesis" not in represented:
        provider = synthesis.get("source")
        if provider in config.WORKERS:
            yield str(provider), synthesis, None, _billing_identity(
                turn,
                None,
                target="synthesis",
                provider=str(provider),
            )


def _billing_identity(
    turn: dict[str, Any],
    attempt: dict[str, Any] | None,
    *,
    target: str,
    provider: str,
) -> tuple[str, ...] | None:
    if isinstance(attempt, dict):
        request_id = turn.get("request_id")
        if attempt.get("original") is True:
            # 再生成開始時に既存回答を包むoriginal attemptは新しいAPI呼出しでは
            # ない。branch先に残る未包装turnと同じidentityへ戻して重複を除く。
            if isinstance(request_id, str) and request_id:
                return ("turn", request_id, target, provider)
            return None
        attempt_id = attempt.get("attempt_id")
        if isinstance(attempt_id, str) and attempt_id:
            return (
                "attempt",
                request_id if isinstance(request_id, str) else "",
                attempt_id,
                target,
                provider,
            )
        fingerprint = attempt.get("request_fingerprint")
        if isinstance(fingerprint, str) and fingerprint:
            return (
                "attempt_fingerprint",
                request_id if isinstance(request_id, str) else "",
                fingerprint,
                target,
                provider,
            )
        created_at = attempt.get("created_at")
        if isinstance(created_at, str) and created_at:
            request_id = turn.get("request_id")
            if isinstance(request_id, str) and request_id:
                return ("attempt_time", request_id, created_at, target, provider)
        return None
    request_id = turn.get("request_id")
    if isinstance(request_id, str) and request_id:
        return ("turn", request_id, target, provider)
    return None


def _billing_reservation_id(
    turn: dict[str, Any],
    attempt: dict[str, Any] | None,
) -> str | None:
    """課金entryを、永続budget ledgerのrequest IDへ結び付ける。"""
    if isinstance(attempt, dict) and attempt.get("original") is not True:
        reservation = attempt.get("budget_reservation")
        if isinstance(reservation, dict):
            request_id = reservation.get("request_id")
            if isinstance(request_id, str) and request_id:
                return request_id
        attempt_id = attempt.get("attempt_id")
        if isinstance(attempt_id, str) and attempt_id:
            return attempt_id
    reservation = turn.get("budget_reservation")
    if isinstance(reservation, dict):
        request_id = reservation.get("request_id")
        if isinstance(request_id, str) and request_id:
            return request_id
    request_id = turn.get("request_id")
    return request_id if isinstance(request_id, str) and request_id else None


def _billing_day(
    turn: dict[str, Any],
    attempt: dict[str, Any] | None,
    budget_timezone: timezone,
) -> str | None:
    """課金attemptを、予約時に確定した予算日へ帰属させる。

    再生成は元turnと別の日に実行できる。予約がない構成や旧データではattemptの
    開始日時へ、attempt自体がない旧shapeではturnの開始日時へ安全にfallbackする。
    """
    if isinstance(attempt, dict):
        reservation = attempt.get("budget_reservation")
        if isinstance(reservation, dict):
            reserved_day = reservation.get("budget_day")
            if _valid_day_key(reserved_day):
                return str(reserved_day)
        if attempt.get("original") is True:
            turn_reservation = turn.get("budget_reservation")
            if isinstance(turn_reservation, dict):
                reserved_day = turn_reservation.get("budget_day")
                if _valid_day_key(reserved_day):
                    return str(reserved_day)
        attempt_day = _day_key(attempt.get("created_at"), budget_timezone)
        if attempt_day:
            return attempt_day
    turn_reservation = turn.get("budget_reservation")
    if isinstance(turn_reservation, dict):
        reserved_day = turn_reservation.get("budget_day")
        if _valid_day_key(reserved_day):
            return str(reserved_day)
    return _day_key(turn.get("created_at"), budget_timezone)


def _valid_day_key(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat() == value
    except ValueError:
        return False


def _safe_usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key in (
            "input_tokens",
            "output_tokens",
            "billable_output_tokens",
            "reasoning_tokens",
            "total_tokens",
            "cached_input_tokens",
            "cache_creation_input_tokens",
        )
        if isinstance((value := raw.get(key)), int)
        and not isinstance(value, bool)
        and value >= 0
    }


def _rate_decimal(value: Any) -> Decimal:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError
    parsed = Decimal(str(value))
    if not parsed.is_finite() or parsed < 0 or parsed > Decimal("1000000"):
        raise ValueError
    return parsed


def _parse_usd_limit(raw: str, name: str) -> int | None:
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise FinanceConfigurationError(f"{name}が不正です") from exc
    if not value.is_finite() or value <= 0 or value > Decimal("1000000000"):
        raise FinanceConfigurationError(f"{name}が不正です")
    return int((value * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING))


def _parse_utc_offset(raw: str) -> timezone:
    matched = re.fullmatch(r"([+-])(\d{2}):(\d{2})", raw)
    if not matched:
        raise FinanceConfigurationError("CLAGE_BUDGET_UTC_OFFSETが不正です")
    hours = int(matched.group(2))
    minutes = int(matched.group(3))
    if hours > 14 or minutes > 59 or (hours == 14 and minutes != 0):
        raise FinanceConfigurationError("CLAGE_BUDGET_UTC_OFFSETが不正です")
    total = hours * 60 + minutes
    if matched.group(1) == "-":
        total = -total
    return timezone(timedelta(minutes=total))


def _day_key(value: Any, target_timezone: timezone) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(target_timezone).date().isoformat()


def _format_micros(value: int | None) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value) / Decimal(1_000_000):.6f}"


def _public_reservation(raw: dict[str, Any]) -> dict[str, Any]:
    amount = raw.get("amount_micros")
    amount = amount if isinstance(amount, int) and amount >= 0 else None
    settled = raw.get("settled_amount_micros")
    settled = settled if isinstance(settled, int) and settled >= 0 else None
    return {
        "request_id": raw.get("request_id"),
        "state": raw.get("state"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "budget_day": raw.get("budget_day"),
        "amount_micros": amount,
        "amount_usd": _format_micros(amount),
        "settled_amount_micros": settled,
        "settled_amount_usd": _format_micros(settled),
        "settled_at": raw.get("settled_at"),
        "currency": "USD",
        "price_version": raw.get("price_version"),
        "reconciliation_reason": raw.get("reconciliation_reason"),
        "reconciliation_resolution": raw.get("reconciliation_resolution"),
        "reconciled_at": raw.get("reconciled_at"),
        "reconciliation_note": raw.get("reconciliation_note"),
    }


def _budget_message(code: str) -> str:
    return {
        "budget_cost_unknown": "金額を安全に見積もれないため実行を停止しました",
        "per_run_budget_exceeded": "会議の最大見積額が1回あたり予算を超えています",
        "daily_budget_exceeded": "本日の確定推定額と予約額が日次予算を超えます",
        "budget_reconciliation_backlog": "照合待ちの予算予約が上限に達したため実行を停止しました",
    }.get(code, "予算条件を満たさないため実行を停止しました")
