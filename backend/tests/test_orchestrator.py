import asyncio

import pytest

import config
import orchestrator
from providers import CompletionResult, Provider, ProviderError


class FakeProvider(Provider):
    def __init__(self, name, calls, *, fail=False, delay=0):
        self.name = name
        self.model = f"{name}-model"
        self.calls = calls
        self.fail = fail
        self.delay = delay

    async def complete(self, request):
        self.calls.append(
            (
                self.name,
                request.system,
                request.prompt,
                request.tier,
                request.prompt_cache_key,
                request.web_search,
            )
        )
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError(f"{self.name} failed")
        return CompletionResult(
            provider=self.name,
            model=self.model,
            text=f"{self.name} answer {len(self.calls)}",
            elapsed_sec=self.delay,
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )


def conversation():
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "title": "test",
        "turns": [],
    }


@pytest.mark.asyncio
async def test_parallel_partial_failure_and_synthesis(monkeypatch):
    calls = []
    monkeypatch.setattr(
        config,
        "get_provider",
        lambda name, tier: FakeProvider(name, calls, fail=name == "gemini", delay=0.001),
    )
    monkeypatch.setattr(
        config,
        "get_synthesizer",
        lambda tier: FakeProvider("synth", calls),
    )
    events = []

    async def emit(event, data):
        events.append((event, data))

    turn = await orchestrator.run_turn(
        conversation(),
        "question",
        orchestrator.TurnOptions(providers=("claude", "gemini", "chatgpt")),
        "request-1",
        emit,
    )
    assert turn["answers"]["gemini"]["ok"] is False
    assert "gemini failed" not in turn["answers"]["gemini"]["error"]
    assert turn["answers"]["claude"]["ok"] is True
    assert turn["synthesis"]["ok"] is True
    assert [event for event, _ in events].count("answer") == 3
    assert events[0][0] == "meta"
    assert events[-1][0] == "synthesis"


@pytest.mark.asyncio
async def test_debate_runs_exactly_one_revision_round(monkeypatch):
    calls = []
    monkeypatch.setattr(
        config, "get_provider", lambda name, tier: FakeProvider(name, calls)
    )
    monkeypatch.setattr(
        config, "get_synthesizer", lambda tier: FakeProvider("synth", calls)
    )
    events = []

    async def emit(event, data):
        events.append((event, data))

    turn = await orchestrator.run_turn(
        conversation(),
        "!debate\nquestion",
        orchestrator.TurnOptions(providers=("claude", "grok")),
        "request-2",
        emit,
    )
    worker_calls = [call for call in calls if call[0] in {"claude", "grok"}]
    assert len(worker_calls) == 4
    assert turn["answers"]["claude"]["round"] == 2
    assert "round1_text" in turn["answers"]["grok"]
    assert turn["answers"]["claude"]["round1_usage"]["total_tokens"] == 15
    assert turn["answers"]["claude"]["round2_usage"]["total_tokens"] == 15
    assert turn["answers"]["claude"]["usage"]["total_tokens"] == 30
    assert [data["status"] for event, data in events if event == "phase" and data["name"] == "debate"] == [
        "started",
        "completed",
    ]


def test_commands_override_structured_controls():
    message, options, help_requested = orchestrator.parse_controls(
        "!high\n!nosynth\n!claude\nhello",
        orchestrator.TurnOptions(tier="low", providers=("grok",)),
    )
    assert message == "hello"
    assert options.tier == "high"
    assert options.synthesize is False
    assert options.providers == ("claude",)
    assert help_requested is False


