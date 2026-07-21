# -*- coding: utf-8 -*-
"""xAI Responses APIプロバイダ。"""

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
    opaque_prompt_cache_key,
)


_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})


class XAIProvider(HttpProvider):
    API_URL = "https://api.x.ai/v1/responses"

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        input_messages = []
        if request.system:
            input_messages.append({"role": "system", "content": request.system})
        input_messages.append({"role": "user", "content": request.prompt})
        payload = {
            "model": self.model,
            "input": input_messages,
            "store": False,
            "max_output_tokens": request.max_output_tokens,
        }
        if request.reasoning_effort in _EFFORTS:
            payload["reasoning"] = {"effort": request.reasoning_effort}
        if request.prompt_cache_key:
            payload["prompt_cache_key"] = opaque_prompt_cache_key(
                request.prompt_cache_key
            )
        if request.web_search:
            payload["tools"] = [{"type": "web_search"}]
            # xAIのmax_turnsは検索回数そのものではなく、assistant/toolの
            # 往復上限。無制限のagentic loopを避けるための安全弁として使う。
            payload["max_turns"] = max(1, min(request.web_search_max_uses, 10))
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
                "grok: 回答テキストが空です",
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
