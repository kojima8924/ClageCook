# -*- coding: utf-8 -*-
"""Anthropic Messages APIプロバイダ。"""

from __future__ import annotations

from model_capabilities import (
    CLAUDE_ADAPTIVE_THINKING_MODEL_PREFIXES as _EXPLICIT_ADAPTIVE_THINKING_MODEL_PREFIXES,
    CLAUDE_DYNAMIC_WEB_SEARCH_MODEL_PREFIXES as _DYNAMIC_WEB_SEARCH_MODEL_PREFIXES,
    CLAUDE_EFFORT_MODEL_PREFIXES as _EFFORT_MODEL_PREFIXES,
    matches_model_prefix as _matches_model,
)

from .base import (
    CompletionRequest,
    CompletionResult,
    HttpProvider,
    ProviderError,
    completion_metadata,
    extract_anthropic_citations,
    normalized_quota_snapshot,
    normalized_usage,
)


_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


class AnthropicProvider(HttpProvider):
    API_URL = "https://api.anthropic.com/v1/messages"

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        payload = {
            "model": self.model,
            "max_tokens": request.max_output_tokens,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.system:
            payload["system"] = request.system
        if (
            request.reasoning_effort in _EFFORTS
            and _matches_model(self.model, _EFFORT_MODEL_PREFIXES)
        ):
            payload["output_config"] = {
                "effort": request.reasoning_effort
            }
        if _matches_model(
            self.model,
            _EXPLICIT_ADAPTIVE_THINKING_MODEL_PREFIXES,
        ):
            # 現行Opus/Sonnetではbudget_tokensではなくadaptive thinkingを使う。
            # Fable/Mythosは常時adaptive、Opus 4.5はmanual thinkingのため送らない。
            payload["thinking"] = {"type": "adaptive"}
        if request.web_search:
            tool_version = (
                "web_search_20260318"
                if _matches_model(self.model, _DYNAMIC_WEB_SEARCH_MODEL_PREFIXES)
                else "web_search_20250305"
            )
            payload["tools"] = [
                {
                    "type": tool_version,
                    "name": "web_search",
                    "max_uses": max(1, min(request.web_search_max_uses, 10)),
                }
            ]
        data, headers, elapsed, audit = await self._post_json(
            self.API_URL,
            headers={
                "content-type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
            },
            payload=payload,
            timeout_sec=request.timeout_sec,
        )
        pieces = [
            str(block.get("text", "")).strip()
            for block in data.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n\n".join(piece for piece in pieces if piece).strip()
        stop_reason = str(data.get("stop_reason") or "") or None
        if stop_reason == "refusal":
            # stop_detailsの説明文は将来変更され得るため反射せず、固定文言で
            # 通常の空レスポンスとは区別する。途中出力も公式推奨どおり破棄する。
            raise ProviderError(
                "claude: モデルのポリシー判定により回答を拒否しました",
                error_code="model_refusal",
                request_audit={**audit, "usage_may_be_incomplete": True},
            )
        truncated = stop_reason in {"max_tokens", "model_context_window_exceeded"}
        paused = stop_reason == "pause_turn"
        if not text and not truncated and not paused:
            raise ProviderError(
                "claude: 回答テキストが空です",
                request_audit={**audit, "usage_may_be_incomplete": True},
            )
        usage = normalized_usage(data.get("usage"))
        usage_unknown = bool(audit["usage_may_be_incomplete"] or not usage)
        audit = {**audit, "usage_may_be_incomplete": usage_unknown}
        return CompletionResult(
            provider=self.name,
            model=str(data.get("model") or self.model),
            text=text,
            elapsed_sec=round(elapsed, 3),
            usage=usage,
            finish_reason=stop_reason,
            request_audit=audit,
            usage_may_be_incomplete=usage_unknown,
            quota_snapshot=normalized_quota_snapshot(self.name, headers),
            citations=extract_anthropic_citations(data),
            web_search_requested=request.web_search,
            **completion_metadata(
                "incomplete" if truncated or paused else "completed",
                text,
                stop_reason if truncated or paused else None,
            ),
        )
