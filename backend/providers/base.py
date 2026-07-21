# -*- coding: utf-8 -*-
"""プロバイダ共通契約と安全なHTTP呼び出し。"""

from __future__ import annotations

import asyncio
import hashlib
import math
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx


_COMPLETION_STATUSES = {
    "budget_exceeded",
    "completed",
    "incomplete",
    "failed",
    "cancelled",
    "requires_action",
    "in_progress",
}
_INCOMPLETE_REASONS = {
    "budget_exceeded",
    "content_filter",
    "max_output_tokens",
    "max_tokens",
    "model_context_window_exceeded",
    "pause_turn",
}
_MAX_RETRY_AFTER_SEC = 60.0
_AUDIT_OUTCOMES = {
    "http_error",
    "invalid_response",
    "mock",
    "network_error",
    "response_received",
    "timeout",
}
_PROVIDER_ERROR_CODES = frozenset(
    {"billing_or_credit_required", "model_refusal"}
)
_BILLING_OR_CREDIT_MESSAGE_ALLOWLIST = {
    "claude": (
        "credit balance is too low to access the anthropic api",
        "plans & billing",
        "purchase credits",
    ),
}


@dataclass(slots=True)
class CompletionRequest:
    """ベンダー非依存の1回分の生成要求。"""

    prompt: str
    system: str = ""
    tier: str = "balanced"
    reasoning_effort: str | None = None
    max_output_tokens: int = 2400
    timeout_sec: float = 180.0
    prompt_cache_key: str | None = None
    web_search: bool = False
    web_search_max_uses: int = 3


@dataclass(slots=True)
class CompletionResult:
    """UI・履歴へ安全に返せる生成結果。"""

    provider: str
    model: str
    text: str
    elapsed_sec: float
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None
    completion_status: str = "completed"
    partial: bool = False
    incomplete_reason: str | None = None
    usage_may_be_incomplete: bool = False
    request_audit: dict[str, Any] = field(default_factory=dict)
    quota_snapshot: dict[str, Any] = field(default_factory=dict)
    citations: list[dict[str, Any]] = field(default_factory=list)
    web_search_requested: bool = False
    mock: bool = False

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        audit = _safe_request_audit(data.get("request_audit"))
        data["request_audit"] = audit
        data["quota_snapshot"] = _safe_quota_snapshot(
            data.get("quota_snapshot")
        )
        data["citations"] = normalize_citations(data.get("citations"))
        if audit.get("usage_may_be_incomplete") is True:
            data["usage_may_be_incomplete"] = True
        return data


