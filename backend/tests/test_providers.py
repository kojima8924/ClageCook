import json
from datetime import datetime, timezone

import httpx
import pytest

import config
from providers import CompletionRequest, ProviderError
from providers.base import HttpProvider, normalized_quota_snapshot, normalized_usage
from providers.anthropic import AnthropicProvider
from providers.gemini import GeminiProvider
from providers.mock import MockProvider
from providers.openai import OpenAIProvider
from providers.xai import XAIProvider


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_usage_fallback_does_not_double_count_cached_input_subset():
    usage = normalized_usage(
        {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 20},
            "output_tokens": 10,
            "flag": True,
        }
    )

    assert usage["cached_input_tokens"] == 20
    assert usage["total_tokens"] == 110
    assert normalized_usage({"input_tokens": True}) == {}


def test_openai_quota_headers_are_allowlisted_and_normalized():
    snapshot = normalized_quota_snapshot(
        "chatgpt",
        httpx.Headers(
            {
                "x-ratelimit-limit-requests": "500",
                "x-ratelimit-remaining-requests": "499",
                "x-ratelimit-reset-requests": "2s",
                "x-ratelimit-limit-tokens": "30000",
                "x-ratelimit-remaining-tokens": "29900",
                "authorization": "Bearer must-not-leak",
            }
        ),
        observed_at="2026-07-18T00:00:00.000Z",
    )
    assert snapshot["dimensions"]["requests"] == {
        "limit": 500,
        "remaining": 499,
        "reset": "2s",
    }
    assert snapshot["dimensions"]["tokens"]["remaining"] == 29900
    assert "authorization" not in str(snapshot).lower()
    assert "must-not-leak" not in str(snapshot)


def test_unknown_or_invalid_quota_headers_do_not_become_zero():
    assert normalized_quota_snapshot(
        "gemini", httpx.Headers({"x-ratelimit-remaining": "0"})
    ) == {}
    snapshot = normalized_quota_snapshot(
        "claude",
        httpx.Headers(
            {
                "anthropic-ratelimit-requests-limit": "not-a-number",
                "anthropic-ratelimit-requests-remaining": "7",
                "anthropic-ratelimit-requests-reset": "bad value with spaces",
            }
        ),
        observed_at="2026-07-18T00:00:00.000Z",
    )
    assert snapshot["dimensions"]["requests"] == {
        "limit": None,
        "remaining": 7,
        "reset": None,
    }


def test_openai_compatible_completion_usage_does_not_double_count_reasoning():
    usage = normalized_usage(
        {
            "prompt_tokens": 32,
            "completion_tokens": 9,
            "completion_tokens_details": {"reasoning_tokens": 110},
            "total_tokens": 151,
        }
    )

    assert usage["input_tokens"] == 32
    assert usage["output_tokens"] == 9
    assert usage["reasoning_tokens"] == 110
    assert "billable_output_tokens" not in usage
    assert usage["total_tokens"] == 151


