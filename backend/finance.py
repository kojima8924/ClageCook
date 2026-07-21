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
from storage import ConversationStore, utc_now


_RATE_FIELDS = (
    "input_per_million_usd",
    "output_per_million_usd",
    "cached_input_per_million_usd",
    "cache_write_per_million_usd",
)
_ACTIVE_RESERVATION_STATES = {"reserved", "reconciliation_pending"}
_SETTLED_RESERVATION_STATES = {"settled", "settled_after_manual_reconciliation"}


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
    for provider in plan.get("providers") or []:
        if not isinstance(provider, dict) or not provider.get("billable"):
            continue
        input_tokens = int(envelope.get("answer_per_call") or 0)
        if debate_effective:
            input_tokens += int(envelope.get("debate_per_call") or 0)
        calls = int(provider.get("max_calls") or 0)
        per_call_output = int(provider.get("max_output_tokens") or 0)
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
        per_call_output = int(synthesizer.get("max_output_tokens") or 0)
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