class ProviderError(RuntimeError):
    """APIキーやレスポンス本文を漏らさない正規化エラー。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        error_code: str | None = None,
        request_audit: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.error_code = (
            error_code if error_code in _PROVIDER_ERROR_CODES else None
        )
        self.request_audit = _safe_request_audit(request_audit)

    def public_metadata(self) -> dict[str, Any]:
        """保存・SSEへ出せる、秘密や生例外を含まない失敗メタデータ。"""
        metadata = {
            "completion_status": "failed",
            "partial": False,
            "usage_may_be_incomplete": bool(
                self.request_audit.get("usage_may_be_incomplete")
            ),
            "request_audit": dict(self.request_audit),
        }
        if self.error_code is not None:
            metadata["error_code"] = self.error_code
        return metadata


class Provider(ABC):
    name: str
    model: str
    is_mock: bool = False

    @abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResult:
        raise NotImplementedError


class HttpProvider(Provider):
    """リトライ・タイムアウト・エラー無害化を共有するHTTPプロバイダ。"""

    def __init__(
        self,
        *,
        name: str,
        model: str,
        api_key: str,
        retries: int = 0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self._api_key = api_key
        self._retries = max(0, min(retries, 4))
        self._client = client

    async def _post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_sec: float,
    ) -> tuple[dict[str, Any], httpx.Headers, float, dict[str, Any]]:
        started = time.perf_counter()
        last_error: ProviderError | None = None

        for attempt in range(self._retries + 1):
            attempts = attempt + 1
            owns_client = self._client is None
            client = self._client or httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_sec),
                follow_redirects=False,
            )
            try:
                response = await client.post(url, headers=headers, json=payload)
                if 200 <= response.status_code < 300:
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise ProviderError(
                            f"{self.name}: API応答がJSONではありません",
                            request_audit=_request_audit(
                                attempts,
                                "invalid_response",
                                status_code=response.status_code,
                                usage_may_be_incomplete=True,
                            ),
                        ) from exc
                    if not isinstance(data, dict):
                        raise ProviderError(
                            f"{self.name}: API応答形式が不正です",
                            request_audit=_request_audit(
                                attempts,
                                "invalid_response",
                                status_code=response.status_code,
                                usage_may_be_incomplete=True,
                            ),
                        )
                    audit = _request_audit(
                        attempts,
                        "response_received",
                        status_code=response.status_code,
                        usage_may_be_incomplete=attempts > 1,
                    )
                    return (
                        data,
                        response.headers,
                        time.perf_counter() - started,
                        audit,
                    )

                retryable = (
                    response.status_code in {408, 409, 429}
                    or response.status_code >= 500
                )
                error_code = self._safe_error_code(response)
                message = self._safe_api_error(response, error_code=error_code)
                last_error = ProviderError(
                    f"{self.name}: {message}",
                    status_code=response.status_code,
                    retryable=retryable,
                    error_code=error_code,
                    request_audit=_request_audit(
                        attempts,
                        "http_error",
                        status_code=response.status_code,
                        usage_may_be_incomplete=(
                            attempts > 1
                            or response.status_code in {408, 409}
                            or response.status_code >= 500
                        ),
                    ),
                )
                if not retryable or attempt >= self._retries:
                    raise last_error
                delay = self._retry_delay(response.headers, attempt)
                # Retry-Afterを早めて再試行すると429を悪化させる。長すぎる待機を
                # アプリ内で抱えず、サーバ指定が上限を超える場合は今回の呼び出しを諦める。
                if delay is None:
                    raise last_error
            except ProviderError:
                raise
            except httpx.TimeoutException as exc:
                last_error = ProviderError(
                    f"{self.name}: APIへの接続がタイムアウトしました",
                    retryable=True,
                    request_audit=_request_audit(
                        attempts,
                        "timeout",
                        usage_may_be_incomplete=True,
                    ),
                )
                if attempt >= self._retries:
                    raise last_error from exc
                delay = min(4.0, (2**attempt) + random.random() * 0.25)
            except httpx.NetworkError as exc:
                last_error = ProviderError(
                    f"{self.name}: APIへの接続に失敗しました",
                    retryable=True,
                    request_audit=_request_audit(
                        attempts,
                        "network_error",
                        usage_may_be_incomplete=True,
                    ),
                )
                if attempt >= self._retries:
                    raise last_error from exc
                delay = min(4.0, (2**attempt) + random.random() * 0.25)
            except httpx.RequestError as exc:
                last_error = ProviderError(
                    f"{self.name}: API通信に失敗しました",
                    retryable=True,
                    request_audit=_request_audit(
                        attempts,
                        "network_error",
                        usage_may_be_incomplete=True,
                    ),
                )
                if attempt >= self._retries:
                    raise last_error from exc
                delay = min(4.0, (2**attempt) + random.random() * 0.25)
            finally:
                if owns_client:
                    await client.aclose()
            await asyncio.sleep(delay)

        raise last_error or ProviderError(f"{self.name}: API呼び出しに失敗しました")

    @staticmethod
    def _retry_delay(
        headers: httpx.Headers,
        attempt: int,
        *,
        now: datetime | None = None,
    ) -> float | None:
        raw = headers.get("retry-after", "").strip()
        retry_after: float | None = None
        try:
            retry_after = float(raw)
        except ValueError:
            if raw:
                try:
                    target = parsedate_to_datetime(raw)
                    if target.tzinfo is None:
                        target = target.replace(tzinfo=timezone.utc)
                    current = now or datetime.now(timezone.utc)
                    if current.tzinfo is None:
                        current = current.replace(tzinfo=timezone.utc)
                    retry_after = (
                        target.astimezone(timezone.utc)
                        - current.astimezone(timezone.utc)
                    ).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    retry_after = None
        if retry_after is not None and math.isfinite(retry_after):
            if retry_after > _MAX_RETRY_AFTER_SEC:
                return None
            return max(0.1, retry_after)
        return min(4.0, (2**attempt) + random.random() * 0.25)

    def _safe_error_code(self, response: httpx.Response) -> str | None:
        """既知のvendor文言だけを固定分類へ写し、生本文は保持しない。"""
        required_phrases = _BILLING_OR_CREDIT_MESSAGE_ALLOWLIST.get(self.name)
        if response.status_code != 400 or required_phrases is None:
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        error = data.get("error")
        if not isinstance(error, dict):
            return None
        message = error.get("message")
        if not isinstance(message, str):
            return None
        normalized = " ".join(message.casefold().split())
        if all(phrase in normalized for phrase in required_phrases):
            return "billing_or_credit_required"
        return None

    def _safe_api_error(
        self,
        response: httpx.Response,
        *,
        error_code: str | None = None,
    ) -> str:
        """応答本文を反射せず、HTTP分類だけを返す。"""
        if error_code == "billing_or_credit_required":
            return (
                "APIの請求設定またはクレジット残高を確認してください "
                f"(HTTP {response.status_code})"
            )
        labels = {
            400: "API要求が受理されませんでした",
            401: "API認証に失敗しました",
            403: "API利用が許可されていません",
            404: "APIまたはモデルが見つかりません",
            408: "API要求がタイムアウトしました",
            409: "API要求が競合しました",
            413: "API要求が大きすぎます",
            422: "API要求を処理できません",
            429: "API利用上限に達しました",
        }
        if response.status_code in labels:
            return f"{labels[response.status_code]} (HTTP {response.status_code})"
        if response.status_code >= 500:
            return f"API側で一時的な障害が発生しました (HTTP {response.status_code})"
        return f"HTTP {response.status_code}"


def _request_audit(
    attempts: int,
    outcome: str,
    *,
    status_code: int | None = None,
    usage_may_be_incomplete: bool = False,
) -> dict[str, Any]:
    """外部へ保存できる固定shapeのHTTP監査情報を作る。"""
    audit: dict[str, Any] = {
        "http_attempts": max(0, attempts),
        "retry_count": max(0, attempts - 1),
        "outcome": outcome,
        "usage_may_be_incomplete": bool(usage_may_be_incomplete),
    }
    if status_code is not None:
        audit["final_http_status"] = int(status_code)
    return audit


def _safe_request_audit(raw: Any) -> dict[str, Any]:
    """任意キーや文字列を落とし、監査shape自体をデータ漏えい経路にしない。"""
    if not isinstance(raw, dict) or not raw:
        return {}
    attempts = raw.get("http_attempts")
    retries = raw.get("retry_count")
    outcome = raw.get("outcome")
    status = raw.get("final_http_status")
    result: dict[str, Any] = {
        "http_attempts": (
            attempts
            if isinstance(attempts, int) and not isinstance(attempts, bool) and attempts >= 0
            else 0
        ),
        "retry_count": (
            retries
            if isinstance(retries, int) and not isinstance(retries, bool) and retries >= 0
            else 0
        ),
        "outcome": outcome if outcome in _AUDIT_OUTCOMES else "unknown",
        "usage_may_be_incomplete": raw.get("usage_may_be_incomplete") is True,
    }
    if (
        isinstance(status, int)
        and not isinstance(status, bool)
        and 100 <= status <= 599
    ):
        result["final_http_status"] = status
    return result


def completion_metadata(
    status: Any,
    text: str,
    incomplete_details: Any = None,
    *,
    default_status: str = "completed",
) -> dict[str, Any]:
    """ベンダー状態を、UIが共通に扱える安全な完了状態へ正規化する。"""
    normalized = str(status or default_status).strip().lower()
    if normalized not in _COMPLETION_STATUSES:
        normalized = "unknown"

    reason: str | None = None
    if isinstance(incomplete_details, dict):
        candidate = str(incomplete_details.get("reason") or "").strip().lower()
        if candidate in _INCOMPLETE_REASONS:
            reason = candidate
    elif isinstance(incomplete_details, str):
        candidate = incomplete_details.strip().lower()
        if candidate in _INCOMPLETE_REASONS:
            reason = candidate
    if reason is None and normalized in _INCOMPLETE_REASONS:
        reason = normalized

    return {
        "completion_status": normalized,
        "partial": normalized != "completed" and bool(text.strip()),
        "incomplete_reason": reason,
    }


def opaque_prompt_cache_key(value: str) -> str:
    """xAIへ送るcache keyが生の会話ID等にならないよう強制する。"""
    if re.fullmatch(r"clage-[0-9a-f]{48}", value):
        return value
    digest = hashlib.sha256(
        f"clage-cook-cache\0{value}".encode("utf-8")
    ).hexdigest()
    return f"clage-{digest[:48]}"


def extract_responses_text(data: dict[str, Any]) -> str:
    """OpenAI/xAI Responses APIのtyped outputから最終テキストを抽出する。"""
    pieces: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    pieces.append(text.strip())
    return "\n\n".join(pieces).strip()


def normalize_citations(raw: Any, *, limit: int = 40) -> list[dict[str, Any]]:
    """Provider由来の引用を、クリック可能なHTTP(S) URLだけへ正規化する。"""
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            url = item.strip()
            title = ""
            start_index = None
            end_index = None
        elif isinstance(item, dict):
            url = str(item.get("url") or item.get("source_url") or "").strip()
            title = str(item.get("title") or item.get("source_title") or "").strip()
            start_index = item.get("start_index")
            end_index = item.get("end_index")
        else:
            continue
        if not url or len(url) > 2048 or url in seen:
            continue
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        citation: dict[str, Any] = {
            "url": url,
            "title": title[:300] or parsed.netloc[:300],
        }
        if (
            isinstance(start_index, int)
            and not isinstance(start_index, bool)
            and start_index >= 0
            and isinstance(end_index, int)
            and not isinstance(end_index, bool)
            and end_index >= start_index
        ):
            citation["start_index"] = start_index
            citation["end_index"] = end_index
        result.append(citation)
        seen.add(url)
        if len(result) >= max(1, min(limit, 100)):
            break
    return result


def extract_responses_citations(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Responses API互換shapeからURL引用だけを抽出する。"""
    raw: list[Any] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            for annotation in content.get("annotations") or []:
                if isinstance(annotation, dict) and annotation.get("type") in {
                    "url_citation",
                    "citation",
                }:
                    raw.append(annotation)
    top_level = data.get("citations")
    if isinstance(top_level, list):
        raw.extend(top_level)
    return normalize_citations(raw)


