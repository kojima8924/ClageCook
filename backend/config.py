# -*- coding: utf-8 -*-
"""環境変数からBYOK設定を解決する。

キーそのものはこのモジュール外へ返さない。実APIは明示的に武装するまで無効で、
既定ではキーの有無に関係なく4体モックだけを使う。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from providers import (
    AnthropicProvider,
    GeminiProvider,
    MockProvider,
    OpenAIProvider,
    Provider,
    XAIProvider,
)
from runtime_settings import RuntimeSettingsStore


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

WORKERS = ("claude", "gemini", "chatgpt", "grok")
LABELS = {
    "claude": "Claude",
    "gemini": "Gemini",
    "chatgpt": "ChatGPT",
    "grok": "Grok",
}

DEFAULT_MODELS = {
    "claude": {
        "low": "claude-haiku-4-5-20251001",
        "balanced": "claude-sonnet-5",
        "high": "claude-opus-4-8",
    },
    "gemini": {
        "low": "gemini-3.1-flash-lite",
        "balanced": "gemini-3.5-flash",
        "high": "gemini-3.5-flash",
    },
    "chatgpt": {
        "low": "gpt-5.6-luna",
        "balanced": "gpt-5.6-terra",
        "high": "gpt-5.6-sol",
    },
    "grok": {
        "low": "grok-4.3",
        "balanced": "grok-4.3",
        "high": "grok-4.5",
    },
}

_ENV_KEYS = {
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "chatgpt": "OPENAI_API_KEY",
    "grok": "XAI_API_KEY",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


AUTH_TOKEN = os.getenv("CLAGE_AUTH_TOKEN", "").strip()
CORS_ORIGINS = tuple(
    item.strip()
    for item in os.getenv("CLAGE_CORS_ORIGINS", "").split(",")
    if item.strip()
)
CORS_ORIGIN_REGEX = os.getenv(
    "CLAGE_CORS_ORIGIN_REGEX",
    r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
).strip()
DATA_DIR = Path(os.getenv("CLAGE_DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()
INCLUDE_MOCKS_WHEN_MIXED = _env_bool("INCLUDE_MOCK_PROVIDERS", False)
LIVE_API_ENABLED = _env_bool("CLAGE_LIVE_API_ENABLED", False)
HTTP_TIMEOUT_SEC = _env_float("HTTP_TIMEOUT_SEC", 180.0, 5.0, 900.0)
# 生成APIは応答喪失時に同じ処理を再度課金され得るため、自動再試行は既定OFF。
HTTP_RETRIES = _env_int("HTTP_RETRIES", 0, 0, 4)
MOCK_DELAY_SEC = _env_float("MOCK_DELAY_SEC", 0.08, 0.0, 5.0)
MAX_MESSAGE_CHARS = _env_int("MAX_MESSAGE_CHARS", 50_000, 1_000, 500_000)
MAX_OUTPUT_TOKENS = {
    "low": _env_int("MAX_OUTPUT_TOKENS_LOW", 1200, 128, 32_000),
    "balanced": _env_int("MAX_OUTPUT_TOKENS_BALANCED", 2400, 128, 32_000),
    "high": _env_int("MAX_OUTPUT_TOKENS_HIGH", 4000, 128, 32_000),
}
MAX_PROVIDER_CALLS_PER_RUN = _env_int("MAX_PROVIDER_CALLS_PER_RUN", 9, 1, 100)
MAX_OUTPUT_TOKENS_PER_RUN = _env_int(
    "MAX_OUTPUT_TOKENS_PER_RUN", 36_000, 128, 1_000_000
)
MAX_INPUT_BYTES_PER_RUN = _env_int(
    "MAX_INPUT_BYTES_PER_RUN", 3_200_000, 1_024, 100_000_000
)
HISTORY_TURNS = _env_int("HISTORY_TURNS", 10, 0, 50)
HISTORY_MAX_CHARS = _env_int("HISTORY_MAX_CHARS", 60_000, 2_000, 500_000)
PEER_MAX_CHARS = _env_int("PEER_MAX_CHARS", 12_000, 1_000, 100_000)
MAX_CONCURRENT_RUNS = _env_int("MAX_CONCURRENT_RUNS", 2, 1, 20)
RATE_LIMIT_PER_MINUTE = _env_int("RATE_LIMIT_PER_MINUTE", 10, 1, 600)
SSE_PING_SEC = _env_float("SSE_PING_SEC", 15.0, 2.0, 60.0)
RUN_RETENTION_SEC = _env_int("RUN_RETENTION_SEC", 3600, 60, 86_400)
PRICE_TABLE_FILE = os.getenv("CLAGE_PRICE_TABLE_FILE", "").strip()
PER_RUN_BUDGET_USD = os.getenv("CLAGE_PER_RUN_BUDGET_USD", "").strip()
DAILY_BUDGET_USD = os.getenv("CLAGE_DAILY_BUDGET_USD", "").strip()
BUDGET_UNKNOWN_POLICY = os.getenv(
    "CLAGE_BUDGET_UNKNOWN_POLICY", "block"
).strip().lower()
if BUDGET_UNKNOWN_POLICY not in {"block", "allow"}:
    BUDGET_UNKNOWN_POLICY = "invalid"
BUDGET_UTC_OFFSET = os.getenv("CLAGE_BUDGET_UTC_OFFSET", "+00:00").strip()
MAX_UNRECONCILED_RESERVATIONS = _env_int(
    "CLAGE_MAX_UNRECONCILED_RESERVATIONS", 10, 1, 1000
)
ATTACHMENT_MAX_BYTES = _env_int(
    "CLAGE_ATTACHMENT_MAX_BYTES", 10 * 1024 * 1024, 1024, 100 * 1024 * 1024
)
ATTACHMENT_MAX_COUNT_PER_CONVERSATION = _env_int(
    "CLAGE_ATTACHMENT_MAX_COUNT_PER_CONVERSATION", 20, 1, 200
)
ATTACHMENT_MAX_TOTAL_BYTES_PER_CONVERSATION = _env_int(
    "CLAGE_ATTACHMENT_MAX_TOTAL_BYTES_PER_CONVERSATION",
    50 * 1024 * 1024,
    1024,
    1024 * 1024 * 1024,
)
ATTACHMENT_MAX_PER_TURN = _env_int("CLAGE_ATTACHMENT_MAX_PER_TURN", 8, 1, 32)
ATTACHMENT_TTL_SEC = _env_int(
    "CLAGE_ATTACHMENT_TTL_SEC", 30 * 86_400, 3600, 365 * 86_400
)
ATTACHMENT_TEXT_MAX_CHARS = _env_int(
    "CLAGE_ATTACHMENT_TEXT_MAX_CHARS", 100_000, 1000, 1_000_000
)
ATTACHMENT_TOTAL_TEXT_MAX_CHARS = _env_int(
    "CLAGE_ATTACHMENT_TOTAL_TEXT_MAX_CHARS", 200_000, 1000, 2_000_000
)
ATTACHMENT_PDF_MAX_PAGES = _env_int("CLAGE_ATTACHMENT_PDF_MAX_PAGES", 50, 1, 500)
CONVERSATION_MEMORY_MAX_CHARS = _env_int(
    "CLAGE_CONVERSATION_MEMORY_MAX_CHARS", 20_000, 500, 200_000
)
WEB_SEARCH_ENABLED = _env_bool("CLAGE_WEB_SEARCH_ENABLED", True)
WEB_SEARCH_MAX_USES = _env_int("CLAGE_WEB_SEARCH_MAX_USES", 3, 1, 10)

WEB_SEARCH_CAPABILITIES = {
    "claude": {
        "supported": True,
        "tool": "web_search_20250305",
        "hard_max_uses": WEB_SEARCH_MAX_USES,
    },
    "gemini": {
        "supported": True,
        "tool": "google_search",
        "hard_max_uses": None,
    },
    "chatgpt": {
        "supported": True,
        "tool": "web_search",
        "hard_max_uses": None,
    },
    "grok": {
        "supported": True,
        "tool": "web_search",
        "hard_max_uses": None,
    },
}

# 組織全体のusage/cost取得は通常の推論キーと権限を分離する。既定OFFで、
# 有効化しても読み取り専用endpoint以外は呼ばない。
ADMIN_TELEMETRY_ENABLED = _env_bool("CLAGE_ADMIN_TELEMETRY_ENABLED", False)
ADMIN_TELEMETRY_CACHE_SEC = _env_int(
    "CLAGE_ADMIN_TELEMETRY_CACHE_SEC", 300, 60, 3600
)
ADMIN_TELEMETRY_LOOKBACK_DAYS = _env_int(
    "CLAGE_ADMIN_TELEMETRY_LOOKBACK_DAYS", 7, 1, 31
)
ADMIN_TELEMETRY_TIMEOUT_SEC = _env_float(
    "CLAGE_ADMIN_TELEMETRY_TIMEOUT_SEC", 15.0, 3.0, 60.0
)
ANTHROPIC_ADMIN_KEY = os.getenv("ANTHROPIC_ADMIN_KEY", "").strip()
OPENAI_ADMIN_KEY = os.getenv("OPENAI_ADMIN_KEY", "").strip()
XAI_MANAGEMENT_KEY = os.getenv("XAI_MANAGEMENT_KEY", "").strip()
XAI_TEAM_ID = os.getenv("XAI_TEAM_ID", "").strip()

runtime_settings = RuntimeSettingsStore(DATA_DIR, WORKERS)

@dataclass(frozen=True, slots=True)
class ProviderStatus:
    name: str
    label: str
    configured: bool
    mode: str
    models: dict[str, str]

    def public_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "configured": self.configured,
            "mode": self.mode,
            "models": dict(self.models),
        }


def has_key(name: str) -> bool:
    key_name = _ENV_KEYS.get(name)
    return bool(key_name and os.getenv(key_name, "").strip())


def secret_values() -> tuple[str, ...]:
    """公開データから除去すべき、現在設定中の秘密値だけを返す。"""
    values = [AUTH_TOKEN]
    values.extend(
        os.getenv(key_name, "").strip()
        for key_name in _ENV_KEYS.values()
    )
    values.extend((ANTHROPIC_ADMIN_KEY, OPENAI_ADMIN_KEY, XAI_MANAGEMENT_KEY))
    return tuple(dict.fromkeys(value for value in values if value))


def model_for(name: str, tier: str) -> str:
    tier = tier if tier in {"low", "balanced", "high"} else "balanced"
    runtime = runtime_settings.snapshot()
    override = (runtime.get("models") or {}).get(name, {}).get(tier)
    if isinstance(override, str) and override:
        return override
    env_name = f"{name.upper()}_MODEL_{tier.upper()}"
    return os.getenv(env_name, DEFAULT_MODELS[name][tier]).strip() or DEFAULT_MODELS[name][tier]


def provider_status(name: str) -> ProviderStatus:
    configured = has_key(name)
    available_as_mock = (
        not LIVE_API_ENABLED or mode() == "mock" or INCLUDE_MOCKS_WHEN_MIXED
    )
    return ProviderStatus(
        name=name,
        label=LABELS[name],
        configured=configured,
        mode=(
            "live"
            if LIVE_API_ENABLED and configured
            else ("mock" if available_as_mock else "disabled")
        ),
        models={tier: model_for(name, tier) for tier in ("low", "balanced", "high")},
    )


def statuses() -> list[dict]:
    return [provider_status(name).public_dict() for name in WORKERS]


def mode() -> str:
    if not LIVE_API_ENABLED:
        return "mock"
    keyed = sum(1 for name in WORKERS if has_key(name))
    if keyed == 0:
        return "mock"
    if keyed == len(WORKERS):
        return "live"
    return "mixed"


def active_workers() -> list[str]:
    if not LIVE_API_ENABLED or mode() == "mock" or INCLUDE_MOCKS_WHEN_MIXED:
        return list(WORKERS)
    return [name for name in WORKERS if has_key(name)]


def get_provider(name: str, tier: str = "balanced") -> Provider:
    if name not in WORKERS:
        raise ValueError(f"不明なプロバイダ: {name}")
    model = model_for(name, tier)
    key = os.getenv(_ENV_KEYS[name], "").strip()
    if not LIVE_API_ENABLED or not key:
        return MockProvider(name, delay=MOCK_DELAY_SEC)
    common = {
        "name": name,
        "model": model,
        "api_key": key,
        "retries": HTTP_RETRIES,
    }
    if name == "claude":
        return AnthropicProvider(**common)
    if name == "gemini":
        return GeminiProvider(**common)
    if name == "chatgpt":
        return OpenAIProvider(**common)
    return XAIProvider(**common)


def synthesizer_name() -> str:
    if not LIVE_API_ENABLED:
        return "synthesizer"
    runtime_requested = runtime_settings.snapshot().get("synthesizer_provider")
    requested = (
        str(runtime_requested).strip().lower()
        if runtime_requested not in {None, "auto"}
        else os.getenv("SYNTHESIZER_PROVIDER", "auto").strip().lower()
    )
    if requested in WORKERS and has_key(requested):
        return requested
    for name in ("claude", "chatgpt", "gemini", "grok"):
        if has_key(name):
            return name
    return "synthesizer"


def get_synthesizer(tier: str = "balanced") -> Provider:
    name = synthesizer_name()
    if name == "synthesizer":
        return MockProvider(name, delay=MOCK_DELAY_SEC)
    provider = get_provider(name, tier)
    provider.model = synthesizer_model_for(tier)
    return provider


def synthesizer_model_for(tier: str = "balanced") -> str:
    """統合役が実際に使うモデル名を、APIクライアント生成なしで返す。"""
    tier = tier if tier in {"low", "balanced", "high"} else "balanced"
    name = synthesizer_name()
    if name == "synthesizer":
        return "mock"
    runtime_override = (runtime_settings.snapshot().get("synthesizer_models") or {}).get(tier)
    if isinstance(runtime_override, str) and runtime_override:
        return runtime_override
    override = os.getenv(f"SYNTHESIZER_MODEL_{tier.upper()}", "").strip()
    return override or model_for(name, tier)


def public_settings() -> dict:
    """秘密値を一切含まない設定スナップショット。"""
    synth = synthesizer_name()
    runtime = runtime_settings.snapshot()
    catalog = {
        provider: sorted(
            {
                *DEFAULT_MODELS[provider].values(),
                *(runtime.get("models") or {}).get(provider, {}).values(),
                *(model_for(provider, tier) for tier in ("low", "balanced", "high")),
            }
        )
        for provider in WORKERS
    }
    return {
        "mode": mode(),
        "live_api_enabled": LIVE_API_ENABLED,
        "providers": statuses(),
        "active_workers": active_workers(),
        "include_mock_providers": INCLUDE_MOCKS_WHEN_MIXED,
        "synthesizer": synth,
        "runtime_settings": {
            **runtime,
            "writable": True,
            "catalog": catalog,
            "effective_synthesizer_models": {
                tier: synthesizer_model_for(tier)
                for tier in ("low", "balanced", "high")
            },
        },
        "auth_required": bool(AUTH_TOKEN),
        "single_process_enforced": True,
        "admin_telemetry": admin_telemetry_public_settings(),
        "web_search": {
            "enabled": WEB_SEARCH_ENABLED,
            "default": False,
            "max_uses": WEB_SEARCH_MAX_USES,
            "providers": {
                name: dict(WEB_SEARCH_CAPABILITIES[name]) for name in WORKERS
            },
            "strict_total_limit": False,
        },
        "limits": {
            "max_message_chars": MAX_MESSAGE_CHARS,
            "max_output_tokens": dict(MAX_OUTPUT_TOKENS),
            "max_provider_calls_per_run": MAX_PROVIDER_CALLS_PER_RUN,
            "max_output_tokens_per_run": MAX_OUTPUT_TOKENS_PER_RUN,
            "max_input_bytes_per_run": MAX_INPUT_BYTES_PER_RUN,
            "history_turns": HISTORY_TURNS,
            "max_concurrent_runs": MAX_CONCURRENT_RUNS,
            "rate_limit_per_minute": RATE_LIMIT_PER_MINUTE,
            "http_retries": HTTP_RETRIES,
            "attachment_max_bytes": ATTACHMENT_MAX_BYTES,
            "attachment_max_per_turn": ATTACHMENT_MAX_PER_TURN,
            "attachment_ttl_sec": ATTACHMENT_TTL_SEC,
            "conversation_memory_max_chars": CONVERSATION_MEMORY_MAX_CHARS,
        },
    }


def admin_telemetry_public_settings() -> dict:
    """管理資格情報の値を含めず、取得可否だけを返す。"""
    return {
        "enabled": ADMIN_TELEMETRY_ENABLED,
        "cache_seconds": ADMIN_TELEMETRY_CACHE_SEC,
        "lookback_days": ADMIN_TELEMETRY_LOOKBACK_DAYS,
        "providers": {
            "claude": {
                "supported": True,
                "configured": bool(ANTHROPIC_ADMIN_KEY),
                "credential": "separate_admin_key",
            },
            "chatgpt": {
                "supported": True,
                "configured": bool(OPENAI_ADMIN_KEY),
                "credential": "separate_admin_key",
            },
            "gemini": {
                "supported": False,
                "configured": False,
                "credential": "ai_studio_portal_only",
            },
            "grok": {
                "supported": True,
                "configured": bool(XAI_MANAGEMENT_KEY),
                "credential": "separate_management_key",
                "team_id_configured": bool(XAI_TEAM_ID),
            },
        },
    }
