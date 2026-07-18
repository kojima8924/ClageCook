# -*- coding: utf-8 -*-
"""明示価格表による推定と、単一process向けのdurable予算予約。"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any, Iterable

import config
from storage import ConversationNotFound, ConversationStore, utc_now


_RATE_FIELDS = (
    "input_per_million_usd",
    "output_per_million_usd",
    "cached_input_per_million_usd",
    "cache_write_per_million_usd",
)
_ACTIVE_RESERVATION_STATES = {"reserved", "reconciliation_pending"}


class FinanceConfigurationError(RuntimeError):
    pass


class BudgetViolation(RuntimeError):
    def __init__(self, code: str, message: str, snapshot: dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.snapshot = snapshot


class ReconciliationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PriceCatalog:
    def __init__(self, path: str) -> None:
        self.path = path
        self.version = ""
        self.currency = "USD"
        self._prices: dict[tuple[str, str], dict[str, Decimal]] = {}
        self.error_code: str | None = None
        if path:
            self._load(Path(path).expanduser())

    @property
    def loaded(self) -> bool:
        return bool(self.version and self._prices and self.error_code is None)

    def public_status(self) -> dict[str, Any]:
        return {
            "configured": bool(self.path),
            "loaded": self.loaded,
            "version": self.version or None,
            "currency": self.currency,
            "model_count": len(self._prices),
            "error_code": self.error_code,
            "source": "user_configured_file" if self.path else "not_configured",
        }

    def price_for(self, provider: str, model: str) -> dict[str, Decimal] | None:
        price = self._prices.get((provider, model))
        return dict(price) if price is not None else None

    def _load(self, path: Path) -> None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.error_code = "price_table_not_found"
            return
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self.error_code = "price_table_invalid"
            return
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            self.error_code = "price_table_invalid"
            return
        version = raw.get("version")
        currency = raw.get("currency")
        models = raw.get("models")
        if (
            not isinstance(version, str)
            or not 1 <= len(version.strip()) <= 80
            or currency != "USD"
            or not isinstance(models, dict)
        ):
            self.error_code = "price_table_invalid"
            return
        parsed: dict[tuple[str, str], dict[str, Decimal]] = {}
        try:
            for provider, provider_models in models.items():
                if provider not in config.WORKERS or not isinstance(
                    provider_models, dict
                ):
                    raise ValueError
                for model, raw_price in provider_models.items():
                    if (
                        not isinstance(model, str)
                        or not 1 <= len(model.strip()) <= 160
                        or not isinstance(raw_price, dict)
                    ):
                        raise ValueError
                    price: dict[str, Decimal] = {}
                    for field in _RATE_FIELDS:
                        if field not in raw_price:
                            continue
                        price[field] = _rate_decimal(raw_price[field])
                    if not price:
                        raise ValueError
                    parsed[(provider, model.strip())] = price
        except (ValueError, InvalidOperation):
            self.error_code = "price_table_invalid"
            return
        if not parsed:
            self.error_code = "price_table_empty"
            return
        self.version = version.strip()
        self._prices = parsed


class BudgetGuard:
    """予算のcheck-and-reserveを同じlockとatomic file更新で行う。"""

    def __init__(self, data_dir: Path) -> None:
        self.catalog = PriceCatalog(config.PRICE_TABLE_FILE)
        self.per_run_limit_micros = _parse_usd_limit(
            config.PER_RUN_BUDGET_USD,
            "CLAGE_PER_RUN_BUDGET_USD",
        )
        self.daily_limit_micros = _parse_usd_limit(
            config.DAILY_BUDGET_USD,
            "CLAGE_DAILY_BUDGET_USD",
        )
        self.unknown_policy = config.BUDGET_UNKNOWN_POLICY
        self.budget_timezone = _parse_utc_offset(config.BUDGET_UTC_OFFSET)
        self._control_dir = Path(data_dir).resolve() / ".control"
        self._ledger_path = self._control_dir / "budget-reservations.json"
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return self.per_run_limit_micros is not None or self.daily_limit_micros is not None

    def validate_configuration(self) -> None:
        if self.unknown_policy == "invalid":
            raise FinanceConfigurationError(
                "CLAGE_BUDGET_UNKNOWN_POLICYはblockまたはallowを指定してください"
            )
        if self.enabled and not self.catalog.loaded:
            raise FinanceConfigurationError(
                "予算上限を使うには有効なCLAGE_PRICE_TABLE_FILEが必要です"
            )

    def recover_orphaned_reservations(self) -> int:
        """前processがsettleできなかった予約を、安全側の照合待ちへ昇格する。"""
        if not self.enabled:
            return 0
        with self._lock:
            ledger = self._read_ledger()
            recovered = 0
            for reservation in ledger["reservations"].values():
                if not isinstance(reservation, dict) or reservation.get("state") != "reserved":
                    continue
                reservation["state"] = "reconciliation_pending"
                reservation["reconciliation_reason"] = "server_restarted_before_settlement"
                reservation["updated_at"] = utc_now()
                recovered += 1
            if recovered:
                self._write_ledger(ledger)
            return recovered

    def decorate_plan(
        self,
        plan: dict[str, Any],
        store: ConversationStore,
    ) -> dict[str, Any]:
        with self._lock:
            estimate = estimate_plan_cost(plan, self.catalog)
            snapshot = self._snapshot_locked(store)
            decision = self._decision(estimate, snapshot)
        result = deepcopy(plan)
        result["cost_estimate"] = estimate
        result["budget"] = decision
        if not decision["allowed"]:
            result["allowed"] = False
            result.setdefault("block_reasons", []).extend(
                code
                for code in decision["block_reasons"]
                if code not in result["block_reasons"]
            )
        result.setdefault("warnings", []).extend(decision["warnings"])
        return result

    def reserve(
        self,
        *,
        request_id: str,
        request_fingerprint: str,
        plan: dict[str, Any],
        store: ConversationStore,
    ) -> dict[str, Any] | None:
        if not self.enabled or not plan.get("billable"):
            return None
        with self._lock:
            ledger = self._read_ledger()
            existing = ledger["reservations"].get(request_id)
            if isinstance(existing, dict):
                if existing.get("request_fingerprint") != request_fingerprint:
                    raise BudgetViolation(
                        "budget_reservation_conflict",
                        "request_idが異なる予算予約で使用済みです",
                        self._snapshot_locked(store, ledger=ledger),
                    )
                return _public_reservation(existing)
            estimate = estimate_plan_cost(plan, self.catalog)
            snapshot = self._snapshot_locked(store, ledger=ledger)
            decision = self._decision(estimate, snapshot)
            if not decision["allowed"]:
                code = decision["block_reasons"][0]
                raise BudgetViolation(
                    code,
                    _budget_message(code),
                    decision,
                )
            amount = estimate.get("total_micros")
            if not isinstance(amount, int):
                amount = 0
            reservation = {
                "request_id": request_id,
                "request_fingerprint": request_fingerprint,
                "state": "reserved",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "budget_day": self._today_key(),
                "amount_micros": amount,
                "currency": "USD",
                "price_version": self.catalog.version,
                "reserved_by_pid": os.getpid(),
                "estimate": estimate,
            }
            ledger["reservations"][request_id] = reservation
            self._write_ledger(ledger)
            return _public_reservation(reservation)

    def settle(self, request_id: str, *, usage_reconciled: bool) -> None:
        if not self.enabled:
            return
        with self._lock:
            ledger = self._read_ledger()
            reservation = ledger["reservations"].get(request_id)
            if not isinstance(reservation, dict):
                return
            reservation["state"] = (
                "settled" if usage_reconciled else "reconciliation_pending"
            )
            reservation["updated_at"] = utc_now()
            self._write_ledger(ledger)

    def release_undispatched(self, request_id: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            ledger = self._read_ledger()
            reservation = ledger["reservations"].get(request_id)
            if not isinstance(reservation, dict) or reservation.get("state") != "reserved":
                return
            reservation["state"] = "released_before_dispatch"
            reservation["updated_at"] = utc_now()
            self._write_ledger(ledger)

    def release_after_manual_reconciliation(
        self,
        request_id: str,
        *,
        confirmed_no_unobserved_charge: bool,
        note: str = "",
    ) -> dict[str, Any]:
        """Provider側を確認済みの場合だけ、照合待ち予約を解除する。"""
        if not confirmed_no_unobserved_charge:
            raise ReconciliationError(
                "reconciliation_confirmation_required",
                "未観測の追加請求がないことをProvider側で確認してください",
            )
        with self._lock:
            ledger = self._read_ledger()
            reservation = ledger["reservations"].get(request_id)
            if not isinstance(reservation, dict):
                raise ReconciliationError(
                    "reservation_not_found",
                    "対象の予算予約が見つかりません",
                )
            state = reservation.get("state")
            if state != "reconciliation_pending":
                raise ReconciliationError(
                    "reservation_not_reconcilable",
                    "照合待ち状態の予約だけを手動解除できます",
                )
            now = utc_now()
            reservation["state"] = "released_after_manual_reconciliation"
            reservation["reconciled_at"] = now
            reservation["updated_at"] = now
            reservation["reconciliation_resolution"] = (
                "provider_review_confirmed_no_unobserved_charge"
            )
            if note:
                reservation["reconciliation_note"] = note[:200]
            self._write_ledger(ledger)
            return _public_reservation(reservation)

    def public_snapshot(self, store: ConversationStore) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked(store)

    def _decision(
        self,
        estimate: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        block_reasons: list[str] = []
        warnings: list[dict[str, str]] = []
        total = estimate.get("total_micros")
        complete = estimate.get("complete") is True
        if self.enabled and not complete:
            warning = {
                "code": "cost_estimate_incomplete",
                "message": "価格表にないmodelまたは課金区分があるため、金額見積りは不完全です。",
            }
            warnings.append(warning)
            if self.unknown_policy == "block":
                block_reasons.append("budget_cost_unknown")
        if isinstance(total, int):
            if (
                self.per_run_limit_micros is not None
                and total > self.per_run_limit_micros
            ):
                block_reasons.append("per_run_budget_exceeded")
            committed = snapshot["today"]["committed_micros"]
            if (
                self.daily_limit_micros is not None
                and committed + total > self.daily_limit_micros
            ):
                block_reasons.append("daily_budget_exceeded")
        backlog = snapshot.get("reconciliation_backlog") or {}
        if (
            self.enabled
            and isinstance(backlog.get("count"), int)
            and backlog["count"] >= config.MAX_UNRECONCILED_RESERVATIONS
        ):
            block_reasons.append("budget_reconciliation_backlog")
        return {
            **snapshot,
            "allowed": not block_reasons,
            "block_reasons": block_reasons,
            "warnings": warnings,
            "run_estimate_micros": total,
            "run_estimate_usd": _format_micros(total),
        }

    def _snapshot_locked(
        self,
        store: ConversationStore,
        *,
        ledger: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        active_ledger = ledger or self._read_ledger()
        budget_day = self._today_key()
        actual = actual_cost_snapshot(
            store,
            self.catalog,
            budget_day=budget_day,
            budget_timezone=self.budget_timezone,
        )
        outstanding = [
            value
            for value in active_ledger["reservations"].values()
            if isinstance(value, dict)
            and value.get("state") in _ACTIVE_RESERVATION_STATES
        ]
        active = [
            value for value in outstanding if value.get("budget_day") == budget_day
        ]
        reserved = sum(
            value.get("amount_micros", 0)
            for value in active
            if isinstance(value.get("amount_micros"), int)
        )
        actual_micros = actual["total_micros"]
        committed = actual_micros + reserved
        remaining = (
            None
            if self.daily_limit_micros is None
            else max(0, self.daily_limit_micros - committed)
        )
        return {
            "configured": self.enabled,
            "currency": "USD",
            "unknown_cost_policy": self.unknown_policy,
            "price_table": self.catalog.public_status(),
            "limits": {
                "per_run_micros": self.per_run_limit_micros,
                "per_run_usd": _format_micros(self.per_run_limit_micros),
                "daily_micros": self.daily_limit_micros,
                "daily_usd": _format_micros(self.daily_limit_micros),
            },
            "today": {
                "day": budget_day,
                "utc_offset": config.BUDGET_UTC_OFFSET,
                "actual_estimated_micros": actual_micros,
                "actual_estimated_usd": _format_micros(actual_micros),
                "active_reservations_micros": reserved,
                "active_reservations_usd": _format_micros(reserved),
                "committed_micros": committed,
                "committed_usd": _format_micros(committed),
                "remaining_micros": remaining,
                "remaining_usd": _format_micros(remaining),
                "unpriced_requests": actual["unpriced_requests"],
            },
            "active_reservations": [
                _public_reservation(item)
                for item in sorted(active, key=lambda value: value.get("created_at", ""))
            ],
            "reconciliation_backlog": {
                "count": len(outstanding),
                "amount_micros": sum(
                    value.get("amount_micros", 0)
                    for value in outstanding
                    if isinstance(value.get("amount_micros"), int)
                ),
                "amount_usd": _format_micros(
                    sum(
                        value.get("amount_micros", 0)
                        for value in outstanding
                        if isinstance(value.get("amount_micros"), int)
                    )
                ),
                "oldest_created_at": min(
                    (
                        str(value.get("created_at"))
                        for value in outstanding
                        if value.get("created_at")
                    ),
                    default=None,
                ),
                "max_count": config.MAX_UNRECONCILED_RESERVATIONS,
                "blocks_new_runs_at_limit": True,
            },
            "disclaimer": (
                "金額は利用者設定の価格表と保存済みusageによる推定で、Provider請求書や"
                "credit残高ではありません。"
            ),
        }

    def _today_key(self) -> str:
        return datetime.now(timezone.utc).astimezone(self.budget_timezone).date().isoformat()

    def _read_ledger(self) -> dict[str, Any]:
        try:
            raw = json.loads(self._ledger_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"schema_version": 1, "reservations": {}}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise FinanceConfigurationError("予算予約台帳を安全に読み取れません")
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != 1
            or not isinstance(raw.get("reservations"), dict)
        ):
            raise FinanceConfigurationError("予算予約台帳の形式が不正です")
        return raw

    def _write_ledger(self, ledger: dict[str, Any]) -> None:
        self._control_dir.mkdir(parents=True, exist_ok=True)
        temp = self._control_dir / f"budget-{uuid.uuid4().hex}.tmp"
        payload = json.dumps(ledger, ensure_ascii=False, indent=2) + "\n"
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self._ledger_path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


def estimate_plan_cost(plan: dict[str, Any], catalog: PriceCatalog) -> dict[str, Any]:
    if not plan.get("billable"):
        return {
            "available": True,
            "complete": True,
            "currency": "USD",
            "total_micros": 0,
            "total_usd": "0.000000",
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
            "price_version": None,
            "items": [],
            "missing": ["price_table"],
            "method": "configured_price_table",
        }
    envelope = plan.get("input_envelope") or {}
    output = plan.get("max_output_tokens") or {}
    attempts = int((plan.get("retry_envelope") or {}).get("configured_retries_per_live_call", 0)) + 1
    per_call_output = int(output.get("per_call") or 0)
    debate_effective = bool((plan.get("options") or {}).get("debate_effective"))
    items: list[dict[str, Any]] = []
    for provider in plan.get("providers") or []:
        if not isinstance(provider, dict) or not provider.get("billable"):
            continue
        input_tokens = int(envelope.get("answer_per_call") or 0)
        if debate_effective:
            input_tokens += int(envelope.get("debate_per_call") or 0)
        calls = int(provider.get("max_calls") or 0)
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
    complete = bool(items) and all(item["complete"] for item in items)
    total = sum(item.get("micros") or 0 for item in items) if complete else None
    return {
        "available": complete,
        "complete": complete,
        "currency": "USD",
        "total_micros": total,
        "total_usd": _format_micros(total),
        "price_version": catalog.version,
        "items": items,
        "missing": sorted(
            {
                missing
                for item in items
                for missing in item.get("missing", [])
            }
        ),
        "method": "utf8_bytes_as_conservative_input_token_upper_bound",
        "includes_retry_envelope": True,
        "disclaimer": "価格表は利用者設定値です。実際の請求額やcredit残高ではありません。",
    }


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
    for summary in store.list():
        try:
            conversation = store.load(str(summary.get("id") or ""))
        except ConversationNotFound:
            continue
        for turn in conversation.get("turns") or []:
            if not isinstance(turn, dict) or _day_key(turn.get("created_at"), budget_timezone) != budget_day:
                continue
            for provider, entry in _billing_entries(turn):
                result = estimate_usage_cost(
                    catalog,
                    provider,
                    str(entry.get("model") or ""),
                    entry.get("usage"),
                )
                if not result["complete"]:
                    unpriced += 1
                    continue
                value = int(result["micros"])
                total += value
                by_provider[provider] = by_provider.get(provider, 0) + value
    return {
        "total_micros": total,
        "total_usd": _format_micros(total),
        "unpriced_requests": unpriced,
        "by_provider_micros": by_provider,
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
    output_tokens = usage.get("output_tokens")
    cache_write = usage.get("cache_creation_input_tokens", 0)
    if input_tokens is None or output_tokens is None:
        return {"complete": False, "micros": None, "missing": ["usage_tokens"]}
    uncached_input = input_tokens if provider == "claude" else max(0, input_tokens - cached)
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
    missing: list[str] = []
    if price is None:
        missing.append(f"{provider}:{model}")
    else:
        for field, tokens in (
            ("input_per_million_usd", input_tokens),
            ("output_per_million_usd", output_tokens),
        ):
            if tokens > 0 and field not in price:
                missing.append(field)
    complete = price is not None and not missing
    micros = (
        _cost_micros(
            (
                ("input_per_million_usd", input_tokens),
                ("output_per_million_usd", output_tokens),
            ),
            price or {},
        )
        if complete
        else None
    )
    return {
        "provider": provider,
        "model": model,
        "phase": phase,
        "input_token_upper_bound": input_tokens,
        "output_token_upper_bound": output_tokens,
        "complete": complete,
        "micros": micros,
        "usd": _format_micros(micros),
        "missing": missing,
        "price_snapshot": (
            {key: str(value) for key, value in (price or {}).items()}
            if complete
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


def _billing_entries(turn: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
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
                yield source, result

    answers = turn.get("answers")
    if isinstance(answers, dict):
        for provider, entry in answers.items():
            if (
                provider in config.WORKERS
                and isinstance(entry, dict)
                and f"answer:{provider}" not in represented
            ):
                yield provider, entry
    synthesis = turn.get("synthesis")
    if isinstance(synthesis, dict) and "synthesis" not in represented:
        provider = synthesis.get("source")
        if provider in config.WORKERS:
            yield str(provider), synthesis


def _safe_usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key in (
            "input_tokens",
            "output_tokens",
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
    return {
        "request_id": raw.get("request_id"),
        "state": raw.get("state"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "budget_day": raw.get("budget_day"),
        "amount_micros": amount,
        "amount_usd": _format_micros(amount),
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