@pytest.mark.asyncio
async def test_anthropic_messages_contract():
    def handler(request: httpx.Request):
        assert request.url == "https://api.anthropic.com/v1/messages"
        assert request.headers["x-api-key"] == "secret"
        body = json.loads(request.content)
        assert body["model"] == "claude-test"
        assert body["system"] == "system"
        return httpx.Response(
            200,
            json={
                "model": "claude-test",
                "content": [{"type": "text", "text": "Claude answer"}],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 4,
                    "output_tokens": 2,
                    "cache_creation_input_tokens": 3,
                    "cache_read_input_tokens": 5,
                },
            },
        )

    async with _client(handler) as client:
        provider = AnthropicProvider(
            name="claude", model="claude-test", api_key="secret", client=client
        )
        result = await provider.complete(
            CompletionRequest(prompt="hello", system="system", max_output_tokens=100)
        )
    assert result.text == "Claude answer"
    assert result.usage == {
        "input_tokens": 4,
        "output_tokens": 2,
        "cache_creation_input_tokens": 3,
        "cached_input_tokens": 5,
        "total_tokens": 14,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "tier", "effort"),
    [
        ("claude-sonnet-5", "balanced", "medium"),
        ("claude-opus-4-8", "high", "high"),
    ],
)
async def test_anthropic_current_models_use_adaptive_thinking(
    model,
    tier,
    effort,
):
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "adaptive"}
        assert body["output_config"] == {"effort": effort}
        assert "budget_tokens" not in json.dumps(body)
        return httpx.Response(
            200,
            json={
                "model": model,
                "content": [{"type": "text", "text": "answer"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        )

    async with _client(handler) as client:
        result = await AnthropicProvider(
            name="claude", model=model, api_key="secret", client=client
        ).complete(
            CompletionRequest(
                prompt="hello",
                tier=tier,
                reasoning_effort=effort,
            )
        )

    assert result.text == "answer"


@pytest.mark.asyncio
async def test_anthropic_always_on_thinking_model_uses_effort_without_field():
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        assert body["output_config"] == {"effort": "high"}
        assert "thinking" not in body
        return httpx.Response(
            200,
            json={
                "model": "claude-fable-5",
                "content": [{"type": "text", "text": "answer"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        )

    async with _client(handler) as client:
        result = await AnthropicProvider(
            name="claude",
            model="claude-fable-5",
            api_key="secret",
            client=client,
        ).complete(
            CompletionRequest(
                prompt="hello",
                tier="low",
                reasoning_effort="high",
            )
        )

    assert result.text == "answer"


@pytest.mark.asyncio
async def test_anthropic_haiku_uses_basic_search_without_unsupported_thinking():
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        assert "thinking" not in body
        assert "output_config" not in body
        assert body["tools"] == [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3,
            }
        ]
        return httpx.Response(
            200,
            json={
                "model": "claude-haiku-4-5-20251001",
                "content": [{"type": "text", "text": "answer"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        )

    async with _client(handler) as client:
        result = await AnthropicProvider(
            name="claude",
            model="claude-haiku-4-5-20251001",
            api_key="secret",
            client=client,
        ).complete(CompletionRequest(prompt="hello", tier="low", web_search=True))

    assert result.text == "answer"


@pytest.mark.asyncio
async def test_openai_responses_contract_and_store_false():
    def handler(request: httpx.Request):
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer secret"
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["instructions"] == "system"
        assert body["reasoning"] == {"effort": "high"}
        return httpx.Response(
            200,
            json={
                "model": "gpt-test",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "OpenAI answer"}],
                    }
                ],
                "usage": {
                    "input_tokens": 5,
                    "input_tokens_details": {"cached_tokens": 2},
                    "output_tokens": 3,
                    "output_tokens_details": {"reasoning_tokens": 1},
                    "total_tokens": 8,
                },
            },
        )

    async with _client(handler) as client:
        provider = OpenAIProvider(
            name="chatgpt", model="gpt-test", api_key="secret", client=client
        )
        result = await provider.complete(
            CompletionRequest(
                prompt="hello",
                system="system",
                tier="high",
                reasoning_effort="high",
            )
        )
    assert result.text == "OpenAI answer"
    assert result.usage["total_tokens"] == 8
    assert result.usage["cached_input_tokens"] == 2
    assert result.usage["reasoning_tokens"] == 1
    assert result.completion_status == "completed"
    assert result.partial is False
    assert result.request_audit == {
        "http_attempts": 1,
        "retry_count": 0,
        "outcome": "response_received",
        "usage_may_be_incomplete": False,
        "final_http_status": 200,
    }


@pytest.mark.asyncio
async def test_gemini_interactions_contract():
    def handler(request: httpx.Request):
        assert request.url == "https://generativelanguage.googleapis.com/v1/interactions"
        assert request.headers["x-goog-api-key"] == "secret"
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["generation_config"]["thinking_level"] == "low"
        return httpx.Response(
            200,
            json={
                "model": "gemini-test",
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": "Gemini answer"}],
                    }
                ],
                "usage": {
                    "total_input_tokens": 7,
                    "total_output_tokens": 4,
                    "total_cached_tokens": 2,
                    "total_thought_tokens": 3,
                    "total_tool_use_tokens": 1,
                    "total_tokens": 14,
                },
            },
        )

    async with _client(handler) as client:
        provider = GeminiProvider(
            name="gemini", model="gemini-test", api_key="secret", client=client
        )
        result = await provider.complete(
            CompletionRequest(
                prompt="hello",
                tier="low",
                reasoning_effort="low",
            )
        )
    assert result.text == "Gemini answer"
    assert result.usage == {
        "input_tokens": 7,
        "output_tokens": 4,
        "cached_input_tokens": 2,
        "reasoning_tokens": 3,
        "billable_output_tokens": 7,
        "tool_tokens": 1,
        "total_tokens": 14,
    }


@pytest.mark.asyncio
async def test_xai_responses_contract():
    def handler(request: httpx.Request):
        assert request.url == "https://api.x.ai/v1/responses"
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["reasoning"] == {"effort": "medium"}
        assert body["input"][0]["role"] == "system"
        return httpx.Response(
            200,
            json={
                "model": "grok-test",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Grok answer"}],
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "cost_in_usd_ticks": 1234,
                    "num_sources_used": 0,
                    "num_server_side_tools_used": 0,
                },
            },
        )

    async with _client(handler) as client:
        provider = XAIProvider(
            name="grok", model="grok-test", api_key="secret", client=client
        )
        result = await provider.complete(
            CompletionRequest(
                prompt="hello",
                system="system",
                tier="balanced",
                reasoning_effort="medium",
            )
        )
    assert result.text == "Grok answer"
    assert result.usage["total_tokens"] == 5
    assert result.usage["cost_in_usd_ticks"] == 1234
    assert result.usage["sources_used"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_factory", "expected_tool"),
    [
        (
            lambda client: AnthropicProvider(
                name="claude",
                model="claude-opus-4-8",
                api_key="secret",
                client=client,
            ),
            {"type": "web_search_20260318", "name": "web_search", "max_uses": 3},
        ),
        (
            lambda client: OpenAIProvider(
                name="chatgpt", model="gpt-test", api_key="secret", client=client
            ),
            {"type": "web_search", "search_context_size": "medium"},
        ),
        (
            lambda client: GeminiProvider(
                name="gemini", model="gemini-test", api_key="secret", client=client
            ),
            {"type": "google_search"},
        ),
        (
            lambda client: XAIProvider(
                name="grok", model="grok-test", api_key="secret", client=client
            ),
            {"type": "web_search"},
        ),
    ],
)
async def test_web_search_payloads_are_explicit_and_capped_where_supported(
    provider_factory,
    expected_tool,
):
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        assert body["tools"] == [expected_tool]
        if "api.x.ai" in str(request.url):
            assert body["max_turns"] == 3
        if "api.openai.com" in str(request.url):
            assert body["tool_choice"] == "auto"
        if "anthropic.com" in str(request.url):
            response = {
                "model": "claude-test",
                "content": [
                    {
                        "type": "text",
                        "text": "answer",
                        "citations": [
                            {"url": "https://example.com/a", "title": "Example"}
                        ],
                    }
                ],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 1,
                    "server_tool_use": {"web_search_requests": 2},
                },
            }
        elif "generativelanguage" in str(request.url):
            response = {
                "model": "gemini-test",
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [
                            {
                                "type": "text",
                                "text": "answer",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "url": "https://example.com/a",
                                        "title": "Example",
                                        "start_index": 0,
                                        "end_index": 6,
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "usage": {
                    "total_input_tokens": 2,
                    "total_output_tokens": 1,
                    "grounding_tool_count": [
                        {"type": "google_search", "count": 2}
                    ],
                },
            }
        else:
            response = {
                "model": "response-test",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "answer",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "url": "https://example.com/a",
                                        "title": "Example",
                                        "start_index": 0,
                                        "end_index": 6,
                                    },
                                    {
                                        "type": "url_citation",
                                        "url": "javascript:alert(1)",
                                        "title": "unsafe",
                                    },
                                ],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            }
        return httpx.Response(200, json=response)

    async with _client(handler) as client:
        result = await provider_factory(client).complete(
            CompletionRequest(prompt="current facts", web_search=True)
        )

    expected = {"url": "https://example.com/a", "title": "Example"}
    if result.provider != "claude":
        expected.update({"start_index": 0, "end_index": 6})
    assert result.web_search_requested is True
    assert result.citations == [expected]
    if result.provider in {"claude", "gemini"}:
        assert result.usage["web_search_requests"] == 2


