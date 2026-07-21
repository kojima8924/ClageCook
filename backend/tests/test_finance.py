# -*- coding: utf-8 -*-

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

import config
import finance
import regeneration
from providers.base import normalized_usage
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


def _partial_price_file(tmp_path):
    path = tmp_path / "partial-prices.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "partial-test-v1",
                "currency": "USD",
                "models": {
                    "claude": {
                        "claude-test": {
                            "input_per_million_usd": "1.00",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _reasoning_price_file(tmp_path):
    path = tmp_path / "reasoning-prices.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "reasoning-test-v1",
                "currency": "USD",
                "models": {
                    provider: {
                        f"{provider}-test": {
                            "input_per_million_usd": "1.00",
                            "output_per_million_usd": "2.00",
                        }
                    }
                    for provider in ("gemini", "grok", "chatgpt")
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
                "max_output_tokens": 1,
            }
        ],
        "synthesizer": {"billable": False, "max_output_tokens": 0},
        "input_envelope": {"answer_per_call": 2, "debate_per_call": 0},
        "max_output_tokens": {"max_per_call": 1},
        "retry_envelope": {"configured_retries_per_live_call": 0},
    }


def _guard(tmp_path, monkeypatch, *, daily="0.000004", unknown="block"):
    monkeypatch.setattr(config, "PRICE_TABLE_FILE", str(_price_file(tmp_path)))
    monkeypatch.setattr(config, "PER_RUN_BUDGET_USD", "")
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", daily)
    monkeypatch.setattr(config, "BUDGET_UNKNOWN_POLICY", unknown)
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


def test_external_reasoning_tokens_are_billed_without_double_counting_openai(
    tmp_path,
):
    catalog = finance.PriceCatalog(str(_reasoning_price_file(tmp_path)))
    gemini_usage = normalized_usage(
        {
            "total_input_tokens": 7,
            "total_output_tokens": 4,
            "total_thought_tokens": 3,
            "total_tokens": 14,
        }
    )
    grok_compatible_usage = normalized_usage(
        {
            "prompt_tokens": 32,
            "completion_tokens": 9,
            "completion_tokens_details": {"reasoning_tokens": 110},
            "total_tokens": 151,
        }
    )
    openai_usage = normalized_usage(
        {
            "input_tokens": 32,
            "output_tokens": 9,
            "output_tokens_details": {"reasoning_tokens": 5},
            "total_tokens": 41,
        }
    )

    gemini = finance.estimate_usage_cost(
        catalog,
        "gemini",
        "gemini-test",
        gemini_usage,
    )
    grok = finance.estimate_usage_cost(
        catalog,
        "grok",
        "grok-test",
        grok_compatible_usage,
    )
    openai = finance.estimate_usage_cost(
        catalog,
        "chatgpt",
        "chatgpt-test",
        openai_usage,
    )

    assert gemini_usage["billable_output_tokens"] == 7
    assert gemini["micros"] == 21  # 7 input + (4 output + 3 thought) * 2
    assert "billable_output_tokens" not in grok_compatible_usage
    assert grok["micros"] == 270  # xAI Chat total-input includes external reasoning
    assert "billable_output_tokens" not in openai_usage
    assert openai["micros"] == 50  # reasoning is already inside output_tokens


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


def test_live_web_search_tool_pricing_keeps_cost_estimate_incomplete(tmp_path):
    catalog = finance.PriceCatalog(str(_price_file(tmp_path)))
    plan = _plan()
    plan["options"]["web_search_effective"] = True
    plan["web_search"] = {"effective": True, "provider_requests": 1}

    estimate = finance.estimate_plan_cost(plan, catalog)

    assert estimate["complete"] is False
    assert estimate["total_micros"] is None
    assert estimate["known_subtotal_micros"] == 4
    assert "web_search_tool_pricing" in estimate["missing"]


def test_unknown_cost_policy_blocks_unpriced_live_web_search(tmp_path, monkeypatch):
    guard = _guard(tmp_path, monkeypatch, daily="1.00")
    store = ConversationStore(tmp_path / "conversations")
    plan = _plan()
    plan["options"]["web_search_effective"] = True
    plan["web_search"] = {"effective": True, "provider_requests": 1}

    decorated = guard.decorate_plan(plan, store)

    assert decorated["allowed"] is False
    assert "budget_cost_unknown" in decorated["block_reasons"]
    assert "web_search_tool_pricing" in decorated["cost_estimate"]["missing"]


def test_unknown_allow_still_enforces_and_reserves_known_token_subtotal(
    tmp_path,
    monkeypatch,
):
    store = ConversationStore(tmp_path / "conversations")
    plan = _plan()
    plan["options"]["web_search_effective"] = True
    plan["web_search"] = {"effective": True, "provider_requests": 1}

    strict_known = _guard(
        tmp_path,
        monkeypatch,
        daily="0.000003",
        unknown="allow",
    )
    decorated = strict_known.decorate_plan(plan, store)
    assert decorated["allowed"] is False
    assert "budget_cost_unknown" not in decorated["block_reasons"]
    assert "daily_budget_exceeded" in decorated["block_reasons"]

    reserving = _guard(tmp_path, monkeypatch, daily="1.00", unknown="allow")
    reservation = reserving.reserve(
        request_id="known-web-subtotal",
        request_fingerprint="known-web-fingerprint",
        plan=plan,
        store=store,
        reservation_owner="known-web-owner",
    )
    assert reservation is not None
    assert reservation["amount_micros"] == 4


def test_partial_model_price_still_reserves_every_known_rate_category(
    tmp_path,
    monkeypatch,
):
    price_path = _partial_price_file(tmp_path)
    catalog = finance.PriceCatalog(str(price_path))

    estimate = finance.estimate_plan_cost(_plan(), catalog)

    assert estimate["complete"] is False
    assert estimate["total_micros"] is None
    assert estimate["known_subtotal_micros"] == 2
    assert estimate["items"][0]["known_micros"] == 2
    assert estimate["items"][0]["micros"] is None
    assert "output_per_million_usd" in estimate["missing"]

    monkeypatch.setattr(config, "PRICE_TABLE_FILE", str(price_path))
    monkeypatch.setattr(config, "PER_RUN_BUDGET_USD", "")
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", "1.00")
    monkeypatch.setattr(config, "BUDGET_UNKNOWN_POLICY", "allow")
    monkeypatch.setattr(config, "BUDGET_UTC_OFFSET", "+09:00")
    guard = finance.BudgetGuard(tmp_path / "partial-price-data")
    reservation = guard.reserve(
        request_id="partial-known-subtotal",
        request_fingerprint="partial-known-fingerprint",
        plan=_plan(),
        store=ConversationStore(tmp_path / "partial-price-conversations"),
    )
    assert reservation is not None
    assert reservation["amount_micros"] == 2


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


def test_actual_cost_snapshot_cache_tracks_store_revision(tmp_path, monkeypatch):
    guard = _guard(tmp_path, monkeypatch, daily="1.00")
    store = ConversationStore(tmp_path / "conversations")
    original = finance.actual_cost_snapshot
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(finance, "actual_cost_snapshot", counted)
    guard.decorate_plan(_plan(), store)
    guard.reserve(
        request_id="cached-reservation",
        request_fingerprint="cached-fingerprint",
        plan=_plan(),
        store=store,
    )
    assert calls == 1

    store.create("revision invalidates cache")
    guard.public_snapshot(store)
    assert calls == 2
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


def test_reserved_request_is_reusable_only_by_the_same_live_owner(
    tmp_path,
    monkeypatch,
):
    guard = _guard(tmp_path, monkeypatch, daily="1.00")
    store = ConversationStore(tmp_path / "conversations")
    first = guard.reserve(
        request_id="owned-request",
        request_fingerprint="same-fingerprint",
        plan=_plan(),
        store=store,
        reservation_owner="live-owner-one",
    )
    repeated = guard.reserve(
        request_id="owned-request",
        request_fingerprint="same-fingerprint",
        plan=_plan(),
        store=store,
        reservation_owner="live-owner-one",
    )

    assert first == repeated
    with pytest.raises(finance.BudgetViolation) as raised:
        guard.reserve(
            request_id="owned-request",
            request_fingerprint="same-fingerprint",
            plan=_plan(),
            store=store,
            reservation_owner="different-live-owner",
        )
    assert raised.value.code == "budget_reservation_in_progress"


def test_released_before_dispatch_rechecks_and_reactivates_for_retry(
    tmp_path,
    monkeypatch,
):
    guard = _guard(tmp_path, monkeypatch, daily="1.00")
    store = ConversationStore(tmp_path / "conversations")
    guard.reserve(
        request_id="cancelled-before-dispatch",
        request_fingerprint="same-fingerprint",
        plan=_plan(),
        store=store,
        reservation_owner="cancelled-owner",
    )
    guard.release_undispatched("cancelled-before-dispatch")

    reactivated = guard.reserve(
        request_id="cancelled-before-dispatch",
        request_fingerprint="same-fingerprint",
        plan=_plan(),
        store=store,
        reservation_owner="retry-owner",
    )

    assert reactivated is not None
    assert reactivated["state"] == "reserved"
    assert guard.public_snapshot(store)["today"]["active_reservations_micros"] == 4


def test_orphaned_or_settled_request_cannot_dispatch_again(
    tmp_path,
    monkeypatch,
):
    guard = _guard(tmp_path, monkeypatch, daily="1.00")
    store = ConversationStore(tmp_path / "conversations")
    guard.reserve(
        request_id="orphaned-retry",
        request_fingerprint="orphan-fingerprint",
        plan=_plan(),
        store=store,
        reservation_owner="old-process-owner",
    )
    restarted = finance.BudgetGuard(tmp_path / "data")
    assert restarted.recover_orphaned_reservations() == 1

    with pytest.raises(finance.BudgetViolation) as orphaned:
        restarted.reserve(
            request_id="orphaned-retry",
            request_fingerprint="orphan-fingerprint",
            plan=_plan(),
            store=store,
            reservation_owner="new-process-owner",
        )
    assert orphaned.value.code == "budget_reconciliation_required"

    guard.reserve(
        request_id="settled-retry",
        request_fingerprint="settled-fingerprint",
        plan=_plan(),
        store=store,
        reservation_owner="settled-owner",
    )
    guard.settle("settled-retry", usage_reconciled=True)
    with pytest.raises(finance.BudgetViolation) as settled:
        guard.reserve(
            request_id="settled-retry",
            request_fingerprint="settled-fingerprint",
            plan=_plan(),
            store=store,
            reservation_owner="new-settled-owner",
        )
    assert settled.value.code == "budget_request_already_finalized"


def test_settled_cost_remains_committed_after_conversation_is_deleted(
    tmp_path,
    monkeypatch,
):
    guard = _guard(tmp_path, monkeypatch, daily="1.00")
    store = ConversationStore(tmp_path / "conversations")
    reservation = guard.reserve(
        request_id="durable-settlement",
        request_fingerprint="durable-fingerprint",
        plan=_plan(),
        store=store,
        reservation_owner="durable-owner",
    )
    conversation = store.create("durable settled cost")
    turn = {
        "request_id": "durable-settlement",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "budget_reservation": reservation,
        "answers": {
            "claude": {
                "model": "claude-test",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        },
    }
    conversation["turns"] = [turn]
    store.save(conversation)
    guard.settle("durable-settlement", usage_reconciled=True, turn=turn)

    before_delete = guard.public_snapshot(store)
    assert before_delete["today"]["actual_estimated_micros"] == 3
    assert before_delete["today"]["durable_settled_micros"] == 3
    assert before_delete["today"]["settled_ledger_top_up_micros"] == 0
    assert before_delete["today"]["committed_micros"] == 3

    store.delete(conversation["id"])
    restarted = finance.BudgetGuard(tmp_path / "data")
    after_delete = restarted.public_snapshot(store)
    assert after_delete["today"]["actual_estimated_micros"] == 0
    assert after_delete["today"]["durable_settled_micros"] == 3
    assert after_delete["today"]["settled_ledger_top_up_micros"] == 3
    assert after_delete["today"]["committed_micros"] == 3


def test_unpriced_canonical_response_model_keeps_reserved_ceiling_committed(
    tmp_path,
    monkeypatch,
):
    guard = _guard(tmp_path, monkeypatch, daily="1.00")
    store = ConversationStore(tmp_path / "conversations")
    reservation = guard.reserve(
        request_id="canonical-model-alias",
        request_fingerprint="canonical-fingerprint",
        plan=_plan(),
        store=store,
        reservation_owner="canonical-owner",
    )
    conversation = store.create("provider canonical model")
    turn = {
        "request_id": "canonical-model-alias",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "budget_reservation": reservation,
        "answers": {
            "claude": {
                "model": "provider-canonical-model-not-in-price-table",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        },
    }
    conversation["turns"] = [turn]
    store.save(conversation)
    guard.settle("canonical-model-alias", usage_reconciled=True, turn=turn)

    snapshot = guard.public_snapshot(store)
    assert snapshot["today"]["actual_estimated_micros"] == 0
    assert snapshot["today"]["unpriced_requests"] == 1
    assert snapshot["today"]["durable_settled_micros"] == 4
    assert snapshot["today"]["settled_ledger_top_up_micros"] == 4
    assert snapshot["today"]["committed_micros"] == 4


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
            store=store,
        )
    assert raised.value.code == "reconciliation_confirmation_required"

    released = guard.release_after_manual_reconciliation(
        "manual-review",
        confirmed_no_unobserved_charge=True,
        store=store,
        note="Provider dashboard checked",
    )
    assert released["state"] == "settled_after_manual_reconciliation"
    assert released["settled_amount_micros"] == 4
    assert released["reconciliation_note"] == "Provider dashboard checked"
    snapshot = guard.public_snapshot(store)
    assert snapshot["reconciliation_backlog"]["count"] == 0
    assert snapshot["today"]["active_reservations_micros"] == 0
    assert snapshot["today"]["committed_micros"] == 4


def test_active_reservation_only_tops_up_observed_partial_usage(
    tmp_path,
    monkeypatch,
):
    guard = _guard(tmp_path, monkeypatch, daily="1.00")
    store = ConversationStore(tmp_path / "conversations")
    reservation = guard.reserve(
        request_id="partial-observed-active",
        request_fingerprint="partial-observed-fingerprint",
        plan=_plan(),
        store=store,
    )
    conversation = store.create("partial observed usage")
    conversation["turns"] = [
        {
            "request_id": "partial-observed-active",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "cancelled",
            "budget_reservation": reservation,
            "answers": {
                "claude": {
                    "model": "claude-test",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "usage_may_be_incomplete": True,
                }
            },
        }
    ]
    store.save(conversation)
    guard.settle("partial-observed-active", usage_reconciled=False)

    snapshot = guard.public_snapshot(store)

    assert snapshot["today"]["actual_estimated_micros"] == 3
    assert snapshot["today"]["active_reservations_micros"] == 4
    assert snapshot["today"]["active_reservation_top_up_micros"] == 1
    assert snapshot["today"]["committed_micros"] == 4


def test_manual_reconciliation_retains_observed_cost_after_conversation_delete(
    tmp_path,
    monkeypatch,
):
    guard = _guard(tmp_path, monkeypatch, daily="1.00")
    store = ConversationStore(tmp_path / "conversations")
    reservation = guard.reserve(
        request_id="manual-observed",
        request_fingerprint="manual-observed-fingerprint",
        plan=_plan(),
        store=store,
        reservation_owner="manual-observed-owner",
    )
    conversation = store.create("manual observed reconciliation")
    conversation["turns"] = [
        {
            "request_id": "manual-observed",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "budget_reservation": reservation,
            "answers": {
                "claude": {
                    "model": "claude-test",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            },
        }
    ]
    store.save(conversation)
    guard.settle("manual-observed", usage_reconciled=False)

    settled = guard.release_after_manual_reconciliation(
        "manual-observed",
        confirmed_no_unobserved_charge=True,
        store=store,
    )
    assert settled["settled_amount_micros"] == 3
    store.delete(conversation["id"])

    restarted = finance.BudgetGuard(tmp_path / "data")
    snapshot = restarted.public_snapshot(store)
    assert snapshot["today"]["durable_settled_micros"] == 3
    assert snapshot["today"]["settled_ledger_top_up_micros"] == 3
    assert snapshot["today"]["committed_micros"] == 3


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


def test_actual_cost_ignores_mock_and_skipped_entries_in_mixed_turn(tmp_path):
    catalog = finance.PriceCatalog(str(_price_file(tmp_path)))
    store = ConversationStore(tmp_path / "conversations")
    conversation = store.create("mixed billing")
    conversation["turns"] = [
        {
            "request_id": "mixed-request",
            "created_at": "2026-07-18T00:00:00Z",
            "budget_reservation": {
                "request_id": "mixed-request",
                "budget_day": "2026-07-18",
            },
            "answers": {
                "claude": {
                    "model": "claude-test",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
                "chatgpt": {
                    "model": "mock",
                    "mock": True,
                    "usage": {},
                },
            },
            "synthesis": {
                "source": "grok",
                "model": "unknown-skipped-model",
                "skipped": True,
                "usage": {},
            },
        }
    ]
    store.save(conversation)

    snapshot = finance.actual_cost_snapshot(
        store,
        catalog,
        budget_day="2026-07-18",
        budget_timezone=timezone.utc,
    )

    assert snapshot["total_micros"] == 3
    assert snapshot["unpriced_requests"] == 0
    assert snapshot["by_reservation_micros"] == {"mixed-request": 3}
    assert snapshot["incomplete_reservation_ids"] == []


def test_actual_cost_attributes_regeneration_to_its_reserved_budget_day(tmp_path):
    catalog = finance.PriceCatalog(str(_price_file(tmp_path)))
    store = ConversationStore(tmp_path / "conversations")
    conversation = store.create("cross-day attempt cost")
    conversation["turns"] = [
        {
            "created_at": "2026-07-17T12:00:00Z",
            "answers": {},
            "attempts": [
                {
                    "attempt_id": "original",
                    "target": "answer",
                    "provider": "claude",
                    "created_at": "2026-07-17T12:00:00Z",
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
                    # 完了が日を跨いでも、予約時に確定したbudget dayへ帰属する。
                    "created_at": "2026-07-19T00:01:00Z",
                    "budget_reservation": {"budget_day": "2026-07-18"},
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

    original_day = finance.actual_cost_snapshot(
        store,
        catalog,
        budget_day="2026-07-17",
        budget_timezone=timezone.utc,
    )
    regeneration_day = finance.actual_cost_snapshot(
        store,
        catalog,
        budget_day="2026-07-18",
        budget_timezone=timezone.utc,
    )
    completion_day = finance.actual_cost_snapshot(
        store,
        catalog,
        budget_day="2026-07-19",
        budget_timezone=timezone.utc,
    )

    assert original_day["total_micros"] == 3
    assert regeneration_day["total_micros"] == 4
    assert completion_day["total_micros"] == 0


def test_actual_cost_uses_attempt_timestamp_when_budget_guard_is_disabled(tmp_path):
    catalog = finance.PriceCatalog(str(_price_file(tmp_path)))
    store = ConversationStore(tmp_path / "conversations")
    conversation = store.create("unreserved regeneration")
    conversation["turns"] = [
        {
            "created_at": "2026-07-17T12:00:00Z",
            "answers": {},
            "attempts": [
                {
                    "attempt_id": "regenerated",
                    "target": "answer",
                    "provider": "claude",
                    "created_at": "2026-07-18T01:00:00Z",
                    "budget_reservation": None,
                    "original": False,
                    "result": {
                        "model": "claude-test",
                        "usage": {"input_tokens": 2, "output_tokens": 1},
                    },
                }
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

    assert snapshot["total_micros"] == 4


def test_actual_cost_deduplicates_turns_copied_into_a_branch(tmp_path):
    catalog = finance.PriceCatalog(str(_price_file(tmp_path)))
    store = ConversationStore(tmp_path / "conversations")
    parent = store.create("branch billing")
    parent["turns"] = [
        {
            "request_id": "shared-billing-request",
            "created_at": "2026-07-18T01:00:00Z",
            "status": "completed",
            "answers": {
                "claude": {
                    "model": "claude-test",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            },
        },
        {
            "request_id": "fork-point",
            "created_at": "2026-07-18T02:00:00Z",
            "status": "completed",
        },
    ]
    store.save(parent)
    store.create_branch(
        parent,
        before_turn_index=1,
        parent_turn_request_id="fork-point",
    )

    snapshot = finance.actual_cost_snapshot(
        store,
        catalog,
        budget_day="2026-07-18",
        budget_timezone=timezone.utc,
    )

    assert snapshot["total_micros"] == 3


def test_branch_dedup_survives_original_attempt_wrapping_after_regeneration(tmp_path):
    catalog = finance.PriceCatalog(str(_price_file(tmp_path)))
    store = ConversationStore(tmp_path / "conversations")
    parent = store.create("branch regeneration billing")
    original = {
        "model": "claude-test",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    parent["turns"] = [
        {
            "request_id": "shared-regenerated-request",
            "created_at": "2026-07-18T01:00:00Z",
            "status": "completed",
            "answers": {"claude": original},
        },
        {
            "request_id": "regeneration-fork-point",
            "created_at": "2026-07-18T02:00:00Z",
            "status": "completed",
        },
    ]
    store.save(parent)
    store.create_branch(
        parent,
        before_turn_index=1,
        parent_turn_request_id="regeneration-fork-point",
    )
    turn = parent["turns"][0]
    original_attempt_id = regeneration.ensure_original_attempt(
        turn,
        target_key="answer:claude",
        target="answer",
        provider="claude",
        current=original,
        now=lambda: "2026-07-18T03:00:00Z",
    )
    turn["attempts"].append(
        {
            "attempt_id": "new-regeneration-call",
            "parent_attempt_id": original_attempt_id,
            "target": "answer",
            "provider": "claude",
            "created_at": "2026-07-18T03:00:00Z",
            "status": "completed",
            "original": False,
            "result": {
                "model": "claude-test",
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        }
    )
    turn["active_attempts"]["answer:claude"] = "new-regeneration-call"
    store.save(parent)

    snapshot = finance.actual_cost_snapshot(
        store,
        catalog,
        budget_day="2026-07-18",
        budget_timezone=timezone.utc,
    )

    assert snapshot["total_micros"] == 7


def test_same_attempt_id_on_different_turns_is_not_deduplicated(tmp_path):
    catalog = finance.PriceCatalog(str(_price_file(tmp_path)))
    store = ConversationStore(tmp_path / "conversations")
    conversation = store.create("attempt identity")
    conversation["turns"] = [
        {
            "request_id": request_id,
            "created_at": "2026-07-18T01:00:00Z",
            "attempts": [
                {
                    "attempt_id": "legacy-colliding-attempt-id",
                    "target": "answer",
                    "provider": "claude",
                    "created_at": "2026-07-18T01:00:00Z",
                    "result": {
                        "model": "claude-test",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                }
            ],
        }
        for request_id in ("first-turn", "second-turn")
    ]
    store.save(conversation)

    snapshot = finance.actual_cost_snapshot(
        store,
        catalog,
        budget_day="2026-07-18",
        budget_timezone=timezone.utc,
    )

    assert snapshot["total_micros"] == 6


def test_original_turn_cost_uses_its_persisted_reservation_day(tmp_path):
    catalog = finance.PriceCatalog(str(_price_file(tmp_path)))
    store = ConversationStore(tmp_path / "conversations")
    conversation = store.create("cross-midnight chat")
    conversation["turns"] = [
        {
            "request_id": "cross-midnight-request",
            "created_at": "2026-07-19T00:01:00Z",
            "budget_reservation": {"budget_day": "2026-07-18"},
            "answers": {
                "claude": {
                    "model": "claude-test",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            },
        }
    ]
    store.save(conversation)

    reserved_day = finance.actual_cost_snapshot(
        store,
        catalog,
        budget_day="2026-07-18",
        budget_timezone=timezone.utc,
    )
    completion_day = finance.actual_cost_snapshot(
        store,
        catalog,
        budget_day="2026-07-19",
        budget_timezone=timezone.utc,
    )

    assert reserved_day["total_micros"] == 3
    assert completion_day["total_micros"] == 0


def test_uncached_input_token_semantics_are_provider_specific():
    # Anthropic reports cache-read tokens separately from input_tokens.
    assert finance._uncached_input_tokens("claude", 100, 40) == 100
    # The other normalized providers include cached tokens in input_tokens.
    assert finance._uncached_input_tokens("chatgpt", 100, 40) == 60
