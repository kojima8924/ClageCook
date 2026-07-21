# -*- coding: utf-8 -*-

import pytest
from pydantic import ValidationError

import config
import main
import orchestrator
import planning
from providers import CompletionResult, Provider


def test_provider_specific_output_ceilings_are_intentionally_asymmetric():
    assert config.max_output_tokens_for("claude", "balanced") == 8_192
    assert config.max_output_tokens_for("gemini", "balanced") == 16_384
    assert config.max_output_tokens_for("chatgpt", "high") == 16_384
    assert config.max_output_tokens_for("grok", "low") == 4_096
    assert config.max_output_tokens_for("synthesizer", "high") == 32_768


def test_default_run_ceiling_fits_four_provider_high_debate(monkeypatch):
    monkeypatch.setattr(config, "LIVE_API_ENABLED", False)
    monkeypatch.setattr(config, "INCLUDE_MOCKS_WHEN_MIXED", False)

    plan = planning.build_run_plan(
        message="難しい問題",
        tier="high",
        debate=True,
    )

    assert plan["max_output_tokens"]["total"] == 196_608
    assert plan["limits"]["max_output_tokens_per_run"] == 196_608
    assert plan["limits"]["output_tokens_exceeded"] is False


@pytest.mark.parametrize(
    ("provider", "model", "effective"),
    [
        ("claude", "claude-sonnet-5", "high"),
        ("gemini", "gemini-3.5-flash", "medium"),
        ("chatgpt", "gpt-5.6-terra", "medium"),
        ("grok", "grok-4.5", "high"),
    ],
)
def test_auto_reasoning_is_model_policy_not_prompt_classification(
    provider,
    model,
    effective,
):
    resolution = config.resolve_reasoning(provider, model, "auto")

    assert resolution.requested == "auto"
    assert resolution.effective == effective
    assert resolution.api_effort == effective
    assert resolution.source == "model_policy"
    assert resolution.pinned is True


def test_unknown_and_unsupported_models_do_not_receive_unverified_effort():
    unknown = config.resolve_reasoning("chatgpt", "custom-runtime-model", "high")
    unsupported = config.resolve_reasoning(
        "claude",
        "claude-haiku-4-5-20251001",
        "high",
    )

    assert unknown.api_effort is None
    assert unknown.source == "unknown_model"
    assert unknown.pinned is False
    assert unsupported.api_effort is None
    assert unsupported.source == "model_unsupported"
    assert unsupported.pinned is True


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_explicit_reasoning_effort_is_preserved_for_supported_model(effort):
    resolution = config.resolve_reasoning(
        "chatgpt",
        "gpt-5.6-terra",
        effort,
    )

    assert resolution.requested == effort
    assert resolution.effective == effort
    assert resolution.api_effort == effort
    assert resolution.source == "explicit"
    assert resolution.pinned is True


@pytest.mark.parametrize("effort", ["auto", "low", "medium", "high"])
def test_haiku_never_receives_reasoning_effort(effort):
    resolution = config.resolve_reasoning(
        "claude",
        "claude-haiku-4-5-20251001",
        effort,
    )

    assert resolution.requested == effort
    assert resolution.effective == "provider_default"
    assert resolution.api_effort is None
    assert resolution.source == "model_unsupported"
    assert resolution.pinned is True


def test_known_claude_model_without_effort_contract_uses_provider_default():
    resolution = config.resolve_reasoning(
        "claude",
        "claude-sonnet-4-5-20250929",
        "medium",
    )

    assert resolution.effective == "provider_default"
    assert resolution.api_effort is None
    assert resolution.source == "model_unsupported"
    assert resolution.pinned is True


@pytest.mark.asyncio
async def test_haiku_provider_request_omits_explicit_reasoning_effort(monkeypatch):
    requests = []

    class HaikuProvider(Provider):
        name = "claude"
        model = "claude-haiku-4-5-20251001"

        async def complete(self, request):
            requests.append(request)
            return CompletionResult(
                provider=self.name,
                model=self.model,
                text="回答",
                elapsed_sec=0.01,
            )

    monkeypatch.setattr(config, "get_provider", lambda _name, _tier: HaikuProvider())

    result = await orchestrator._run_provider(
        "claude",
        "質問",
        system="system",
        tier="low",
        reasoning_mode="high",
        round_number=1,
    )

    assert len(requests) == 1
    assert requests[0].reasoning_effort is None
    assert result["reasoning"] == {
        "requested": "high",
        "effective": "provider_default",
        "source": "model_unsupported",
        "pinned": True,
        "policy_version": config.REASONING_POLICY_VERSION,
    }


@pytest.mark.asyncio
async def test_reasoning_is_independent_and_incomplete_output_is_not_continued(
    monkeypatch,
):
    requests = []

    class PartialProvider(Provider):
        name = "chatgpt"
        model = "gpt-5.6-terra"

        async def complete(self, request):
            requests.append(request)
            return CompletionResult(
                provider=self.name,
                model=self.model,
                text="途中までの回答",
                elapsed_sec=0.01,
                finish_reason="incomplete",
                completion_status="incomplete",
                partial=True,
                incomplete_reason="max_output_tokens",
            )

    monkeypatch.setattr(
        config,
        "get_provider",
        lambda _name, _tier: PartialProvider(),
    )

    result = await orchestrator._run_provider(
        "chatgpt",
        "元の質問",
        system="元のsystem prompt",
        tier="low",
        reasoning_mode="high",
        round_number=1,
    )

    assert len(requests) == 1
    assert requests[0].prompt == "元の質問"
    assert requests[0].system == "元のsystem prompt"
    assert requests[0].reasoning_effort == "high"
    assert requests[0].max_output_tokens == 4_096
    assert result["partial"] is True
    assert result["reasoning"]["requested"] == "high"
    assert result["reasoning"]["effective"] == "high"


def test_reasoning_mode_changes_request_identity():
    automatic = main.ChatRequest(message="同じ質問", reasoning_mode="auto")
    high = main.ChatRequest(message="同じ質問", reasoning_mode="high")

    assert main._request_fingerprint(automatic) != main._request_fingerprint(high)


@pytest.mark.parametrize("mode", ["auto", "low", "medium", "high"])
def test_request_contract_accepts_current_reasoning_modes(mode):
    request = main.PlanRequest(message="質問", reasoning_mode=mode)

    assert request.reasoning_mode == mode


@pytest.mark.parametrize("removed_mode", ["standard", "deep"])
def test_request_contract_rejects_removed_reasoning_modes(removed_mode):
    with pytest.raises(ValidationError):
        main.PlanRequest(message="質問", reasoning_mode=removed_mode)


@pytest.mark.parametrize("tier", ["low", "balanced", "high"])
def test_request_contract_accepts_current_model_tiers(tier):
    request = main.PlanRequest(message="質問", tier=tier)

    assert request.tier == tier


def test_request_contract_rejects_unknown_model_tier():
    with pytest.raises(ValidationError):
        main.PlanRequest(message="質問", tier="auto")


def test_public_settings_exposes_current_reasoning_contract():
    assert config.public_settings()["limits"]["reasoning_modes"] == [
        "auto",
        "low",
        "medium",
        "high",
    ]
