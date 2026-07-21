# -*- coding: utf-8 -*-

import pytest

import orchestrator


@pytest.mark.asyncio
async def test_turn_emits_and_persists_local_insights(monkeypatch):
    async def fake_provider(
        source,
        prompt,
        *,
        system,
        tier,
        reasoning_mode,
        round_number,
        prompt_cache_key=None,
    ):
        del prompt_cache_key
        del prompt, system, tier, reasoning_mode
        text = {
            "claude": "安全性と暗号化を優先します。",
            "gemini": "安全性と監査ログを優先します。",
        }[source]
        return {
            "source": source,
            "ok": True,
            "text": text,
            "round": round_number,
            "model": "fake",
            "usage": {},
        }

    monkeypatch.setattr(orchestrator.config, "active_workers", lambda: ["claude", "gemini"])
    monkeypatch.setattr(orchestrator, "_run_provider", fake_provider)
    events = []

    async def emit(event, data):
        events.append((event, data))

    turn = await orchestrator.run_turn(
        {"id": "conversation", "turns": []},
        "安全策を比較して",
        orchestrator.TurnOptions(
            providers=("claude", "gemini"), synthesize=False
        ),
        "insights-request",
        emit,
    )

    insight_events = [data for event, data in events if event == "insights"]
    assert len(insight_events) == 1
    assert insight_events[0] == turn["insights"]
    assert turn["insights"]["is_comparable"] is True
    assert "安全性" in turn["insights"]["shared_terms"]
    assert [event for event, _ in events].index("insights") < [
        event for event, _ in events
    ].index("synthesis")
