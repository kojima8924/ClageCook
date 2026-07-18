# -*- coding: utf-8 -*-
"""Anthropic Messages APIプロバイダ。"""

from __future__ import annotations

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
        if request.web_search:
            payload["tools"] = [
                {
                    "type": "web_search_20250305",
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
        truncated = stop_reason in {"max_tokens", "model_context_window_exceeded"}
        if not text and not truncated:
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
                "incomplete" if truncated else "completed",
                text,
                stop_reason if truncated else None,
            ),
        )