@pytest.mark.asyncio
async def test_retry_audit_marks_prior_attempt_usage_unknown(monkeypatch):
    calls = 0

    def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": {"message": "do not reflect"}})
        return httpx.Response(
            200,
            json={
                "model": "gpt-test",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "answer"}],
                    }
                ],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        )

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("providers.base.asyncio.sleep", no_sleep)
    async with _client(handler) as client:
        provider = OpenAIProvider(
            name="chatgpt",
            model="gpt-test",
            api_key="secret",
            retries=1,
            client=client,
        )
        result = await provider.complete(CompletionRequest(prompt="hello"))

    assert calls == 2
    assert result.request_audit["http_attempts"] == 2
    assert result.request_audit["retry_count"] == 1
    assert result.request_audit["usage_may_be_incomplete"] is True
    assert result.usage_may_be_incomplete is True


@pytest.mark.asyncio
async def test_http_529_is_retried_and_honors_retry_after(monkeypatch):
    calls = 0
    delays: list[float] = []

    def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                529,
                headers={"retry-after": "30"},
                json={"error": {"type": "overloaded_error"}},
            )
        return httpx.Response(
            200,
            json={
                "model": "claude-test",
                "content": [{"type": "text", "text": "answer"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        )

    async def capture_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("providers.base.asyncio.sleep", capture_sleep)
    async with _client(handler) as client:
        result = await AnthropicProvider(
            name="claude",
            model="claude-test",
            api_key="secret",
            retries=1,
            client=client,
        ).complete(CompletionRequest(prompt="hello"))

    assert result.text == "answer"
    assert calls == 2
    assert delays == [30.0]


def test_retry_after_http_date_uses_fixed_clock():
    fixed_now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)

    delay = HttpProvider._retry_delay(
        httpx.Headers({"retry-after": "Sat, 18 Jul 2026 12:00:30 GMT"}),
        0,
        now=fixed_now,
    )

    assert delay == 30.0


@pytest.mark.asyncio
async def test_retry_after_above_local_wait_cap_gives_up_without_early_retry(
    monkeypatch,
):
    calls = 0

    def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            headers={"retry-after": "120"},
            json={"error": {"message": "do not reflect"}},
        )

    async def forbidden_sleep(_delay):
        raise AssertionError("上限超過のRetry-Afterでsleepしてはいけない")

    monkeypatch.setattr("providers.base.asyncio.sleep", forbidden_sleep)
    async with _client(handler) as client:
        provider = OpenAIProvider(
            name="chatgpt",
            model="gpt-test",
            api_key="secret",
            retries=1,
            client=client,
        )
        with pytest.raises(ProviderError) as captured:
            await provider.complete(CompletionRequest(prompt="hello"))

    assert calls == 1
    assert captured.value.retryable is True
    assert captured.value.status_code == 429


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_type", "outcome"),
    [
        (httpx.ReadTimeout, "timeout"),
        (httpx.ConnectError, "network_error"),
        (httpx.RemoteProtocolError, "network_error"),
    ],
)
async def test_connection_failure_audit_never_reflects_exception(
    error_type,
    outcome,
):
    def handler(request: httpx.Request):
        raise error_type("raw-secret-exception", request=request)

    async with _client(handler) as client:
        provider = OpenAIProvider(
            name="chatgpt",
            model="gpt-test",
            api_key="secret",
            retries=0,
            client=client,
        )
        with pytest.raises(ProviderError) as captured:
            await provider.complete(CompletionRequest(prompt="hello"))

    error = captured.value
    assert "raw-secret-exception" not in str(error)
    assert error.request_audit == {
        "http_attempts": 1,
        "retry_count": 0,
        "outcome": outcome,
        "usage_may_be_incomplete": True,
    }