def extract_anthropic_citations(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Messages APIのtext blockに付く引用metadataを抽出する。"""
    raw: list[Any] = []
    for block in data.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        citations = block.get("citations")
        if isinstance(citations, list):
            raw.extend(citations)
    return normalize_citations(raw)


def extract_gemini_citations(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Gemini Interactions APIのmodel_output annotationを抽出する。"""
    raw: list[Any] = []
    for step in data.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for content in step.get("content") or []:
            if not isinstance(content, dict):
                continue
            for annotation in content.get("annotations") or []:
                if isinstance(annotation, dict) and annotation.get("type") == "url_citation":
                    raw.append(annotation)
    return normalize_citations(raw)


def normalized_usage(raw: Any) -> dict[str, int]:
    """4社のusageを、意味を捏造せず共通キーへ正規化する。"""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    aliases = {
        "input_tokens": "input_tokens",
        "prompt_tokens": "input_tokens",
        "total_input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "completion_tokens": "output_tokens",
        "total_output_tokens": "output_tokens",
        "total_tokens": "total_tokens",
        "cache_creation_input_tokens": "cache_creation_input_tokens",
        "cache_read_input_tokens": "cached_input_tokens",
        "total_cached_tokens": "cached_input_tokens",
        "total_thought_tokens": "reasoning_tokens",
        "total_tool_use_tokens": "tool_tokens",
        "cost_in_usd_ticks": "cost_in_usd_ticks",
        "num_sources_used": "sources_used",
        "num_server_side_tools_used": "server_side_tools_used",
    }
    for source, target in aliases.items():
        value = raw.get(source)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[target] = value

    input_details = raw.get("input_tokens_details") or raw.get(
        "prompt_tokens_details"
    )
    if isinstance(input_details, dict):
        cached = input_details.get("cached_tokens")
        if (
            isinstance(cached, int)
            and not isinstance(cached, bool)
            and cached >= 0
        ):
            result["cached_input_tokens"] = cached

    output_details = raw.get("output_tokens_details") or raw.get(
        "completion_tokens_details"
    )
    if isinstance(output_details, dict):
        reasoning = output_details.get("reasoning_tokens")
        if (
            isinstance(reasoning, int)
            and not isinstance(reasoning, bool)
            and reasoning >= 0
        ):
            result["reasoning_tokens"] = reasoning

    # Gemini Interactionsはthinkingをoutputと別区分で返し、両方が出力単価の
    # 対象になる。OpenAI/xAI Responsesのoutputはreasoningを内包する一方、
    # xAI Chatのcompletionは外数の場合がある。provider非依存のここでは
    # completion shapeだけで推測せず、finance側がprovider+totalで判別する。
    if "output_tokens" in result:
        if "total_thought_tokens" in raw:
            result["billable_output_tokens"] = (
                result["output_tokens"] + result.get("reasoning_tokens", 0)
            )

    server_tools = raw.get("server_tool_use")
    if isinstance(server_tools, dict):
        searches = server_tools.get("web_search_requests")
        if (
            isinstance(searches, int)
            and not isinstance(searches, bool)
            and searches >= 0
        ):
            result["web_search_requests"] = searches

    grounding_counts = raw.get("grounding_tool_count")
    if isinstance(grounding_counts, dict):
        grounding_counts = [grounding_counts]
    if isinstance(grounding_counts, list):
        searches = 0
        found = False
        for item in grounding_counts:
            if not isinstance(item, dict) or item.get("type") != "google_search":
                continue
            count = item.get("count")
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                searches += count
                found = True
        if found:
            result["web_search_requests"] = searches

    if "total_tokens" not in result and (
        "input_tokens" in result or "output_tokens" in result
    ):
        # OpenAI/xAI/Geminiのcached値はinputの内数。Anthropicだけは
        # cache creation/readがinput_tokensの外数なので、元キーがある場合だけ加算する。
        total = result.get("input_tokens", 0) + result.get("output_tokens", 0)
        if "cache_creation_input_tokens" in raw:
            total += result.get("cache_creation_input_tokens", 0)
        if "cache_read_input_tokens" in raw:
            total += result.get("cached_input_tokens", 0)
        if "total_thought_tokens" in raw:
            total += result.get("reasoning_tokens", 0)
        result["total_tokens"] = total
    return result


_QUOTA_DIMENSIONS = (
    "requests",
    "tokens",
    "input_tokens",
    "output_tokens",
)
_QUOTA_HEADER_PREFIXES = {
    "claude": {
        "requests": "anthropic-ratelimit-requests",
        "tokens": "anthropic-ratelimit-tokens",
        "input_tokens": "anthropic-ratelimit-input-tokens",
        "output_tokens": "anthropic-ratelimit-output-tokens",
    },
    "chatgpt": {
        "requests": "x-ratelimit",
        "tokens": "x-ratelimit",
    },
    "grok": {
        "requests": "x-ratelimit",
        "tokens": "x-ratelimit",
    },
}


def normalized_quota_snapshot(
    provider: str,
    headers: httpx.Headers,
    *,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """許可したrate-limit headerだけを、推測せず共通shapeへ写す。"""
    prefixes = _QUOTA_HEADER_PREFIXES.get(provider, {})
    dimensions: dict[str, dict[str, Any]] = {}
    for dimension, prefix in prefixes.items():
        if provider == "claude":
            names = {
                "limit": f"{prefix}-limit",
                "remaining": f"{prefix}-remaining",
                "reset": f"{prefix}-reset",
            }
        else:
            plural = "requests" if dimension == "requests" else "tokens"
            names = {
                "limit": f"{prefix}-limit-{plural}",
                "remaining": f"{prefix}-remaining-{plural}",
                "reset": f"{prefix}-reset-{plural}",
            }
        limit = _nonnegative_header_int(headers.get(names["limit"]))
        remaining = _nonnegative_header_int(headers.get(names["remaining"]))
        reset = _safe_header_marker(headers.get(names["reset"]))
        if limit is None and remaining is None and reset is None:
            continue
        dimensions[dimension] = {
            "limit": limit,
            "remaining": remaining,
            "reset": reset,
        }

    retry_after = _bounded_header_float(headers.get("retry-after"))
    if not dimensions and retry_after is None:
        return {}
    timestamp = observed_at or datetime.now(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    return {
        "source": "response_headers",
        "scope": "unknown",
        "confidence": "observed",
        "parser_version": 1,
        "observed_at": timestamp,
        "dimensions": dimensions,
        "retry_after_seconds": retry_after,
    }


def _nonnegative_header_int(value: str | None) -> int | None:
    if value is None or not re.fullmatch(r"\d{1,18}", value.strip()):
        return None
    parsed = int(value)
    return parsed if parsed <= 10**15 else None


def _bounded_header_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value.strip())
    except ValueError:
        return None
    if parsed < 0 or parsed > 86_400:
        return None
    return round(parsed, 3)


def _safe_header_marker(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 80:
        return None
    if not re.fullmatch(r"[A-Za-z0-9.:+\-]+", cleaned):
        return None
    return cleaned


def _safe_quota_snapshot(raw: Any) -> dict[str, Any]:
    """保存・公開経路へ任意header名や文字列を持ち込ませない。"""
    if not isinstance(raw, dict) or raw.get("source") != "response_headers":
        return {}
    raw_dimensions = raw.get("dimensions")
    dimensions: dict[str, dict[str, Any]] = {}
    if isinstance(raw_dimensions, dict):
        for name in _QUOTA_DIMENSIONS:
            item = raw_dimensions.get(name)
            if not isinstance(item, dict):
                continue
            limit = item.get("limit")
            remaining = item.get("remaining")
            reset = item.get("reset")
            safe_limit = (
                limit
                if isinstance(limit, int)
                and not isinstance(limit, bool)
                and 0 <= limit <= 10**15
                else None
            )
            safe_remaining = (
                remaining
                if isinstance(remaining, int)
                and not isinstance(remaining, bool)
                and 0 <= remaining <= 10**15
                else None
            )
            safe_reset = _safe_header_marker(reset if isinstance(reset, str) else None)
            if safe_limit is None and safe_remaining is None and safe_reset is None:
                continue
            dimensions[name] = {
                "limit": safe_limit,
                "remaining": safe_remaining,
                "reset": safe_reset,
            }
    retry_after = raw.get("retry_after_seconds")
    safe_retry_after = (
        round(float(retry_after), 3)
        if isinstance(retry_after, (int, float))
        and not isinstance(retry_after, bool)
        and 0 <= float(retry_after) <= 86_400
        else None
    )
    if not dimensions and safe_retry_after is None:
        return {}
    observed_at = raw.get("observed_at")
    safe_observed_at = (
        observed_at
        if isinstance(observed_at, str)
        and 1 <= len(observed_at) <= 40
        and re.fullmatch(r"[0-9TZ:.,+\-]+", observed_at)
        else None
    )
    return {
        "source": "response_headers",
        "scope": "unknown",
        "confidence": "observed",
        "parser_version": 1,
        "observed_at": safe_observed_at,
        "dimensions": dimensions,
        "retry_after_seconds": safe_retry_after,
    }
