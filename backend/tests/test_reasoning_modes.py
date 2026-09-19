# -*- coding: utf-8 -*-

import pytest
from pydantic import ValidationError

import config
import main
import model_capabilities
import orchestrator
import planning
from providers import CompletionResult, Provider
from providers import anthropic as anthropic_provider


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
    resolution = config.resolve_reasoning(provider, model, "auto", tier="balanced")

    assert resolution.requested == "auto"
    assert resolution.effective == effective
    assert resolution.api_effort == effective
    assert resolution.source == "model_policy"
    assert resolution.pinned is True


def test_unknown_and_unsupported_models_do_not_receive_unverified_effort():
    unknown = config.resolve_reasoning(
        "chatgpt",
        "custom-runtime-model",
        "high",
        tier="balanced",
    )
    unsupported = config.resolve_reasoning(
        "claude",
        "claude-haiku-4-5-20251001",
        "high",
        tier="balanced",
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
        tier="balanced",
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
        tier="balanced",
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
        tier="balanced",
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


@pytest.mark.parametrize(
    "prefix",
    [
        prefix
        for prefix, capabilities in model_capabilities.CLAUDE_MODEL_CAPABILITIES
        if "effort" in capabilities
    ],
)
def test_claude_effort_table_is_the_single_source_for_plan_and_payload(prefix):
    """issue #21-1: planのpinned effortと実送信のoutput_configが乖離しない。

    config側のAUTO policyとproviders/anthropic側のpayload組み立てが、同じ
    model_capabilities表だけを見ていることをprefixごとに突き合わせる。片方に
    modelを足し忘れると「planはeffortを表示するのに実送信では黙って落ちる」に
    なるため、表の全行をここで固定する。
    """
    model = f"{prefix}-20260101"

    resolution = config.resolve_reasoning("claude", model, "auto", tier="high")

    assert resolution.api_effort is not None
    assert resolution.pinned is True
    # 実送信側も同じ表からprefixを導出しているか
    assert model_capabilities.matches_model_prefix(
        model,
        anthropic_provider._EFFORT_MODEL_PREFIXES,
    )
    assert (
        anthropic_provider._EFFORT_MODEL_PREFIXES
        == model_capabilities.CLAUDE_EFFORT_MODEL_PREFIXES
    )


def test_claude_model_outside_the_capability_table_is_not_pinned_anywhere():
    """表に無いClaude modelは、planでも実送信でもeffortを付けない。"""
    model = "claude-sonnet-4-5-20250929"

    resolution = config.resolve_reasoning("claude", model, "auto", tier="high")

    assert resolution.api_effort is None
    assert not model_capabilities.matches_model_prefix(
        model,
        anthropic_provider._EFFORT_MODEL_PREFIXES,
    )


@pytest.mark.parametrize(
    ("provider", "model"),
    [("claude", "claude-sonnet-5"), ("grok", "grok-4.5")],
)
def test_auto_effort_is_lowered_when_the_tier_output_ceiling_is_tight(
    provider,
    model,
):
    """issue #21-2: 生成枠の狭いtierでAUTOがhighを選ぶと本文が切れる。

    tier=lowでも、runtime設定でeffort対応modelを選べばAUTOはhighを選ぶ。
    thinkingが4,096の枠を食って本文がmax_tokensで切れるのがこの組合せ。
    """
    assert config.max_output_tokens_for(provider, "low") < 8_192

    low = config.resolve_reasoning(provider, model, "auto", tier="low")
    balanced = config.resolve_reasoning(provider, model, "auto", tier="balanced")

    assert low.effective == "medium"
    assert low.api_effort == "medium"
    assert low.source == "model_policy_output_capped"
    assert low.pinned is True
    # balanced以上は引き下げない(枠が本文に足りる)
    assert balanced.effective == "high"
    assert balanced.source == "model_policy"


@pytest.mark.parametrize("provider", ["chatgpt"])
def test_auto_effort_below_the_cap_is_left_untouched(provider):
    """AUTOがもともとmedium以下のproviderは、狭いtierでも調整しない。"""
    resolution = config.resolve_reasoning(
        provider,
        config.DEFAULT_MODELS[provider]["low"],
        "auto",
        tier="low",
    )

    assert resolution.effective == "medium"
    assert resolution.source == "model_policy"


def test_explicit_high_effort_is_not_silently_lowered_on_a_tight_tier():
    """利用者が明示したeffortは、枠が狭くても黙って落とさない。"""
    resolution = config.resolve_reasoning(
        "claude",
        "claude-sonnet-5",
        "high",
        tier="low",
    )

    assert resolution.effective == "high"
    assert resolution.source == "explicit"


def test_raising_the_low_tier_ceiling_restores_the_auto_high_effort(monkeypatch):
    """lowの生成枠を広げた利用者からはAUTOのhighを取り上げない。"""
    monkeypatch.setitem(config.MAX_OUTPUT_TOKENS["claude"], "low", 32_768)

    resolution = config.resolve_reasoning(
        "claude",
        "claude-sonnet-5",
        "auto",
        tier="low",
    )

    assert resolution.effective == "high"
    assert resolution.source == "model_policy"