@pytest.mark.asyncio
async def test_gemini_incomplete_text_is_explicitly_partial():
    def handler(_request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "model": "gemini-test",
                "status": "incomplete",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": "partial answer"}],
                    }
                ],
                "usage": {"total_input_tokens": 3, "total_output_tokens": 2},
            },
        )

    async with _client(handler) as client:
        result = await GeminiProvider(
            name="gemini",
            model="gemini-test",
            api_key="secret",
            client=client,
        ).complete(CompletionRequest(prompt="hello"))

    assert result.text == "partial answer"
    assert result.completion_status == "incomplete"
    assert result.partial is True
    assert result.incomplete_reason is None


@pytest.mark.asyncio
async def test_gemini_budget_exceeded_is_preserved_as_incomplete_reason():
    def handler(_request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "model": "gemini-test",
                "status": "budget_exceeded",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": "partial answer"}],
                    }
                ],
                "usage": {"total_input_tokens": 3, "total_output_tokens": 2},
            },
        )

    async with _client(handler) as client:
        result = await GeminiProvider(
            name="gemini",
            model="gemini-test",
            api_key="secret",
            client=client,
        ).complete(CompletionRequest(prompt="hello"))

    assert result.completion_status == "budget_exceeded"
    assert result.partial is True
    assert result.incomplete_reason == "budget_exceeded"


