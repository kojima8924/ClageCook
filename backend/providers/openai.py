# -*- coding: utf-8 -*-
"""OpenAI Responses APIプロバイダ。"""

from __future__ import annotations

from .base import (
    CompletionRequest,
    CompletionResult,
    HttpProvider,
    ProviderError,
    completion_metadata,
    extract_responses_citations,
    extract_responses_text,
    normalized_quota_snapshot,
    normalized_usage,
)


_EFFORT = {"low": "low", "balanced": "medium", "high": "high"}


class OpenAIProvider(HttpProvider):
    API_URL = "https://api.openai.com/v1/responses"

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        payload = {
            "model": self.model,
            "input": request.prompt,
            "store": False,
            "max_output_tokens": request.max_output_tokens,
            "reasoning": {"effort": _EFFORT.get(request.tier, "medium")},
        }
        if request.system:
            payload["instructions"] = request.system
        if request.web_search:
            payload["tools"] = [
                {"type": "web_search", "search_context_size": "medium"}
            ]
            payload["tool_choice"] = "auto"
        data, headers, elapsed, audit = await self._post_json(
            self.API_URL,
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {self._api_key}",
            },
            payload=payload,
            timeout_sec=request.timeout_sec,
        )
        text = extract_responses_text(data)
        status = str(data.get("status") or "") or "completed"
        if not text and status == "completed":
            raise ProviderError(
                "chatgpt: 回答テキストが空です",
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
            finish_reason=status,
            request_audit=audit,
            usage_may_be_incomplete=usage_unknown,
            quota_snapshot=normalized_quota_snapshot(self.name, headers),
            citations=extract_responses_citations(data),
            web_search_requested=request.web_search,
            **completion_metadata(
                status,
                text,
                data.get("incomplete_details"),
            ),
        )
