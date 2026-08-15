# -*- coding: utf-8 -*-
"""明示価格表による推定と、単一process向けのdurable予算予約。"""

from __future__ import annotations

import json
import os
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import config
from storage import ConversationStore, utc_now

# 再export: 純粋なコスト計算・課金entry集計・設定パーサはfinance_costs.pyへ分離。
# test(test_finance.py)がfinance.actual_cost_snapshot等をmonkeypatchし、
# BudgetGuard/PriceCatalogは本モジュールglobal経由で参照し続けるため、
# 同名で全て再exportして互換を維持する。
from finance_costs import (
    FinanceConfigurationError,
    actual_cost_snapshot,
    estimate_plan_cost,
    estimate_usage_cost,
    turn_cost_micros,
    turn_usage_reconciled,
    _billable_output_tokens,
    _billing_day,
    _billing_entries,
    _billing_identity,
    _billing_reservation_id,
    _budget_message,
    _cost_micros,
    _day_key,
    _estimate_max_item,
    _format_micros,
    _parse_usd_limit,
    _parse_utc_offset,
    _public_reservation,
    _rate_decimal,
    _reservation_amount,
    _safe_usage,
    _turn_entries,
    _uncached_input_tokens,
    _valid_day_key,
)


_RATE_FIELDS = (
    "input_per_million_usd",
    "output_per_million_usd",
    "cached_input_per_million_usd",
    "cache_write_per_million_usd",
)
_ACTIVE_RESERVATION_STATES = {"reserved", "reconciliation_pending"}
_SETTLED_RESERVATION_STATES = {"settled", "settled_after_manual_reconciliation"}


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
        self._actual_cache_store: ConversationStore | None = None
        self._actual_cache_key: tuple[int, str, str] | None = None
        self._actual_cache: dict[str, Any] | None = None

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
        reservation_owner: str | None = None,
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
                existing_state = existing.get("state")
                if existing_state == "reserved":
                    existing_owner = existing.get("reservation_owner")
                    if (
                        isinstance(existing_owner, str)
                        and existing_owner
                        and existing_owner != reservation_owner
                    ):
                        raise BudgetViolation(
                            "budget_reservation_in_progress",
                            "同じrequest_idの予算予約が別の実行で使用中です",
                            self._snapshot_locked(store, ledger=ledger),
                        )
                    if reservation_owner and not existing_owner:
                        existing["reservation_owner"] = reservation_owner
                        existing["updated_at"] = utc_now()
                        self._write_ledger(ledger)
                    return _public_reservation(existing)
                if existing_state == "released_before_dispatch":
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
                    amount = _reservation_amount(estimate)
                    existing.update(
                        {
                            "state": "reserved",
                            "updated_at": utc_now(),
                            "reactivated_at": utc_now(),
                            "budget_day": self._today_key(),
                            "amount_micros": amount if isinstance(amount, int) else 0,
                            "price_version": self.catalog.version,
                            "estimate": estimate,
                            "reserved_by_pid": os.getpid(),
                            "reservation_owner": reservation_owner,
                        }
                    )
                    self._write_ledger(ledger)
                    return _public_reservation(existing)
                if existing_state == "reconciliation_pending":
                    code = "budget_reconciliation_required"
                    message = (
                        "同じrequest_idはProvider利用照合待ちです。"
                        "既存結果を確認するか手動照合を完了してください"
                    )
                elif existing_state in _SETTLED_RESERVATION_STATES:
                    code = "budget_request_already_finalized"
                    message = "同じrequest_idは確定済みです。保存済み結果を再利用してください"
                else:
                    code = "budget_request_id_finalized"
                    message = "同じrequest_idは終了済みです。新しいrequest_idを使用してください"
                raise BudgetViolation(
                    code,
                    message,
                    self._snapshot_locked(store, ledger=ledger),
                )
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
            amount = _reservation_amount(estimate)
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
                "reservation_owner": reservation_owner,
                "estimate": estimate,
            }
            ledger["reservations"][request_id] = reservation
            self._write_ledger(ledger)
            return _public_reservation(reservation)

    def settle(
        self,
        request_id: str,
        *,
        usage_reconciled: bool,
        turn: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        with self._lock:
            ledger = self._read_ledger()
            reservation = ledger["reservations"].get(request_id)
            if not isinstance(reservation, dict):
                return
            state = reservation.get("state")
            if state in _SETTLED_RESERVATION_STATES:
                return
            if state == "reconciliation_pending" and not usage_reconciled:
                return
            if state not in {"reserved", "reconciliation_pending"}:
                return
            now = utc_now()
            reservation["state"] = (
                "settled" if usage_reconciled else "reconciliation_pending"
            )
            if usage_reconciled:
                actual = (
                    turn_cost_micros(turn, self.catalog)
                    if isinstance(turn, dict)
                    else None
                )
                # 旧callerや価格区分の欠落時も、予約上限を0円へ解放しない。
                # 通常の完了経路は保存済みusageから実測token換算額を渡せる。
                if not isinstance(actual, int) or actual < 0:
                    reserved = reservation.get("amount_micros")
                    actual = reserved if isinstance(reserved, int) and reserved >= 0 else 0
                reservation["settled_amount_micros"] = actual
                reservation["settled_at"] = now
            reservation["updated_at"] = now
            self._write_ledger(ledger)

    def refresh_reservation(
        self,
        *,
        request_id: str,
        request_fingerprint: str,
        plan: dict[str, Any],
        store: ConversationStore,
        reservation_owner: str,
    ) -> dict[str, Any] | None:
        """dispatch直前のplan・予算日で、同じlive ownerの予約を原子的に再検証する。"""
        if not self.enabled:
            return None
        with self._lock:
            ledger = self._read_ledger()
            reservation = ledger["reservations"].get(request_id)
            if not plan.get("billable"):
                if (
                    isinstance(reservation, dict)
                    and reservation.get("state") == "reserved"
                    and reservation.get("reservation_owner") == reservation_owner
                ):
                    reservation["state"] = "released_before_dispatch"
                    reservation["updated_at"] = utc_now()
                    self._write_ledger(ledger)
                return None
            if not isinstance(reservation, dict):
                raise BudgetViolation(
                    "budget_reservation_missing",
                    "dispatch直前の予算予約が見つかりません",
                    self._snapshot_locked(store, ledger=ledger),
                )
            if reservation.get("request_fingerprint") != request_fingerprint:
                raise BudgetViolation(
                    "budget_reservation_conflict",
                    "request_idが異なる予算予約で使用済みです",
                    self._snapshot_locked(store, ledger=ledger),
                )
            if (
                reservation.get("state") != "reserved"
                or reservation.get("reservation_owner") != reservation_owner
            ):
                raise BudgetViolation(
                    "budget_reservation_not_active",
                    "dispatch可能な予算予約ではありません",
                    self._snapshot_locked(store, ledger=ledger),
                )

            decision_ledger = deepcopy(ledger)
            decision_reservation = decision_ledger["reservations"].get(request_id)
            if isinstance(decision_reservation, dict):
                decision_reservation["state"] = "refreshing"
            estimate = estimate_plan_cost(plan, self.catalog)
            snapshot = self._snapshot_locked(store, ledger=decision_ledger)
            decision = self._decision(estimate, snapshot)
            if not decision["allowed"]:
                code = decision["block_reasons"][0]
                raise BudgetViolation(code, _budget_message(code), decision)
            amount = _reservation_amount(estimate)
            now = utc_now()
            reservation.update(
                {
                    "amount_micros": amount if isinstance(amount, int) else 0,
                    "budget_day": self._today_key(),
                    "price_version": self.catalog.version,
                    "estimate": estimate,
                    "dispatch_refreshed_at": now,
                    "updated_at": now,
                }
            )
            self._write_ledger(ledger)
            return _public_reservation(reservation)

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
        store: ConversationStore,
        note: str = "",
    ) -> dict[str, Any]:
        """照合待ちを解消し、観測済み額または予約上限を確定台帳へ残す。"""
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
            budget_day = reservation.get("budget_day")
            observed: int | None = None
            if _valid_day_key(budget_day):
                actual = actual_cost_snapshot(
                    store,
                    self.catalog,
                    budget_day=str(budget_day),
                    budget_timezone=self.budget_timezone,
                )
                incomplete = set(actual.get("incomplete_reservation_ids") or [])
                by_reservation = actual.get("by_reservation_micros") or {}
                candidate = by_reservation.get(request_id)
                if request_id not in incomplete and isinstance(candidate, int) and candidate >= 0:
                    observed = candidate
            if observed is None:
                reserved = reservation.get("amount_micros")
                observed = reserved if isinstance(reserved, int) and reserved >= 0 else 0

            now = utc_now()
            reservation["state"] = "settled_after_manual_reconciliation"
            reservation["settled_amount_micros"] = observed
            reservation["settled_at"] = now
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
        known_subtotal = estimate.get("known_subtotal_micros")
        guarded_amount = total if isinstance(total, int) else known_subtotal
        complete = estimate.get("complete") is True
        if self.enabled and not complete:
            warning = {
                "code": "cost_estimate_incomplete",
                "message": "価格表にないmodelまたは課金区分があるため、金額見積りは不完全です。",
            }
            warnings.append(warning)
            if self.unknown_policy == "block":
                block_reasons.append("budget_cost_unknown")
        if isinstance(guarded_amount, int):
            if (
                self.per_run_limit_micros is not None
                and guarded_amount > self.per_run_limit_micros
            ):
                block_reasons.append("per_run_budget_exceeded")
            committed = snapshot["today"]["committed_micros"]
            if (
                self.daily_limit_micros is not None
                and committed + guarded_amount > self.daily_limit_micros
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
            "run_known_subtotal_micros": (
                known_subtotal if isinstance(known_subtotal, int) else None
            ),
            "run_known_subtotal_usd": _format_micros(
                known_subtotal if isinstance(known_subtotal, int) else None
            ),
        }

    def _snapshot_locked(
        self,
        store: ConversationStore,
        *,
        ledger: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        active_ledger = ledger or self._read_ledger()
        budget_day = self._today_key()
        store_revision = store.revision
        cache_key = (
            store_revision,
            budget_day,
            self.catalog.version,
        )
        if (
            self._actual_cache_store is store
            and self._actual_cache_key == cache_key
            and self._actual_cache is not None
        ):
            actual = deepcopy(self._actual_cache)
        else:
            actual = actual_cost_snapshot(
                store,
                self.catalog,
                budget_day=budget_day,
                budget_timezone=self.budget_timezone,
            )
            # Provider完了saveと競合したsnapshotは使用できるがcacheしない。
            # そのrunの予約はsettleまでactiveなので、この瞬間も安全側を維持する。
            if store.revision == store_revision:
                self._actual_cache_store = store
                self._actual_cache_key = cache_key
                self._actual_cache = deepcopy(actual)
        outstanding = [
            value
            for value in active_ledger["reservations"].values()
            if isinstance(value, dict)
            and value.get("state") in _ACTIVE_RESERVATION_STATES
        ]
        active = [
            value for value in outstanding if value.get("budget_day") == budget_day
        ]
        settled = [
            value
            for value in active_ledger["reservations"].values()
            if isinstance(value, dict)
            and value.get("state") in _SETTLED_RESERVATION_STATES
            and value.get("budget_day") == budget_day
        ]
        reserved = sum(
            value.get("amount_micros", 0)
            for value in active
            if isinstance(value.get("amount_micros"), int)
        )
        actual_micros = actual["total_micros"]
        actual_by_reservation = actual.get("by_reservation_micros") or {}
        active_top_up = 0
        for item in active:
            amount = item.get("amount_micros")
            if not isinstance(amount, int) or amount < 0:
                continue
            request_id = item.get("request_id")
            represented = (
                actual_by_reservation.get(request_id, 0)
                if isinstance(request_id, str)
                else 0
            )
            if not isinstance(represented, int) or represented < 0:
                represented = 0
            active_top_up += max(0, amount - represented)
        durable_settled = 0
        settled_top_up = 0
        for item in settled:
            amount = item.get("settled_amount_micros")
            if not isinstance(amount, int) or amount < 0:
                amount = item.get("amount_micros")
            if not isinstance(amount, int) or amount < 0:
                continue
            durable_settled += amount
            request_id = item.get("request_id")
            represented = (
                actual_by_reservation.get(request_id, 0)
                if isinstance(request_id, str)
                else 0
            )
            if not isinstance(represented, int) or represented < 0:
                represented = 0
            settled_top_up += max(0, amount - represented)
        committed = actual_micros + active_top_up + settled_top_up
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
                "durable_settled_micros": durable_settled,
                "durable_settled_usd": _format_micros(durable_settled),
                "settled_ledger_top_up_micros": settled_top_up,
                "settled_ledger_top_up_usd": _format_micros(settled_top_up),
                "active_reservations_micros": reserved,
                "active_reservations_usd": _format_micros(reserved),
                "active_reservation_top_up_micros": active_top_up,
                "active_reservation_top_up_usd": _format_micros(active_top_up),
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