@pytest.mark.asyncio
async def test_anthropic_pause_turn_is_explicitly_incomplete():
    def handler(_request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "partial answer"}],
                "stop_reason": "pause_turn",
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        )

    async with _client(handler) as client:
        result = await AnthropicProvider(
            name="claude",
            model="claude-opus-4-8",
            api_key="secret",
            client=client,
        ).complete(CompletionRequest(prompt="hello", web_search=True))

    assert result.text == "partial answer"
    assert result.finish_reason == "pause_turn"
    assert result.completion_status == "incomplete"
    assert result.partial is True
    assert result.incomplete_reason == "pause_turn"


@pytest.mark.asyncio
async def test_anthropic_refusal_uses_fixed_error_and_discards_partial_output():
    def handler(_request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "must-not-surface"}],
                "stop_reason": "refusal",
                "stop_details": {
                    "category": "unstable-vendor-value",
                    "explanation": "must-not-surface-either",
                },
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )

    async with _client(handler) as client:
        provider = AnthropicProvider(
            name="claude",
            model="claude-opus-4-8",
            api_key="secret",
            client=client,
        )
        with pytest.raises(ProviderError) as captured:
            await provider.complete(CompletionRequest(prompt="hello"))

    assert str(captured.value) == (
        "claude: モデルのポリシー判定により回答を拒否しました"
    )
    assert captured.value.error_code == "model_refusal"
    assert "must-not-surface" not in str(captured.value)


def test_live_gate_without_any_key_is_fail_closed(monkeypatch):
    monkeypatch.setattr(config, "LIVE_API_ENABLED", True)
    monkeypatch.setattr(config, "INCLUDE_MOCKS_WHEN_MIXED", False)
    for key_name in config._ENV_KEYS.values():
        monkeypatch.delenv(key_name, raising=False)

    assert config.mode() == "mixed"
    assert config.active_workers() == []
    assert {item["mode"] for item in config.statuses()} == {"disabled"}
    with pytest.raises(RuntimeError, match="APIキーが未設定"):
        config.get_provider("claude")
    with pytest.raises(RuntimeError, match="APIキーが未設定"):
        config.get_synthesizer()


def test_live_gate_allows_mock_only_with_explicit_opt_in(monkeypatch):
    monkeypatch.setattr(config, "LIVE_API_ENABLED", True)
    monkeypatch.setattr(config, "INCLUDE_MOCKS_WHEN_MIXED", True)
    for key_name in config._ENV_KEYS.values():
        monkeypatch.delenv(key_name, raising=False)

    assert config.mode() == "mixed"
    assert config.active_workers() == list(config.WORKERS)
    assert isinstance(config.get_provider("claude"), MockProvider)
    assert isinstance(config.get_synthesizer(), MockProvider)


@pytest.mark.asyncio
async def test_xai_incomplete_text_and_opaque_prompt_cache_key():
    raw_conversation_id = "00000000-0000-0000-0000-000000000001"

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        assert body["prompt_cache_key"].startswith("clage-")
        assert len(body["prompt_cache_key"]) == 54
        assert raw_conversation_id not in request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "model": "grok-test",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "partial grok"}],
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        )

    async with _client(handler) as client:
        result = await XAIProvider(
            name="grok",
            model="grok-test",
            api_key="secret",
            client=client,
        ).complete(
            CompletionRequest(
                prompt="hello",
                prompt_cache_key=raw_conversation_id,
            )
        )

    assert result.completion_status == "incomplete"
    assert result.partial is True
    assert result.incomplete_reason == "max_output_tokens"


@pytest.mark.asyncio
async def test_missing_usage_is_explicitly_unknown_even_after_http_success():
    def handler(_request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "model": "gpt-test",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "partial"}],
                    }
                ],
            },
        )

    async with _client(handler) as client:
        result = await OpenAIProvider(
            name="chatgpt",
            model="gpt-test",
            api_key="secret",
            client=client,
        ).complete(CompletionRequest(prompt="hello"))

    assert result.usage == {}
    assert result.usage_may_be_incomplete is True
    assert result.request_audit["usage_may_be_incomplete"] is True