@pytest.mark.asyncio
async def test_web_search_is_only_enabled_for_initial_answers(monkeypatch):
    calls = []
    monkeypatch.setattr(
        config, "get_provider", lambda name, tier: FakeProvider(name, calls)
    )
    monkeypatch.setattr(
        config, "get_synthesizer", lambda tier: FakeProvider("synth", calls)
    )
    monkeypatch.setattr(config, "WEB_SEARCH_ENABLED", True)

    async def emit(_event, _data):
        return None

    turn = await orchestrator.run_turn(
        conversation(),
        "!web\n!debate\nquestion",
        orchestrator.TurnOptions(providers=("claude", "grok")),
        "web-request-1",
        emit,
    )

    worker_calls = [call for call in calls if call[0] in {"claude", "grok"}]
    assert [call[5] for call in worker_calls].count(True) == 2
    assert [call[5] for call in worker_calls].count(False) == 2
    assert turn["options"]["web_search"] is True


def test_blind_control_anonymizes_peer_and_synthesis_metadata():
    message, options, help_requested = orchestrator.parse_controls(
        "!blind\nquestion",
        orchestrator.TurnOptions(),
    )
    answers = {
        "claude": {"ok": True, "text": "first neutral answer"},
        "chatgpt": {"ok": True, "text": "second neutral answer"},
    }
    aliases = orchestrator._blind_aliases(list(answers), "blind-run-1")

    debate_prompt = orchestrator._debate_prompt("claude", answers, aliases)
    synthesis_prompt = orchestrator._synthesis_prompt("question", answers, aliases)

    assert message == "question"
    assert options.blind is True
    assert help_requested is False
    assert set(aliases.values()) == {"回答A", "回答B"}
    assert "claude" not in debate_prompt
    assert "chatgpt" not in debate_prompt
    assert "claude" not in synthesis_prompt
    assert "chatgpt" not in synthesis_prompt
    assert "回答A" in synthesis_prompt
    assert "回答B" in synthesis_prompt
    assert aliases == orchestrator._blind_aliases(list(answers), "blind-run-1")


def test_history_is_bounded(monkeypatch):
    monkeypatch.setattr(config, "HISTORY_MAX_CHARS", 120)
    conv = conversation()
    conv["turns"] = [
        {
            "message": "old question " + "x" * 100,
            "synthesis": {"text": "old answer " + "y" * 100},
        },
        {"message": "recent", "synthesis": {"text": "recent answer"}},
    ]
    text = orchestrator._history_text(conv)
    assert len(text) <= 140
    assert "recent answer" in text
    assert "古い履歴を省略" in text


def test_history_excludes_pending_turn_before_applying_turn_limit(monkeypatch):
    monkeypatch.setattr(config, "HISTORY_TURNS", 1)
    conv = conversation()
    conv["turns"] = [
        {
            "message": "completed question",
            "synthesis": {"text": "completed answer"},
            "status": "completed",
        },
        {
            "message": "must not be duplicated",
            "synthesis": {"pending": True},
            "status": "running",
        },
    ]

    text = orchestrator._history_text(conv)

    assert "completed question" in text
    assert "completed answer" in text
    assert "must not be duplicated" not in text


@pytest.mark.asyncio
async def test_prompt_cache_key_is_stable_and_does_not_contain_conversation_id(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        config,
        "get_provider",
        lambda name, _tier: FakeProvider(name, calls),
    )

    async def emit(_event, _data):
        return None

    conv = conversation()
    options = orchestrator.TurnOptions(
        providers=("grok",),
        synthesize=False,
    )
    await orchestrator.run_turn(conv, "first", options, "request-a", emit)
    await orchestrator.run_turn(conv, "second", options, "request-b", emit)

    cache_keys = [call[4] for call in calls]
    assert len(cache_keys) == 2
    assert cache_keys[0] == cache_keys[1]
    assert cache_keys[0].startswith("clage-")
    assert conv["id"] not in cache_keys[0]


