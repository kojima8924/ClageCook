# -*- coding: utf-8 -*-
"""Google Gemini Interactions APIプロバイダ。"""

from __future__ import annotations

from .base import (
    CompletionRequest,
    CompletionResult,
    HttpProvider,
    ProviderError,
    completion_metadata,
    extract_gemini_citations,
    normalized_quota_snapshot,
    normalized_usage,
)


_THINKING_LEVELS = frozenset({"minimal", "low", "medium", "high"})


class GeminiProvider(HttpProvider):
    API_URL = "https://generativelanguage.googleapis.com/v1/interactions"

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        payload = {
            "model": self.model,
            "input": request.prompt,
            "store": False,
            "generation_config": {
                "max_output_tokens": request.max_output_tokens,
            },
        }
        if request.reasoning_effort in _THINKING_LEVELS:
            payload["generation_config"]["thinking_level"] = (
                request.reasoning_effort
            )
        if request.system:
            payload["system_instruction"] = request.system
        if request.web_search:
            payload["tools"] = [{"type": "google_search"}]
        data, headers, elapsed, audit = await self._post_json(
            self.API_URL,
            headers={
                "content-type": "application/json",
                "x-goog-api-key": self._api_key,
            },
            payload=payload,
            timeout_sec=request.timeout_sec,
        )
        pieces: list[str] = []
        for step in data.get("steps") or []:
            if not isinstance(step, dict) or step.get("type") != "model_output":
                continue
            for content in step.get("content") or []:
                if isinstance(content, dict) and content.get("type") == "text":
                    text = content.get("text")
                    if isinstance(text, str) and text.strip():
                        pieces.append(text.strip())
        text = "\n\n".join(pieces).strip()
        status = str(data.get("status") or "") or "completed"
        if not text and status == "completed":
            raise ProviderError(
                "gemini: 回答テキストが空です",
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
            citations=extract_gemini_citations(data),
            web_search_requested=request.web_search,
            **completion_metadata(status, text),
        )
