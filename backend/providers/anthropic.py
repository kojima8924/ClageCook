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

# pause_turn(サーバ側ツールループの一時停止)の継続回数上限。web検索の
# max_uses<=10に対し、実際のpauseは数回で収まるため3回で十分カバーできる。
# 上限到達時はエラーにせず、それまでの本文をincompleteとして返す(issue #20)。
_MAX_PAUSE_TURN_CONTINUATIONS = 3
# 継続1回に最低限確保したい残り時間(秒)。これを下回ったら途中結果で打ち切る
# (タイムアウト直前に継続を始めても、課金だけ増えて成果が返らないため)。
_PAUSE_TURN_MIN_REMAINING_SEC = 5.0


class AnthropicProvider(HttpProvider):
    API_URL = "https://api.anthropic.com/v1/messages"

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        messages = [{"role": "user", "content": request.prompt}]
        payload = {
            "model": self.model,
            "max_tokens": request.max_output_tokens,
            "messages": messages,
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

        # pause_turnはエラーではなく「サーバ側ツールループの一時停止」。返って
        # きたcontentをassistantターンとして積んで再送すると続きが生成される。
        # 各回のusageは全て課金されるため取りこぼさず合算する。timeout_secは
        # 継続を含む全体の予算として扱う(1回ごとに満額与えると、pauseの回数分
        # だけ呼び出し全体の所要時間が膨らみ、呼び出し側の想定を破るため)。
        pieces: list[str] = []
        citations: list[dict] = []
        usage_total: dict[str, int] = {}
        elapsed_total = 0.0
        attempts_total = 0
        retries_total = 0
        usage_unknown = False
        continuations = 0

        while True:
            data, headers, elapsed, audit = await self._post_json(
                self.API_URL,
                headers={
                    "content-type": "application/json",
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                },
                payload=payload,
                timeout_sec=max(1.0, request.timeout_sec - elapsed_total),
            )
            elapsed_total += elapsed
            attempts_total += int(audit.get("http_attempts") or 0)
            retries_total += int(audit.get("retry_count") or 0)
            usage = normalized_usage(data.get("usage"))
            for key, value in usage.items():
                usage_total[key] = usage_total.get(key, 0) + value
            usage_unknown = bool(
                usage_unknown or audit["usage_may_be_incomplete"] or not usage
            )
            stop_reason = str(data.get("stop_reason") or "") or None
            if stop_reason == "refusal":
                # stop_detailsの説明文は将来変更され得るため反射せず、固定文言で
                # 通常の空レスポンスとは区別する。途中出力(継続前の回を含む)も
                # 公式推奨どおり破棄する。
                raise ProviderError(
                    "claude: モデルのポリシー判定により回答を拒否しました",
                    error_code="model_refusal",
                    request_audit={
                        **audit,
                        "http_attempts": attempts_total,
                        "retry_count": retries_total,
                        "usage_may_be_incomplete": True,
                    },
                )
            pieces.extend(
                str(block.get("text", "")).strip()
                for block in data.get("content") or []
                if isinstance(block, dict) and block.get("type") == "text"
            )
            citations.extend(extract_anthropic_citations(data))
            if stop_reason != "pause_turn":
                break
            if (
                continuations >= _MAX_PAUSE_TURN_CONTINUATIONS
                or request.timeout_sec - elapsed_total
                < _PAUSE_TURN_MIN_REMAINING_SEC
            ):
                break  # 上限到達。ここまでの本文をincompleteとして返す
            continuations += 1
            messages.append(
                {"role": "assistant", "content": data.get("content") or []}
            )

        text = "\n\n".join(piece for piece in pieces if piece).strip()
        truncated = stop_reason in {"max_tokens", "model_context_window_exceeded"}
        paused = stop_reason == "pause_turn"
        audit = {
            **audit,
            "http_attempts": attempts_total,
            "retry_count": retries_total,
            "usage_may_be_incomplete": usage_unknown,
        }
        if not text and not truncated and not paused:
            raise ProviderError(
                "claude: 回答テキストが空です",
                request_audit={**audit, "usage_may_be_incomplete": True},
            )
        return CompletionResult(
            provider=self.name,
            model=str(data.get("model") or self.model),
            text=text,
            elapsed_sec=round(elapsed_total, 3),
            usage=usage_total,
            finish_reason=stop_reason,
            request_audit=audit,
            usage_may_be_incomplete=usage_unknown,
            quota_snapshot=normalized_quota_snapshot(self.name, headers),
            citations=citations,
            web_search_requested=request.web_search,
            **completion_metadata(
                "incomplete" if truncated or paused else "completed",
                text,
                stop_reason if truncated or paused else None,
            ),
        )
