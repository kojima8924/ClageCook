# -*- coding: utf-8 -*-

from types import SimpleNamespace

import pytest

import regeneration


class _Config:
    WORKERS = ("claude", "chatgpt")
    LABELS = {"claude": "Claude"}
    HTTP_RETRIES = 1
    MAX_OUTPUT_TOKENS = {"low": 50, "balanced": 100, "high": 200}
    MAX_PROVIDER_CALLS_PER_RUN = 8
    MAX_OUTPUT_TOKENS_PER_RUN = 1_000
    MAX_INPUT_BYTES_PER_RUN = 20_000
    WEB_SEARCH_ENABLED = False
    WEB_SEARCH_MAX_USES = 2

    @staticmethod
    def synthesizer_name(*, runtime=None):
        return "synthesizer"

    @staticmethod
    def provider_status(_source, *, runtime=None):
        return SimpleNamespace(mode="mock", label="Claude")

    @staticmethod
    def model_for(_source, _tier, *, runtime=None):
        return "unused-live-model"

    @staticmethod
    def synthesizer_model_for(_tier, *, runtime=None, synthesizer=None):
        return "mock"

    @staticmethod
    def mode():
        return "mock"


class _Orchestrator:
    WORKER_SYSTEM = "worker-system"
    SYNTH_SYSTEM = "synth-system"

    @staticmethod
    def _worker_prompt(_context, message):
        return f"worker:{message}"

    @staticmethod
    def _synthesis_prompt(message, _answers, _aliases):
        return f"synthesis:{message}"

    @staticmethod
    def _blind_aliases(names, _request_id):
        return {name: f"Model {index}" for index, name in enumerate(names, 1)}


def _completed_turn():
    return {
        "request_id": "original-request",
        "created_at": "2026-01-01T00:00:00.000Z",
        "status": "completed",
        "message": "question",
        "clean_message": "question",
        "options": {"tier": "balanced", "blind": False, "web_search": False},
        "answers": {
            "claude": {
                "ok": True,
                "source": "claude",
                "text": "old answer",
            }
        },
        "synthesis": {
            "ok": True,
            "source": "synthesizer",
            "text": "old synthesis",
            "skipped": False,
        },
    }


def test_build_plan_uses_explicit_dependencies_and_attachment_bundle():
    decorated = []
    dependencies = regeneration.PlanDependencies(
        config=_Config,
        orchestrator=_Orchestrator,
        runtime_snapshot=lambda: {},
        scan_text=lambda text: {"action": "allow", "scanned": text},
        attachment_context=lambda *_args: pytest.fail(
            "provided attachment bundle must be reused"
        ),
        decorate_plan=lambda plan: decorated.append(plan) or {**plan, "decorated": True},
    )
    conversation = {"id": "conversation", "turns": [_completed_turn()]}

    plan = regeneration.build_plan(
        conversation,
        0,
        target="answer",
        provider="claude",
        dependencies=dependencies,
        attachment_bundle=(
            "\nattachment text",
            [{"attachment_id": "attachment-1", "included_in_prompt": True}],
        ),
    )

    assert plan["decorated"] is True
    assert plan["regeneration"] == {"target": "answer", "provider": "claude"}
    assert plan["calls"] == {"answers": 1, "debate": 0, "synthesis": 0, "total": 1}
    assert plan["attachments"]["text_included_count"] == 1
    assert plan["policy"]["scanned"] == "question\nattachment text"
    assert decorated[0]["billable"] is False
    expected_input = len(
        (
            "worker:question\nattachment text"
            + "worker-system "
            + regeneration.ANSWER_REGENERATION_INSTRUCTION
        ).encode("utf-8")
    )
    assert plan["input_envelope"]["answer_per_call"] == expected_input


def test_synthesis_plan_uses_one_runtime_snapshot_for_provider_and_model():
    class ChangingConfig(_Config):
        LABELS = {**_Config.LABELS, "chatgpt": "ChatGPT"}

        @staticmethod
        def synthesizer_name(*, runtime=None):
            return runtime["provider"]

        @staticmethod
        def synthesizer_model_for(
            _tier,
            *,
            runtime=None,
            synthesizer=None,
        ):
            assert synthesizer == runtime["provider"]
            return runtime["model"]

    calls = 0

    def changing_snapshot():
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"provider": "chatgpt", "model": "first-model"}
        return {"provider": "claude", "model": "second-model"}

    dependencies = regeneration.PlanDependencies(
        config=ChangingConfig,
        orchestrator=_Orchestrator,
        runtime_snapshot=changing_snapshot,
        scan_text=lambda text: {"action": "allow", "scanned": text},
        attachment_context=lambda *_args: ("", []),
        decorate_plan=lambda plan: plan,
    )

    plan = regeneration.build_plan(
        {"id": "conversation", "turns": [_completed_turn()]},
        0,
        target="synthesis",
        provider=None,
        dependencies=dependencies,
    )

    assert calls == 1
    assert plan["regeneration"]["provider"] == "chatgpt"
    assert plan["synthesizer"]["name"] == "chatgpt"
    assert plan["synthesizer"]["model"] == "first-model"


def test_original_attempt_is_immutable_and_reused():
    turn = _completed_turn()
    current = turn["answers"]["claude"]
    first = regeneration.ensure_original_attempt(
        turn,
        target_key="answer:claude",
        target="answer",
        provider="claude",
        current=current,
        now=lambda: "2026-02-01T00:00:00.000Z",
    )
    current["text"] = "changed later"
    second = regeneration.ensure_original_attempt(
        turn,
        target_key="answer:claude",
        target="answer",
        provider="claude",
        current=current,
        now=lambda: "2026-03-01T00:00:00.000Z",
    )

    assert second == first
    assert len(turn["attempts"]) == 1
    assert turn["attempts"][0]["result"]["text"] == "old answer"


def test_target_validation_fingerprint_and_interrupt_helpers():
    turn = _completed_turn()
    first = regeneration.fingerprint(
        "conversation",
        "turn",
        target="answer",
        provider="claude",
    )
    assert first == regeneration.fingerprint(
        "conversation",
        "turn",
        target="answer",
        provider="claude",
    )
    assert first != regeneration.fingerprint(
        "conversation",
        "turn",
        target="synthesis",
        provider=None,
    )

    with pytest.raises(regeneration.TargetError) as missing:
        regeneration.resolve_target(
            turn,
            target="answer",
            provider="chatgpt",
            workers=_Config.WORKERS,
            synthesizer="synthesizer",
        )
    assert missing.value.status_code == 404

    attempt = {"status": "running"}
    assert regeneration.interrupt_attempt(
        attempt,
        now="2026-01-02T00:00:00.000Z",
        cancelled=True,
    ) is True
    assert attempt["status"] == "interrupted"
    assert attempt["usage_may_be_incomplete"] is True
    assert regeneration.interrupt_attempt(
        attempt,
        now="2026-01-03T00:00:00.000Z",
        cancelled=False,
    ) is False
