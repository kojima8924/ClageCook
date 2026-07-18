# -*- coding: utf-8 -*-

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timezone

import pytest

import config
import finance
from storage import ConversationStore


def _price_file(tmp_path):
    path = tmp_path / "prices.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "test-v1",
                "currency": "USD",
                "models": {
                    "claude": {
                        "claude-test": {
                            "input_per_million_usd": "1.00",
                            "output_per_million_usd": "2.00",
                            "cached_input_per_million_usd": "0.10",
                            "cache_write_per_million_usd": "1.25",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _plan():
    return {
        "allowed": True,
        "block_reasons": [],
        "warnings": [],
        "billable": True,
        "options": {"debate_effective": False},
        "providers": [
            {
                "name": "claude",
                "model": "claude-test",
                "billable": True,
                "max_calls": 1,
            }
        ],
        "synthesizer": {"billable": False},
        "input_envelope": {"answer_per_call": 2, "debate_per_call": 0},
        "max_output_tokens": {"per_call": 1},
        "retry_envelope": {"configured_retries_per_live_call": 0},
    }


def _guard(tmp_path, monkeypatch, *, daily="0.000004"):
    monkeypatch.setattr(config, "PRICE_TABLE_FILE", str(_price_file(tmp_path)))
    monkeypatch.setattr(config, "PER_RUN_BUDGET_USD", "")
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", daily)
    monkeypatch.setattr(config, "BUDGET_UNKNOWN_POLICY", "block")
    monkeypatch.setattr(config, "BUDGET_UTC_OFFSET", "+09:00")
    return finance.BudgetGuard(tmp_path / "data")


def test_usage_cost_uses_decimal_micros_and_cache_categories(tmp_path):
    catalog = finance.PriceCatalog(str(_price_file(tmp_path)))
    result = finance.estimate_usage_cost(
        catalog,
        "claude",
        "claude-test",
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "cached_input_tokens": 4,
            "cache_creation_input_tokens": 2,
        },
    )
    assert result["complete"] is True
    # 10*1 + 5*2 + 4*0.1 + 2*1.25 = 22.9 micro USD, safety rounded up.
    assert result["micros"] == 23
    assert result["usd"] == "0.000023"


def test_missing_price_is_unknown_not_zero(tmp_path):
    catalog = finance.PriceCatalog(str(_price_file(tmp_path)))
    result = finance.estimate_usage_cost(
        catalog,
        "claude",
        "unknown-model",
        {"input_tokens": 1, "output_tokens": 1},
    )
    assert result["complete"] is False
    assert result["micros"] is None


def test_concurrent_reservations_cannot_overspend(tmp_path, monkeypatch):
    guard = _guard(tmp_path, monkeypatch)
    store = ConversationStore(tmp_path / "conversations")
    guard.validate_configuration()

    def reserve(index):
        try:
            guard.reserve(
                request_id=f"request-{index}",
                request_fingerprint=f"fingerprint-{index}",
                plan=_plan(),
                store=store,
            )
            return "reserved"
        except finance.BudgetViolation:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sorted(results) == ["blocked", "reserved"]
    snapshot = guard.public_snapshot(store)
    assert snapshot["today"]["active_reservations_micros"] == 4
    assert len(snapshot["active_reservations"]) == 1


def test_same_request_is_idempotent_and_different_fingerprint_conflicts(
    tmp_path, monkeypatch
):
    guard = _guard(tmp_path, monkeypatch, daily="1.00")
    store = ConversationStore(tmp_path / "conversations")
    first = guard.reserve(
        request_id="same-request",
        request_fingerprint="same-fingerprint",
        plan=_plan(),
        store=store,
    )
    second = guard.reserve(
        request_id="same-request",
        request_fingerprint="same-fingerprint",
        plan=_plan(),
        store=store,
    )
    assert first == second
    with pytest.raises(finance.BudgetViolation) as raised:
        guard.reserve(
            request_id="same-request",
            request_fingerprint="different",
            plan=_plan(),
            store=store,
        )
    assert raised.value.code == "budget_reservation_conflict"


def test_unreconciled_attempt_keeps_reservation_committed(tmp_path, monkeypatch):
    guard = _guard(tmp_path, monkeypatch, daily="1.00")
    store = ConversationStore(tmp_path / "conversations")
    guard.reserve(
        request_id="pending-request",
        request_fingerprint="fingerprint",
        plan=_plan(),
        store=store,
    )
    guard.settle("pending-request", usage_reconciled=False)
    snapshot = guard.public_snapshot(store)
    assert snapshot["active_reservations"][0]["state"] == "reconciliation_pending"
    assert snapshot["today"]["committed_micros"] == 4


def test_restart_promotes_orphaned_reservation_without_releasing_budget(
    tmp_path, monkeypatch
):
    guard = _guard(tmp_path, monkeypatch, daily="1.00")
    store = ConversationStore(tmp_path / "conversations")
    guard.reserve(
        request_id="crashed-request",
        request_fingerprint="fingerprint",
        plan=_plan(),
        store=store,
    )

    restarted = finance.BudgetGuard(tmp_path / "data")
    assert restarted.recover_orphaned_reservations() == 1
    snapshot = restarted.public_snapshot(store)
    assert snapshot["active_reservations"][0]["state"] == "reconciliation_pending"
    assert snapshot["active_reservations"][0]["reconciliation_reason"] == (
        "server_restarted_before_settlement"
    )
    assert snapshot["today"]["committed_micros"] == 4


def test_reconciliation_backlog_blocks_new_billable_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_UNRECONCILED_RESERVATIONS", 1)
    guard = _guard(tmp_path, monkeypatch, daily="1.00")
    store = ConversationStore(tmp_path / "conversations")
    guard.reserve(
        request_id="first-request",
        request_fingerprint="first",
        plan=_plan(),
        store=store,
    )
    guard.settle("first-request", usage_reconciled=False)

    with pytest.raises(finance.BudgetViolation) as raised:
        guard.reserve(
            request_id="second-request",
            request_fingerprint="second",
            plan=_plan(),
            store=store,
        )
    assert raised.value.code == "budget_reconciliation_backlog"
    assert raised.value.snapshot["reconciliation_backlog"]["count"] == 1


def test_manual_reconciliation_requires_confirmation_and_releases_backlog(
    tmp_path, monkeypatch
):
    guard = _guard(tmp_path, monkeypatch, daily="1.00")
    store = ConversationStore(tmp_path / "conversations")
    guard.reserve(
        request_id="manual-review",
        request_fingerprint="fingerprint",
        plan=_plan(),
        store=store,
    )
    guard.settle("manual-review", usage_reconciled=False)
    with pytest.raises(finance.ReconciliationError) as raised:
        guard.release_after_manual_reconciliation(
            "manual-review",
            confirmed_no_unobserved_charge=False,
        )
    assert raised.value.code == "reconciliation_confirmation_required"

    released = guard.release_after_manual_reconciliation(
        "manual-review",
        confirmed_no_unobserved_charge=True,
        note="Provider dashboard checked",
    )
    assert released["state"] == "released_after_manual_reconciliation"
    assert released["reconciliation_note"] == "Provider dashboard checked"
    snapshot = guard.public_snapshot(store)
    assert snapshot["reconciliation_backlog"]["count"] == 0
    assert snapshot["today"]["active_reservations_micros"] == 0


def test_ledger_write_failure_never_returns_a_reservation(tmp_path, monkeypatch):
    guard = _guard(tmp_path, monkeypatch, daily="1.00")
    store = ConversationStore(tmp_path / "conversations")

    def fail_replace(_source, _destination):
        raise OSError("disk full")

    monkeypatch.setattr(finance.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk full"):
        guard.reserve(
            request_id="failed-write",
            request_fingerprint="fingerprint",
            plan=_plan(),
            store=store,
        )


def test_actual_cost_keeps_original_and_regeneration_usage(tmp_path):
    catalog = finance.PriceCatalog(str(_price_file(tmp_path)))
    store = ConversationStore(tmp_path / "conversations")
    conversation = store.create("attempt cost")
    conversation["turns"] = [
        {
            "created_at": "2026-07-18T00:00:00Z",
            "answers": {
                "claude": {
                    "model": "claude-test",
                    "usage": {"input_tokens": 2, "output_tokens": 1},
                }
            },
            "attempts": [
                {
                    "attempt_id": "original",
                    "target": "answer",
                    "provider": "claude",
                    "original": True,
                    "result": {
                        "model": "claude-test",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                },
                {
                    "attempt_id": "regenerated",
                    "target": "answer",
                    "provider": "claude",
                    "original": False,
                    "result": {
                        "model": "claude-test",
                        "usage": {"input_tokens": 2, "output_tokens": 1},
                    },
                },
            ],
        }
    ]
    store.save(conversation)
    snapshot = finance.actual_cost_snapshot(
        store,
        catalog,
        budget_day="2026-07-18",
        budget_timezone=timezone.utc,
    )
    # Original: 1*1 + 1*2 = 3; regeneration: 2*1 + 1*2 = 4 micro USD.
    assert snapshot["total_micros"] == 7
    assert snapshot["unpriced_requests"] == 0
