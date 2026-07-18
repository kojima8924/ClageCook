# -*- coding: utf-8 -*-

from datetime import datetime, timezone

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