@pytest.mark.asyncio
async def test_provider_error_audit_is_saved_without_raw_exception(monkeypatch):
    class AuditFailureProvider(Provider):
        name = "chatgpt"
        model = "test"

        async def complete(self, _request):
            raise ProviderError(
                "chatgpt: APIへの接続がタイムアウトしました",
                request_audit={
                    "http_attempts": 1,
                    "retry_count": 0,
                    "outcome": "timeout",
                    "usage_may_be_incomplete": True,
                },
            ) from RuntimeError("raw-secret-exception")

    monkeypatch.setattr(
        config,
        "get_provider",
        lambda _name, _tier: AuditFailureProvider(),
    )

    result = await orchestrator._run_provider(
        "chatgpt",
        "question",
        system="system",
        tier="balanced",
        round_number=1,
    )

    assert result["ok"] is False
    assert result["request_audit"]["http_attempts"] == 1
    assert result["request_audit"]["outcome"] == "timeout"
    assert result["usage_may_be_incomplete"] is True
    assert "raw-secret-exception" not in result["error"]


def test_billing_or_credit_error_uses_fixed_public_message():
    raw_vendor_message = "vendor credit detail with raw-secret"
    error = ProviderError(
        raw_vendor_message,
        status_code=400,
        error_code="billing_or_credit_required",
        request_audit={
            "http_attempts": 1,
            "retry_count": 0,
            "outcome": "http_error",
            "final_http_status": 400,
            "usage_may_be_incomplete": False,
        },
    )

    result = orchestrator._failure_result(
        error,
        source="claude",
        round_number=1,
    )

    assert result["error_code"] == "billing_or_credit_required"
    assert result["error"] == (
        "プロバイダの請求設定またはクレジット残高を確認してください"
    )
    assert raw_vendor_message not in result["error"]


def test_outbound_defense_redacts_secrets_from_saved_history():
    secret = "sk-proj-" + "z" * 32
    text = f"[保存済み履歴]\n{secret}\n[今回]\n安全な質問"

    outbound = orchestrator._safe_outbound_text(text)

    assert secret not in outbound
    assert "REDACTED:openai_api_key" in outbound


def test_outbound_defense_does_not_redact_contact_details():
    text = "連絡先 foo@example.com について整理する"

    assert orchestrator._safe_outbound_text(text) == text


def test_outbound_defense_redacts_contact_details_in_history_or_peer_text():
    text = "連絡先 foo@example.com について整理する"

    outbound = orchestrator._safe_outbound_text(text, redact_confirm=True)

    assert "foo@example.com" not in outbound
    assert "REDACTED:email_address" in outbound


@pytest.mark.asyncio
async def test_unconfigured_provider_cannot_be_forced_into_mixed_mode(monkeypatch):
    for key in (
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "configured")
    monkeypatch.setattr(config, "LIVE_API_ENABLED", True)
    monkeypatch.setattr(config, "INCLUDE_MOCKS_WHEN_MIXED", False)

    async def emit(_event, _data):
        return None

    with pytest.raises(ValueError, match="参加するAIがありません"):
        await orchestrator.run_turn(
            conversation(),
            "question",
            orchestrator.TurnOptions(providers=("claude",)),
            "request-mixed",
            emit,
        )


@pytest.mark.asyncio
async def test_cancelling_turn_collects_provider_tasks(monkeypatch):
    started = 0
    all_started = asyncio.Event()
    cancelled = []

    class BlockingProvider(Provider):
        def __init__(self, name):
            self.name = name
            self.model = f"{name}-model"

        async def complete(self, _request):
            nonlocal started
            started += 1
            if started == 2:
                all_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.append(self.name)
                raise

    monkeypatch.setattr(config, "active_workers", lambda: ["claude", "grok"])
    monkeypatch.setattr(
        config,
        "get_provider",
        lambda name, _tier: BlockingProvider(name),
    )

    async def emit(_event, _data):
        return None

    task = asyncio.create_task(
        orchestrator.run_turn(
            conversation(),
            "question",
            orchestrator.TurnOptions(providers=("claude", "grok")),
            "request-cancel",
            emit,
        )
    )
    await asyncio.wait_for(all_started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert set(cancelled) == {"claude", "grok"}
