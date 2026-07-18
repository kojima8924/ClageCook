# -*- coding: utf-8 -*-

from datetime import datetime, timezone

import regeneration
import telemetry
from storage import ConversationStore


def test_local_snapshot_aggregates_saved_usage_and_latest_quota(tmp_path):
    store = ConversationStore(tmp_path)
    conversation = store.create("usage")
    conversation["turns"] = [
        {
            "created_at": "2026-07-18T01:00:00.000Z",
            "answers": {
                "claude": {
                    "model": "claude-test",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                    },
                    "quota_snapshot": {
                        "source": "response_headers",
                        "observed_at": "2026-07-18T01:00:01.000Z",
                        "dimensions": {
                            "requests": {"limit": 50, "remaining": 49, "reset": "1m"}
                        },
                    },
                },
                "chatgpt": {
                    "model": "gpt-test",
                    "usage": {},
                    "usage_may_be_incomplete": True,
                },
            },
            "synthesis": {
                "source": "claude",
                "model": "claude-test",
                "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
            },
        }
    ]
    store.save(conversation)

    snapshot = telemetry.local_snapshot(
        store,
        now=datetime(2026, 7, 18, 12, tzinfo=timezone.utc),
    )
    providers = {item["name"]: item for item in snapshot["providers"]}
    claude = providers["claude"]
    assert claude["usage"]["all_time"]["observed_requests"] == 2
    assert claude["usage"]["today"]["usage"] == {
        "input_tokens": 14,
        "output_tokens": 7,
        "total_tokens": 21,
    }
    assert claude["latest_quota_snapshot"]["dimensions"]["requests"]["remaining"] == 49
    assert providers["chatgpt"]["usage"]["today"]["usage_unknown_requests"] == 1
    assert providers["gemini"]["usage"]["all_time"]["usage"] == {}


def test_local_snapshot_counts_every_regeneration_attempt(tmp_path):
    store = ConversationStore(tmp_path)
    conversation = store.create("attempt usage")
    conversation["turns"] = [
        {
            "created_at": "2026-07-18T01:00:00.000Z",
            "answers": {
                "claude": {
                    "model": "active",
                    "usage": {"input_tokens": 20, "output_tokens": 10},
                }
            },
            "attempts": [
                {
                    "target": "answer",
                    "provider": "claude",
                    "original": True,
                    "result": {
                        "model": "original",
                        "usage": {"input_tokens": 10, "output_tokens": 5},
                    },
                },
                {
                    "target": "answer",
                    "provider": "claude",
                    "original": False,
                    "result": {
                        "model": "active",
                        "usage": {"input_tokens": 20, "output_tokens": 10},
                    },
                },
            ],
            "synthesis": {"source": "none", "usage": {}},
        }
    ]
    store.save(conversation)
    snapshot = telemetry.local_snapshot(
        store,
        now=datetime(2026, 7, 18, 12, tzinfo=timezone.utc),
    )
    claude = next(item for item in snapshot["providers"] if item["name"] == "claude")
    assert claude["usage"]["today"]["observed_requests"] == 2
    assert claude["usage"]["today"]["usage"]["input_tokens"] == 30
    assert set(claude["usage"]["today"]["models"]) == {"original", "active"}


def test_local_snapshot_deduplicates_branch_and_uses_attempt_day(tmp_path):
    store = ConversationStore(tmp_path)
    parent = store.create("branch telemetry")
    parent["turns"] = [
        {
            "request_id": "shared-telemetry-request",
            "created_at": "2026-07-17T01:00:00.000Z",
            "status": "completed",
            "attempts": [
                {
                    "attempt_id": "original-telemetry-attempt",
                    "target": "answer",
                    "provider": "claude",
                    "original": True,
                    "created_at": "2026-07-17T01:00:00.000Z",
                    "result": {
                        "model": "original",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                },
                {
                    "attempt_id": "today-telemetry-attempt",
                    "target": "answer",
                    "provider": "claude",
                    "original": False,
                    "created_at": "2026-07-18T02:00:00.000Z",
                    "result": {
                        "model": "regenerated",
                        "usage": {"input_tokens": 2, "output_tokens": 1},
                    },
                },
            ],
        },
        {
            "request_id": "telemetry-fork-point",
            "created_at": "2026-07-18T03:00:00.000Z",
            "status": "completed",
        },
    ]
    store.save(parent)
    store.create_branch(
        parent,
        before_turn_index=1,
        parent_turn_request_id="telemetry-fork-point",
    )

    snapshot = telemetry.local_snapshot(
        store,
        now=datetime(2026, 7, 18, 12, tzinfo=timezone.utc),
    )
    claude = next(item for item in snapshot["providers"] if item["name"] == "claude")

    assert claude["usage"]["all_time"]["observed_requests"] == 2
    assert claude["usage"]["today"]["observed_requests"] == 1
    assert claude["usage"]["today"]["usage"]["input_tokens"] == 2


def test_branch_dedup_survives_original_attempt_wrapping(tmp_path):
    store = ConversationStore(tmp_path)
    parent = store.create("branch wrapped telemetry")
    original = {
        "model": "original",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    parent["turns"] = [
        {
            "request_id": "shared-wrapped-telemetry",
            "created_at": "2026-07-17T01:00:00.000Z",
            "status": "completed",
            "answers": {"claude": original},
        },
        {
            "request_id": "wrapped-telemetry-fork-point",
            "created_at": "2026-07-18T01:00:00.000Z",
            "status": "completed",
        },
    ]
    store.save(parent)
    store.create_branch(
        parent,
        before_turn_index=1,
        parent_turn_request_id="wrapped-telemetry-fork-point",
    )
    turn = parent["turns"][0]
    original_attempt_id = regeneration.ensure_original_attempt(
        turn,
        target_key="answer:claude",
        target="answer",
        provider="claude",
        current=original,
        now=lambda: "2026-07-18T02:00:00.000Z",
    )
    turn["attempts"].append(
        {
            "attempt_id": "wrapped-telemetry-regeneration",
            "parent_attempt_id": original_attempt_id,
            "target": "answer",
            "provider": "claude",
            "created_at": "2026-07-18T02:00:00.000Z",
            "status": "completed",
            "original": False,
            "result": {
                "model": "regenerated",
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        }
    )
    store.save(parent)

    snapshot = telemetry.local_snapshot(
        store,
        now=datetime(2026, 7, 18, 12, tzinfo=timezone.utc),
    )
    claude = next(item for item in snapshot["providers"] if item["name"] == "claude")

    assert claude["usage"]["all_time"]["observed_requests"] == 2
    assert claude["usage"]["all_time"]["usage"]["input_tokens"] == 3
    assert claude["usage"]["today"]["observed_requests"] == 1


def test_same_attempt_id_on_different_turns_counts_both_requests(tmp_path):
    store = ConversationStore(tmp_path)
    conversation = store.create("attempt identity")
    conversation["turns"] = [
        {
            "request_id": request_id,
            "created_at": "2026-07-18T01:00:00.000Z",
            "attempts": [
                {
                    "attempt_id": "legacy-colliding-attempt-id",
                    "target": "answer",
                    "provider": "claude",
                    "created_at": "2026-07-18T01:00:00.000Z",
                    "result": {
                        "model": "same-model",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                }
            ],
        }
        for request_id in ("first-turn", "second-turn")
    ]
    store.save(conversation)

    snapshot = telemetry.local_snapshot(
        store,
        now=datetime(2026, 7, 18, 12, tzinfo=timezone.utc),
    )
    claude = next(item for item in snapshot["providers"] if item["name"] == "claude")

    assert claude["usage"]["today"]["observed_requests"] == 2
