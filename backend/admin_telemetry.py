# -*- coding: utf-8 -*-
"""任意の管理資格情報から、読み取り専用の組織usage/costを取得する。

通常の推論キーとは完全に分離し、既定では外部通信を行わない。Providerの応答本文や
識別子は公開せず、UIに必要な集計値と失敗分類だけへ正規化する。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable

import httpx

import config
from storage import utc_now


_OPENAI_BASE = "https://api.openai.com/v1"
_ANTHROPIC_BASE = "https://api.anthropic.com/v1"
_XAI_BASE = "https://management-api.x.ai"
_MAX_PAGES = 32

_cache_guard = threading.RLock()
_cache: dict[str, Any] | None = None
_cache_deadline = 0.0
_cache_fingerprint: tuple[Any, ...] | None = None


def reset_cache() -> None:
    """設定変更・test用に、秘密値を保持しないmemory cacheを破棄する。"""
    global _cache, _cache_deadline, _cache_fingerprint
    with _cache_guard:
        _cache = None
        _cache_deadline = 0.0
        _cache_fingerprint = None


async def snapshot(
    *,
    now: datetime | None = None,
    client: httpx.AsyncClient | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """設定済みの管理APIだけを並列取得する。未設定時は外部通信しない。"""
    current = _utc_datetime(now)
    fingerprint = _configuration_fingerprint(current)
    use_cache = client is None and not force
    if use_cache:
        with _cache_guard:
            if (
                _cache is not None
                and _cache_fingerprint == fingerprint
                and time.monotonic() < _cache_deadline
            ):
                cached = deepcopy(_cache)
                cached["cache"] = {"hit": True, "ttl_seconds": config.ADMIN_TELEMETRY_CACHE_SEC}
                return cached

    if not config.ADMIN_TELEMETRY_ENABLED:
        result = _disabled_snapshot(current)
        return _remember(result, fingerprint) if use_cache else result

    start, end = _calendar_window(current)
    own_client = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(config.ADMIN_TELEMETRY_TIMEOUT_SEC),
        follow_redirects=False,
        headers={"User-Agent": "ClageCook/0.2 admin-telemetry"},
    )
    try:
        claude_task = _configured_task(
            "claude",
            config.ANTHROPIC_ADMIN_KEY,
            _anthropic_snapshot(http, start, end),
        )
        openai_task = _configured_task(
            "chatgpt",
            config.OPENAI_ADMIN_KEY,
            _openai_snapshot(http, start, end),
        )
        grok_task = _configured_task(
            "grok",
            config.XAI_MANAGEMENT_KEY,
            _xai_snapshot(http, start, end),
        )
        claude, chatgpt, grok = await asyncio.gather(
            claude_task, openai_task, grok_task
        )
    finally:
        if own_client:
            await http.aclose()

    result = {
        "schema_version": 1,
        "enabled": True,
        "generated_at": utc_now(),
        "window": {
            "starting_at": _iso(start),
            "ending_at": _iso(end),
            "lookback_days": config.ADMIN_TELEMETRY_LOOKBACK_DAYS,
            "utc_offset": config.BUDGET_UTC_OFFSET,
        },
        "cache": {"hit": False, "ttl_seconds": config.ADMIN_TELEMETRY_CACHE_SEC},
        "providers": [
            claude,
            chatgpt,
            _unsupported_gemini(),
            grok,
        ],
        "limitations": [
            "管理telemetryは明示有効化され、別管理資格情報があるProviderだけ取得します。",
            "Provider側の反映遅延、権限、plan、pagination上限により値が部分的な場合があります。",
            (
                "ClaudeのUsage/CostはProvider契約上UTC日次bucketです。"
                "開始境界は予算窓を包含するUTC日へ広げ、終了境界は現在時刻を超えません。"
                "完全なbucketの最終境界はProvider別window.complete_throughに示します。"
            ),
            "取得処理は読み取り専用で、top-up・支払方法・spend limitを変更しません。",
            "Gemini Developer APIのcredit残高と集計usageはAI Studioで確認してください。",
        ],
    }
    return _remember(result, fingerprint) if use_cache else result


async def _configured_task(
    name: str,
    credential: str,
    operation: Awaitable[dict[str, Any]],
) -> dict[str, Any]:
    if not credential:
        operation.close()  # coroutineを未awaitのまま残さない。
        return _provider_base(name, configured=False, status="not_configured")
    try:
        return await operation
    except Exception as exc:  # Provider本文やcredentialを外へ反射しない。
        result = _provider_base(name, configured=True, status="error")
        result["error_code"] = _error_code(exc)
        return result


async def _anthropic_snapshot(
    client: httpx.AsyncClient,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    query_start, query_end, complete_through = _anthropic_utc_day_window(start, end)
    headers = {
        "x-api-key": config.ANTHROPIC_ADMIN_KEY,
        "anthropic-version": "2023-06-01",
    }
    query_days = max(
        1,
        (complete_through - query_start).days
        + int(query_end > complete_through),
    )
    params = {
        "starting_at": _iso(query_start),
        "ending_at": _iso(query_end),
        "bucket_width": "1d",
        "limit": min(query_days, 31),
    }
    usage_task = _capture(
        _paged_get(
            client,
            f"{_ANTHROPIC_BASE}/organizations/usage_report/messages",
            headers=headers,
            params=params,
        )
    )
    cost_task = _capture(
        _paged_get(
            client,
            f"{_ANTHROPIC_BASE}/organizations/cost_report",
            headers=headers,
            params={
                "starting_at": _iso(query_start),
                "ending_at": _iso(query_end),
                "bucket_width": "1d",
                "limit": min(query_days, 31),
            },
        )
    )
    usage_result, cost_result = await asyncio.gather(usage_task, cost_task)
    usage = _section(usage_result, _normalize_anthropic_usage)
    cost = _section(cost_result, _normalize_anthropic_cost)
    result = _provider_base(
        "claude",
        configured=True,
        status=_combined_status(usage, cost),
    )
    result.update(
        {
            "source": "organization_admin_api",
            "window": _provider_window(
                query_start,
                query_end,
                requested_start=start,
                requested_end=end,
                alignment="provider_utc_day_buckets",
                bucket_width="1d",
                exact_budget_window=False,
                complete_through=complete_through,
            ),
            "usage": usage,
            "cost": cost,
        }
    )
    return result


async def _openai_snapshot(
    client: httpx.AsyncClient,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {config.OPENAI_ADMIN_KEY}"}
    base_params = {
        "start_time": int(start.timestamp()),
        "end_time": int(end.timestamp()),
        "bucket_width": "1d",
        "limit": min(config.ADMIN_TELEMETRY_LOOKBACK_DAYS + 1, 31),
    }
    usage_task = _capture(
        _paged_get(
            client,
            f"{_OPENAI_BASE}/organization/usage/completions",
            headers=headers,
            params=base_params,
        )
    )
    cost_task = _capture(
        _paged_get(
            client,
            f"{_OPENAI_BASE}/organization/costs",
            headers=headers,
            params=base_params,
        )
    )
    usage_result, cost_result = await asyncio.gather(usage_task, cost_task)
    usage = _section(usage_result, _normalize_openai_usage)
    cost = _section(cost_result, _normalize_openai_cost)
    result = _provider_base(
        "chatgpt",
        configured=True,
        status=_combined_status(usage, cost),
    )
    result.update(
        {
            "source": "organization_admin_api",
            "window": _provider_window(
                start,
                end,
                requested_start=start,
                requested_end=end,
                alignment="requested_budget_window",
                bucket_width="1d",
                exact_budget_window=True,
            ),
            "usage": usage,
            "cost": cost,
        }
    )
    return result


async def _xai_snapshot(
    client: httpx.AsyncClient,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {config.XAI_MANAGEMENT_KEY}"}
    team_id = config.XAI_TEAM_ID
    team_lookup: dict[str, Any] = {"status": "ok", "source": "configured"}
    if not team_id:
        validation = await _capture(
            _get_json(
                client,
                f"{_XAI_BASE}/auth/management-keys/validation",
                headers=headers,
            )
        )
        if validation[1] is not None:
            result = _provider_base("grok", configured=True, status="error")
            result.update(
                {
                    "source": "team_management_api",
                    "error_code": validation[1],
                    "team_lookup": {"status": "error", "error_code": validation[1]},
                }
            )
            return result
        raw_validation = validation[0] or {}
        candidate = raw_validation.get("teamId")
        if not candidate and raw_validation.get("scope") == "SCOPE_TEAM":
            candidate = raw_validation.get("scopeId")
        team_id = str(candidate or "").strip()
        team_lookup = {"status": "ok", "source": "management_key_validation"}
    if not _valid_team_id(team_id):
        result = _provider_base("grok", configured=True, status="error")
        result.update(
            {
                "source": "team_management_api",
                "error_code": "team_id_required",
                "team_lookup": {"status": "error", "error_code": "team_id_required"},
            }
        )
        return result

    team_base = f"{_XAI_BASE}/v1/billing/teams/{team_id}"
    usage_payload = {
        "analyticsRequest": {
            "timeRange": {
                "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
                "endTime": end.strftime("%Y-%m-%d %H:%M:%S"),
                "timezone": "Etc/GMT",
            },
            "timeUnit": "TIME_UNIT_DAY",
            "values": [{"name": "usd", "aggregation": "AGGREGATION_SUM"}],
            "groupBy": ["description"],
            "filters": [],
        }
    }
    usage_result, balance_result, preview_result, limits_result = await asyncio.gather(
        _capture(
            _post_json(
                client,
                f"{team_base}/usage",
                headers=headers,
                payload=usage_payload,
            )
        ),
        _capture(
            _get_json(client, f"{team_base}/prepaid/balance", headers=headers)
        ),
        _capture(
            _get_json(
                client,
                f"{team_base}/postpaid/invoice/preview",
                headers=headers,
            )
        ),
        _capture(
            _get_json(
                client,
                f"{team_base}/postpaid/spending-limits",
                headers=headers,
            )
        ),
    )
    usage = _section(usage_result, _normalize_xai_usage)
    balance = _section(balance_result, _normalize_xai_balance)
    preview = _section(preview_result, _normalize_xai_preview)
    limits = _section(limits_result, _normalize_xai_limits)
    result = _provider_base(
        "grok",
        configured=True,
        status=_combined_status(usage, balance, preview, limits),
    )
    result.update(
        {
            "source": "team_management_api",
            "window": _provider_window(
                start,
                end,
                requested_start=start,
                requested_end=end,
                alignment="requested_budget_window",
                bucket_width="1d",
                exact_budget_window=True,
            ),
            "team_lookup": team_lookup,
            "usage": usage,
            "credit_balance": balance,
            "current_billing_period": preview,
            "spending_limits": limits,
        }
    )
    return result


async def _paged_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    page_params = dict(params)
    for _ in range(_MAX_PAGES):
        payload = await _get_json(client, url, headers=headers, params=page_params)
        pages.append(payload)
        if payload.get("has_more") is not True:
            break
        cursor = payload.get("next_page")
        if not isinstance(cursor, str) or not cursor:
            raise ValueError("invalid_pagination")
        page_params["page"] = cursor
    else:
        raise ValueError("pagination_limit")
    return pages


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.get(url, headers=headers, params=params)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("invalid_response") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_response")
    return payload


async def _post_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = await client.post(url, headers=headers, json=payload)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise ValueError("invalid_response") from exc
    if not isinstance(data, dict):
        raise ValueError("invalid_response")
    return data


async def _capture(
    operation: Awaitable[Any],
) -> tuple[Any | None, str | None]:
    try:
        return await operation, None
    except Exception as exc:
        return None, _error_code(exc)


def _section(
    captured: tuple[Any | None, str | None],
    normalizer: Any,
) -> dict[str, Any]:
    payload, error = captured
    if error:
        return {"status": "error", "error_code": error}
    try:
        normalized = normalizer(payload)
    except Exception:
        return {"status": "error", "error_code": "invalid_response"}
    return {"status": "ok", **normalized}


def _normalize_anthropic_usage(pages: list[dict[str, Any]]) -> dict[str, Any]:
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "web_search_requests": 0,
    }
    for result in _page_results(pages):
        uncached = _nonnegative_int(result.get("uncached_input_tokens"))
        cached = _nonnegative_int(result.get("cache_read_input_tokens"))
        cache_creation = result.get("cache_creation")
        creation = 0
        if isinstance(cache_creation, dict):
            creation = sum(_nonnegative_int(value) for value in cache_creation.values())
        usage["input_tokens"] += uncached + cached + creation
        usage["output_tokens"] += _nonnegative_int(result.get("output_tokens"))
        usage["cached_input_tokens"] += cached
        usage["cache_creation_input_tokens"] += creation
        server_tools = result.get("server_tool_use")
        if isinstance(server_tools, dict):
            usage["web_search_requests"] += _nonnegative_int(
                server_tools.get("web_search_requests")
            )
    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return {"usage": usage}


def _normalize_anthropic_cost(pages: list[dict[str, Any]]) -> dict[str, Any]:
    cents = sum(
        (_decimal(result.get("amount")) for result in _page_results(pages)),
        Decimal(0),
    )
    return {
        "amount_usd": _decimal_text(cents / Decimal(100)),
        "currency": "USD",
        "provider_units": "fractional_cents",
    }


def _normalize_openai_usage(pages: list[dict[str, Any]]) -> dict[str, Any]:
    keys = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "input_cached_tokens": "cached_input_tokens",
        "num_model_requests": "requests",
        "input_audio_tokens": "input_audio_tokens",
        "output_audio_tokens": "output_audio_tokens",
    }
    usage: dict[str, int] = {target: 0 for target in keys.values()}
    for result in _page_results(pages):
        for source, target in keys.items():
            usage[target] += _nonnegative_int(result.get(source))
    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return {"usage": usage}


def _normalize_openai_cost(pages: list[dict[str, Any]]) -> dict[str, Any]:
    total = Decimal(0)
    currency = "USD"
    for result in _page_results(pages):
        amount = result.get("amount")
        if not isinstance(amount, dict):
            continue
        total += _decimal(amount.get("value"))
        candidate = amount.get("currency")
        if isinstance(candidate, str) and candidate:
            currency = candidate.upper()
    return {"amount_usd": _decimal_text(total), "currency": currency}


def _normalize_xai_usage(payload: dict[str, Any]) -> dict[str, Any]:
    total = Decimal(0)
    for series in payload.get("timeSeries") or []:
        if not isinstance(series, dict):
            continue
        for point in series.get("dataPoints") or []:
            if not isinstance(point, dict):
                continue
            values = point.get("values")
            if isinstance(values, list) and values:
                total += _decimal(values[0])
    return {
        "amount_usd": _decimal_text(total),
        "currency": "USD",
        "limit_reached": payload.get("limitReached") is True,
        "metric": "usd",
    }


def _normalize_xai_balance(payload: dict[str, Any]) -> dict[str, Any]:
    total = payload.get("total")
    raw = total.get("val") if isinstance(total, dict) else None
    return {
        "provider_reported_usd": _minor_units_usd(raw),
        "currency": "USD",
        "sign_convention": "provider_reported",
    }


def _normalize_xai_preview(payload: dict[str, Any]) -> dict[str, Any]:
    core = payload.get("coreInvoice")
    core = core if isinstance(core, dict) else {}
    total = core.get("totalWithCorr")
    total_raw = total.get("val") if isinstance(total, dict) else None
    cycle = payload.get("billingCycle")
    cycle = cycle if isinstance(cycle, dict) else {}
    return {
        "estimated_invoice_usd": _minor_units_usd(total_raw),
        "effective_spending_limit_usd": _minor_units_usd(
            payload.get("effectiveSpendingLimit")
        ),
        "billing_cycle": {
            "year": _nonnegative_int(cycle.get("year")),
            "month": _nonnegative_int(cycle.get("month")),
        },
    }


def _normalize_xai_limits(payload: dict[str, Any]) -> dict[str, Any]:
    limits = payload.get("spendingLimits")
    limits = limits if isinstance(limits, dict) else {}
    return {
        "effective_soft_usd": _nested_minor_units(limits.get("effectiveSl")),
        "effective_hard_usd": _nested_minor_units(limits.get("effectiveHardSl")),
    }


def _page_results(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for page in pages:
        for bucket in page.get("data") or []:
            if not isinstance(bucket, dict):
                continue
            for result in bucket.get("results") or []:
                if isinstance(result, dict):
                    results.append(result)
    return results


def _combined_status(*sections: dict[str, Any]) -> str:
    statuses = {section.get("status") for section in sections}
    if statuses == {"ok"}:
        return "ok"
    if "ok" in statuses:
        return "partial"
    return "error"


def _provider_base(name: str, *, configured: bool, status: str) -> dict[str, Any]:
    return {
        "name": name,
        "label": config.LABELS[name],
        "supported": name != "gemini",
        "configured": configured,
        "status": status,
    }


def _unsupported_gemini() -> dict[str, Any]:
    result = _provider_base("gemini", configured=False, status="unsupported")
    result.update(
        {
            "source": "ai_studio_portal",
            "reason": "developer_api_has_no_api_key_usage_credit_endpoint",
        }
    )
    return result


def _disabled_snapshot(now: datetime) -> dict[str, Any]:
    providers = []
    for name in config.WORKERS:
        if name == "gemini":
            providers.append(_unsupported_gemini())
            continue
        configured = bool(
            {
                "claude": config.ANTHROPIC_ADMIN_KEY,
                "chatgpt": config.OPENAI_ADMIN_KEY,
                "grok": config.XAI_MANAGEMENT_KEY,
            }[name]
        )
        providers.append(
            _provider_base(name, configured=configured, status="disabled")
        )
    return {
        "schema_version": 1,
        "enabled": False,
        "generated_at": _iso(now),
        "window": {
            "lookback_days": config.ADMIN_TELEMETRY_LOOKBACK_DAYS,
            "utc_offset": config.BUDGET_UTC_OFFSET,
        },
        "cache": {"hit": False, "ttl_seconds": config.ADMIN_TELEMETRY_CACHE_SEC},
        "providers": providers,
        "limitations": [
            "CLAGE_ADMIN_TELEMETRY_ENABLED=falseのため管理APIへの外部通信は行っていません。"
        ],
    }


def _error_code(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return "unauthorized"
        if status == 403:
            return "forbidden"
        if status == 404:
            return "not_available"
        if status == 429:
            return "rate_limited"
        if 400 <= status < 500:
            return "request_rejected"
        return "upstream_error"
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return "network_error"
    if isinstance(exc, ValueError) and str(exc) in {
        "invalid_pagination",
        "pagination_limit",
        "invalid_response",
    }:
        return str(exc)
    return "unexpected_error"


def _configuration_fingerprint(now: datetime | None = None) -> tuple[Any, ...]:
    current = _utc_datetime(now)
    budget_window_start, _ = _calendar_window(current)
    return (
        config.ADMIN_TELEMETRY_ENABLED,
        _secret_fingerprint(config.ANTHROPIC_ADMIN_KEY),
        _secret_fingerprint(config.OPENAI_ADMIN_KEY),
        _secret_fingerprint(config.XAI_MANAGEMENT_KEY),
        config.XAI_TEAM_ID,
        config.ADMIN_TELEMETRY_LOOKBACK_DAYS,
        config.ADMIN_TELEMETRY_CACHE_SEC,
        config.BUDGET_UTC_OFFSET,
        _iso(budget_window_start),
    )


def _secret_fingerprint(value: str) -> str:
    """資格情報rotationで別組織のcacheを再利用しない非公開識別子。"""
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _remember(result: dict[str, Any], fingerprint: tuple[Any, ...]) -> dict[str, Any]:
    global _cache, _cache_deadline, _cache_fingerprint
    with _cache_guard:
        _cache = deepcopy(result)
        _cache_deadline = time.monotonic() + config.ADMIN_TELEMETRY_CACHE_SEC
        _cache_fingerprint = fingerprint
    return result


def _utc_datetime(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _calendar_window(current: datetime) -> tuple[datetime, datetime]:
    """予算日と同じ固定offsetで、今日を含むcalendar-day窓を作る。"""
    budget_timezone = _budget_timezone()
    local = current.astimezone(budget_timezone)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_local -= timedelta(days=max(0, config.ADMIN_TELEMETRY_LOOKBACK_DAYS - 1))
    return start_local.astimezone(timezone.utc), current.astimezone(timezone.utc)


def _anthropic_utc_day_window(
    requested_start: datetime,
    requested_end: datetime,
) -> tuple[datetime, datetime, datetime]:
    """Anthropicの1d bucket契約へ合わせ、開始だけをUTC日境界へ広げる。

    Usageの日次bucketはUTC日境界へsnapされ、Costは日次のみである。任意offsetの
    予算日と一致するように見せず、問い合わせ終了は現在時刻のまま未来へ広げない。
    併せて、完全に閉じた最後のUTC bucket境界を返す。
    """
    start = requested_start.astimezone(timezone.utc).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    end = requested_end.astimezone(timezone.utc)
    complete_through = end.replace(hour=0, minute=0, second=0, microsecond=0)
    if end <= start:
        start = complete_through - timedelta(days=1)
    return start, end, complete_through


def _provider_window(
    start: datetime,
    end: datetime,
    *,
    requested_start: datetime,
    requested_end: datetime,
    alignment: str,
    bucket_width: str,
    exact_budget_window: bool,
    complete_through: datetime | None = None,
) -> dict[str, Any]:
    result = {
        "starting_at": _iso(start),
        "ending_at": _iso(end),
        "requested_starting_at": _iso(requested_start),
        "requested_ending_at": _iso(requested_end),
        "query_utc_offset": "+00:00",
        "budget_utc_offset": config.BUDGET_UTC_OFFSET,
        "alignment": alignment,
        "bucket_width": bucket_width,
        "exact_budget_window": exact_budget_window,
    }
    if complete_through is not None:
        result["complete_through"] = _iso(complete_through)
    return result


def _budget_timezone() -> timezone:
    raw = config.BUDGET_UTC_OFFSET
    matched = re.fullmatch(r"([+-])(\d{2}):(\d{2})", raw)
    if not matched:
        raise ValueError("invalid_budget_utc_offset")
    hours = int(matched.group(2))
    minutes = int(matched.group(3))
    if hours > 14 or minutes > 59 or (hours == 14 and minutes != 0):
        raise ValueError("invalid_budget_utc_offset")
    total = hours * 60 + minutes
    if matched.group(1) == "-":
        total = -total
    return timezone(timedelta(minutes=total))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(parsed, 0)


def _decimal(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        return Decimal(0)
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)
    return parsed if parsed.is_finite() else Decimal(0)


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    text = text.rstrip("0").rstrip(".") if "." in text else text
    return text or "0"


def _minor_units_usd(value: Any) -> str | None:
    if value is None:
        return None
    return _decimal_text(_decimal(value) / Decimal(100))


def _nested_minor_units(value: Any) -> str | None:
    return _minor_units_usd(value.get("val")) if isinstance(value, dict) else None


def _valid_team_id(value: str) -> bool:
    return 8 <= len(value) <= 128 and all(
        char.isascii() and (char.isalnum() or char in "-_") for char in value
    )