def test_public_audit_shape_drops_arbitrary_strings():
    error = ProviderError(
        "safe",
        error_code="raw-secret-classification",
        request_audit={
            "http_attempts": 1,
            "retry_count": 0,
            "outcome": "timeout",
            "usage_may_be_incomplete": True,
            "raw_exception": "raw-secret-exception",
        },
    )

    encoded = json.dumps(error.public_metadata())
    assert "raw-secret-exception" not in encoded
    assert "raw_exception" not in encoded
    assert "raw-secret-classification" not in encoded
    assert "error_code" not in error.public_metadata()


@pytest.mark.asyncio
async def test_http_error_never_includes_key():
    def handler(_request: httpx.Request):
        return httpx.Response(
            401,
            json={"error": {"message": "invalid key super-secret"}},
        )

    async with _client(handler) as client:
        provider = OpenAIProvider(
            name="chatgpt", model="gpt-test", api_key="super-secret", client=client
        )
        with pytest.raises(Exception) as captured:
            await provider.complete(CompletionRequest(prompt="hello"))
    assert "super-secret" not in str(captured.value)
    assert "invalid key" not in str(captured.value)
    assert "API認証に失敗しました" in str(captured.value)
    assert captured.value.request_audit["http_attempts"] == 1


@pytest.mark.asyncio
async def test_generic_http_400_stays_unclassified_and_fixed():
    def handler(_request: httpx.Request):
        return httpx.Response(
            400,
            json={"error": {"message": "ordinary invalid request"}},
        )

    async with _client(handler) as client:
        provider = AnthropicProvider(
            name="claude", model="claude-test", api_key="secret", client=client
        )
        with pytest.raises(ProviderError) as captured:
            await provider.complete(CompletionRequest(prompt="hello"))

    error = captured.value
    assert str(error) == "claude: API要求が受理されませんでした (HTTP 400)"
    assert error.error_code is None
    assert "error_code" not in error.public_metadata()


@pytest.mark.asyncio
async def test_http_400_secret_body_is_never_reflected_or_classified():
    leaked_secret = "sk-proj-" + "z" * 32

    def handler(_request: httpx.Request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": (
                        f"invalid request with {leaked_secret}; billing and credit"
                    )
                }
            },
        )

    async with _client(handler) as client:
        provider = AnthropicProvider(
            name="claude", model="claude-test", api_key="secret", client=client
        )
        with pytest.raises(ProviderError) as captured:
            await provider.complete(CompletionRequest(prompt="hello"))

    error = captured.value
    public = json.dumps(error.public_metadata())
    assert leaked_secret not in str(error)
    assert leaked_secret not in public
    assert error.error_code is None
    assert "billing_or_credit_required" not in public


@pytest.mark.asyncio
async def test_anthropic_credit_billing_message_uses_fixed_classification():
    vendor_message = (
        "Your credit balance is too low to access the Anthropic API. "
        "Please go to Plans & Billing to upgrade or purchase credits."
    )

    def handler(_request: httpx.Request):
        return httpx.Response(
            400,
            json={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": vendor_message,
                },
            },
        )

    async with _client(handler) as client:
        provider = AnthropicProvider(
            name="claude", model="claude-test", api_key="secret", client=client
        )
        with pytest.raises(ProviderError) as captured:
            await provider.complete(CompletionRequest(prompt="hello"))

    error = captured.value
    assert error.error_code == "billing_or_credit_required"
    assert error.public_metadata()["error_code"] == "billing_or_credit_required"
    assert str(error) == (
        "claude: APIの請求設定またはクレジット残高を確認してください (HTTP 400)"
    )
    assert vendor_message not in str(error)


@pytest.mark.asyncio
async def test_mock_synthesis_hides_internal_prompt_markup():
    result = await MockProvider("synthesizer", delay=0).complete(
        CompletionRequest(
            prompt=(
                "<question>BYOK版の目的は？</question>\n"
                '<answer speaker="claude">internal answer</answer>'
            )
        )
    )
    assert "BYOK版の目的は？" in result.text
    assert "<question>" not in result.text
    assert "<answer" not in result.text
