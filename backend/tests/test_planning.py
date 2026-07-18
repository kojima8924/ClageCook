import asyncio
from collections import defaultdict
from copy import deepcopy

from fastapi.testclient import TestClient

import config
import main
import planning
from storage import ConversationStore


def _clear_keys(monkeypatch, *, live=True):
    for name in (
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "INCLUDE_MOCKS_WHEN_MIXED", False)
    monkeypatch.setattr(config, "LIVE_API_ENABLED", live)


def _warning_codes(plan):
    return {warning["code"] for warning in plan["warnings"]}


def test_mock_debate_plan_is_free_and_uses_safe_upper_bound(monkeypatch):
    _clear_keys(monkeypatch, live=False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("planning must not construct a Provider")

    monkeypatch.setattr(config, "get_provider", forbidden)
    monkeypatch.setattr(config, "get_synthesizer", forbidden)
    plan = planning.build_run_plan(message="question", debate=True)

    assert plan["allowed"] is True
    assert plan["billable"] is False
    assert [provider["name"] for provider in plan["providers"]] == list(
        config.WORKERS
    )
    assert {provider["mode"] for provider in plan["providers"]} == {"mock"}
    assert {provider["model"] for provider in plan["providers"]} == {"mock"}
    assert plan["synthesizer"]["mode"] == "mock"
    assert plan["calls"] == {
        "answers": 4,
        "debate": 4,
        "synthesis": 1,
        "total": 9,
    }
    assert plan["max_output_tokens"] == {
        "per_call": 2400,
        "answers": 9600,
        "debate": 9600,
        "synthesis": 2400,
        "total": 21600,
        "live_total": 0,
    }


def test_live_api_gate_keeps_configured_keys_in_free_mock_mode(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "configured-but-locked")
    monkeypatch.setattr(config, "LIVE_API_ENABLED", False)

    plan = planning.build_run_plan(message="question")

    assert plan["mode"] == "mock"
    assert plan["billable"] is False
    assert {provider["mode"] for provider in plan["providers"]} == {"mock"}
    assert config.public_settings()["live_api_enabled"] is False


def test_blind_command_is_reflected_without_extra_calls(monkeypatch):
    _clear_keys(monkeypatch, live=False)

    regular = planning.build_run_plan(message="question", debate=True)
    blind = planning.build_run_plan(message="!blind\nquestion", debate=True)

    assert blind["options"]["blind"] is True
    assert blind["calls"] == regular["calls"]
    assert blind["max_output_tokens"] == regular["max_output_tokens"]


def test_inline_controls_and_disabled_provider_match_runtime(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.setenv("CHATGPT_MODEL_HIGH", "test-high-model")

    plan = planning.build_run_plan(
        message="!high\n!debate\nquestion",
        tier="low",
        providers=["claude", "chatgpt"],
    )

    assert plan["options"]["tier"] == "high"
    assert plan["options"]["debate_requested"] is True
    assert plan["options"]["debate_effective"] is False
    assert plan["unavailable_providers"] == ["claude"]
    assert plan["providers"] == [
        {
            "name": "chatgpt",
            "label": "ChatGPT",
            "mode": "live",
            "model": "test-high-model",
            "billable": True,
            "max_calls": 1,
        }
    ]
    assert plan["calls"]["total"] == 1
    assert plan["billable"] is True
    assert {
        "providers_unavailable",
        "debate_skipped",
        "synthesis_skipped",
        "billable_live_api",
    }.issubset(_warning_codes(plan))


def test_live_synthesizer_makes_mock_participant_plan_billable(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.setattr(config, "INCLUDE_MOCKS_WHEN_MIXED", True)

    plan = planning.build_run_plan(
        message="question",
        providers=["claude", "gemini"],
    )

    assert {provider["mode"] for provider in plan["providers"]} == {"mock"}
    assert plan["synthesizer"]["name"] == "chatgpt"
    assert plan["synthesizer"]["mode"] == "live"
    assert plan["calls"]["total"] == 3
    assert plan["billable"] is True
    assert plan["max_output_tokens"]["live_total"] == 2400


def test_plan_uses_one_atomic_runtime_settings_snapshot(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-openai-key")
    first = {
        "models": {
            "claude": {"balanced": "claude-worker-first"},
            "chatgpt": {"balanced": "chatgpt-worker-first"},
        },
        "synthesizer_provider": "claude",
        "synthesizer_models": {"balanced": "claude-synth-first"},
    }
    second = {
        "models": {
            "claude": {"balanced": "claude-worker-second"},
            "chatgpt": {"balanced": "chatgpt-worker-second"},
        },
        "synthesizer_provider": "chatgpt",
        "synthesizer_models": {"balanced": "chatgpt-synth-second"},
    }
    calls = 0

    def changing_snapshot():
        nonlocal calls
        snapshot = first if calls == 0 else second
        calls += 1
        return deepcopy(snapshot)

    monkeypatch.setattr(config.runtime_settings, "snapshot", changing_snapshot)

    plan = planning.build_run_plan(
        message="question",
        providers=["claude", "chatgpt"],
    )

    assert calls == 1
    assert {item["name"]: item["model"] for item in plan["providers"]} == {
        "claude": "claude-worker-first",
        "chatgpt": "chatgpt-worker-first",
    }
    assert plan["synthesizer"]["name"] == "claude"
    assert plan["synthesizer"]["model"] == "claude-synth-first"


def test_plan_endpoint_never_calls_api_and_help_is_local(monkeypatch):
    _clear_keys(monkeypatch)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("planning must not construct a Provider")

    monkeypatch.setattr(config, "get_provider", forbidden)
    monkeypatch.setattr(config, "get_synthesizer", forbidden)
    monkeypatch.setattr(config, "AUTH_TOKEN", "")
    response = TestClient(main.app).post(
        "/api/plan",
        json={"message": "!help", "tier": "high", "debate": True},
    )

    assert response.status_code == 200
    plan = response.json()
    assert plan["options"]["help_requested"] is True
    assert plan["providers"] == []
    assert plan["calls"]["total"] == 0
    assert plan["max_output_tokens"]["total"] == 0
    assert plan["billable"] is False


def test_chat_rejects_call_limit_before_creating_conversation(
    tmp_path, monkeypatch
):
    _clear_keys(monkeypatch, live=False)
    monkeypatch.setattr(main, "store", ConversationStore(tmp_path))
    monkeypatch.setattr(main, "_registry", main.RunRegistry())
    monkeypatch.setattr(main, "_rate_limiter", main.SlidingWindowLimiter(100))
    monkeypatch.setattr(main, "_run_slots", asyncio.Semaphore(8))
    monkeypatch.setattr(main, "_conversation_locks", defaultdict(asyncio.Lock))
    monkeypatch.setattr(config, "AUTH_TOKEN", "")
    monkeypatch.setattr(config, "MAX_PROVIDER_CALLS_PER_RUN", 8)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_PER_RUN", 1_000_000)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("orchestrator must not run over the limit")

    monkeypatch.setattr(main.orchestrator, "run_turn", forbidden)
    payload = {"message": "question", "debate": True}
    client = TestClient(main.app)
    preflight = client.post("/api/plan", json=payload)
    rejected = client.post("/api/chat", json=payload)

    assert preflight.status_code == 200
    assert preflight.json()["allowed"] is False
    assert preflight.json()["limits"]["provider_calls_exceeded"] is True
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "run_limit_exceeded"
    assert rejected.json()["detail"]["plan"] == preflight.json()
    assert main.store.list() == []
    assert main._rate_limiter._entries == {}


def test_chat_rejects_output_token_budget(monkeypatch):
    _clear_keys(monkeypatch, live=False)
    monkeypatch.setattr(config, "AUTH_TOKEN", "")
    monkeypatch.setattr(config, "MAX_PROVIDER_CALLS_PER_RUN", 100)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_PER_RUN", 7_199)
    response = TestClient(main.app).post(
        "/api/plan",
        json={"message": "question", "providers": ["claude", "grok"]},
    )

    assert response.status_code == 200
    plan = response.json()
    assert plan["calls"]["total"] == 3
    assert plan["max_output_tokens"]["total"] == 7_200
    assert plan["limits"]["output_tokens_exceeded"] is True
    assert "output_token_limit_exceeded" in _warning_codes(plan)


def test_plan_endpoint_uses_selected_conversation_history(tmp_path, monkeypatch):
    _clear_keys(monkeypatch, live=False)
    monkeypatch.setattr(main, "store", ConversationStore(tmp_path))
    monkeypatch.setattr(config, "AUTH_TOKEN", "")
    conversation = main.store.create("first")
    conversation["turns"].append(
        {
            "message": "previous question",
            "answers": {},
            "synthesis": {"ok": True, "text": "previous answer"},
        }
    )
    main.store.save(conversation)
    client = TestClient(main.app)

    fresh = client.post("/api/plan", json={"message": "next"}).json()
    continued = client.post(
        "/api/plan",
        json={"message": "next", "conversation_id": conversation["id"]},
    ).json()

    assert continued["input_envelope"]["history"] > 0
    assert continued["input_envelope"]["total"] > fresh["input_envelope"]["total"]


def test_public_settings_reports_per_run_limits(monkeypatch):
    monkeypatch.setattr(config, "MAX_PROVIDER_CALLS_PER_RUN", 7)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_PER_RUN", 12_345)
    monkeypatch.setattr(config, "MAX_INPUT_BYTES_PER_RUN", 54_321)
    limits = config.public_settings()["limits"]
    assert config.public_settings()["single_process_enforced"] is True
    assert limits["max_provider_calls_per_run"] == 7
    assert limits["max_output_tokens_per_run"] == 12_345
    assert limits["max_input_bytes_per_run"] == 54_321


def test_live_retry_attempts_are_included_in_safety_limits(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.setattr(config, "HTTP_RETRIES", 2)
    monkeypatch.setattr(config, "MAX_PROVIDER_CALLS_PER_RUN", 2)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_PER_RUN", 1_000_000)

    plan = planning.build_run_plan(
        message="question",
        providers=["chatgpt"],
        synthesize=False,
    )

    assert plan["calls"]["total"] == 1
    assert plan["retry_envelope"]["additional_http_attempts"] == 2
    assert plan["retry_envelope"]["total_provider_executions"] == 3
    assert plan["allowed"] is False
    assert plan["limits"]["provider_calls_exceeded"] is True


def test_input_envelope_includes_history_debate_synthesis_and_retry(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.setattr(config, "INCLUDE_MOCKS_WHEN_MIXED", True)
    monkeypatch.setattr(config, "HTTP_RETRIES", 1)
    monkeypatch.setattr(config, "MAX_PROVIDER_CALLS_PER_RUN", 100)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_PER_RUN", 1_000_000)
    monkeypatch.setattr(config, "MAX_INPUT_BYTES_PER_RUN", 100_000_000)

    without_history = planning.build_run_plan(
        message="question",
        providers=["claude", "chatgpt"],
        debate=True,
    )
    with_history = planning.build_run_plan(
        message="question",
        providers=["claude", "chatgpt"],
        debate=True,
        history_text="保存履歴" * 100,
    )

    envelope = with_history["input_envelope"]
    assert envelope["unit"] == "utf8_bytes"
    assert envelope["token_count_estimated"] is False
    assert envelope["history"] > 0
    assert envelope["debate_total"] > 0
    assert envelope["synthesis"] > 0
    assert envelope["live_with_retries"] > envelope["live_initial_total"]
    assert envelope["total"] > without_history["input_envelope"]["total"]


def test_input_envelope_limit_blocks_before_execution(monkeypatch):
    _clear_keys(monkeypatch, live=False)
    monkeypatch.setattr(config, "MAX_PROVIDER_CALLS_PER_RUN", 100)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_PER_RUN", 1_000_000)
    monkeypatch.setattr(config, "MAX_INPUT_BYTES_PER_RUN", 1_024)

    plan = planning.build_run_plan(
        message="question",
        debate=True,
        history_text="large history " * 200,
    )

    assert plan["allowed"] is False
    assert plan["limits"]["input_bytes_exceeded"] is True
    assert "input_byte_limit_exceeded" in plan["block_reasons"]


def test_live_without_keys_is_rejected_during_planning(monkeypatch):
    _clear_keys(monkeypatch, live=True)

    plan = planning.build_run_plan(message="question")

    assert plan["mode"] == "mixed"
    assert plan["allowed"] is False
    assert plan["providers"] == []
    assert "invalid_request" in plan["block_reasons"]
    assert "no_effective_providers" in _warning_codes(plan)


def test_web_control_is_explicit_and_plan_discloses_non_strict_limits(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.setattr(config, "WEB_SEARCH_ENABLED", True)

    plan = planning.build_run_plan(
        message="!web\ncurrent question",
        providers=["chatgpt"],
        synthesize=False,
    )

    assert plan["allowed"] is True
    assert plan["options"]["web_search_requested"] is True
    assert plan["options"]["web_search_effective"] is True
    assert plan["web_search"]["providers"] == [
        {
            "name": "chatgpt",
            "enabled": True,
            "tool": "web_search",
            "hard_max_uses": None,
        }
    ]
    assert plan["web_search"]["strict_total_limit"] is False
    assert "web_search_exact_limit_unknown" in _warning_codes(plan)


def test_disabled_web_search_blocks_before_dispatch(monkeypatch):
    monkeypatch.setattr(config, "WEB_SEARCH_ENABLED", False)
    plan = planning.build_run_plan(message="question", web_search=True)

    assert plan["allowed"] is False
    assert "web_search_disabled" in plan["block_reasons"]
