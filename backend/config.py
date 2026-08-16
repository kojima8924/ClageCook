# -*- coding: utf-8 -*-
"""環境変数からBYOK設定を解決する。

キーそのものはこのモジュール外へ返さない。実APIは明示的に武装するまで無効で、
既定ではキーの有無に関係なく4体モックだけを使う。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from model_capabilities import CLAUDE_EFFORT_MODEL_PREFIXES
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


# 設定値の読み取りで「指定値と実効値が食い違った」ものを記録する。不正値を
# 黙って既定へ落としたり、範囲外を黙ってクランプすると、利用者は自分の設定が
# 効いていないことに気付けない。起動時にまとめて警告するための帯域外の記録。
ENV_ADJUSTMENTS: list[dict[str, str]] = []


def _record_adjustment(name: str, requested: str, effective: object, reason: str) -> None:
    ENV_ADJUSTMENTS.append(
        {
            "name": name,
            "requested": requested,
            "effective": str(effective),
            "reason": reason,
        }
    )


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    _record_adjustment(name, raw, default, "not_a_boolean")
    return default


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        _record_adjustment(name, raw, default, "not_an_integer")
        return default
    clamped = max(minimum, min(value, maximum))
    if clamped != value:
        _record_adjustment(
            name,
            raw,
            clamped,
            f"outside_allowed_range[{minimum},{maximum}]",
        )
    return clamped


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        _record_adjustment(name, raw, default, "not_a_number")
        return default
    clamped = max(minimum, min(value, maximum))
    if clamped != value:
        _record_adjustment(
            name,
            raw,
            clamped,
            f"outside_allowed_range[{minimum},{maximum}]",
        )
    return clamped


# ベンダー標準名のためCLAGE_接頭辞を付けない環境変数。他は全てCLAGE_付き。
VENDOR_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "XAI_API_KEY",
    "ANTHROPIC_ADMIN_KEY",
    "OPENAI_ADMIN_KEY",
    "XAI_MANAGEMENT_KEY",
    "XAI_TEAM_ID",
)

# 0.2.0で CLAGE_ 接頭辞へ統一した際の旧名 → 新名。汎用名(HISTORY_TURNS等)は
# docker-composeや共有シェル環境で他プロセスと衝突するため廃止した。旧名を
# 黙って無視すると設定が効かないことに気付けないので、起動時に警告する。
RENAMED_ENV_NAMES = {
    "INCLUDE_MOCK_PROVIDERS": "CLAGE_INCLUDE_MOCK_PROVIDERS",
    "HTTP_TIMEOUT_SEC": "CLAGE_HTTP_TIMEOUT_SEC",
    "HTTP_RETRIES": "CLAGE_HTTP_RETRIES",
    "MOCK_DELAY_SEC": "CLAGE_MOCK_DELAY_SEC",
    "MAX_MESSAGE_CHARS": "CLAGE_MAX_MESSAGE_CHARS",
    "MAX_PROVIDER_CALLS_PER_RUN": "CLAGE_MAX_PROVIDER_CALLS_PER_RUN",
    "MAX_OUTPUT_TOKENS_PER_RUN": "CLAGE_MAX_OUTPUT_TOKENS_PER_RUN",
    "MAX_INPUT_BYTES_PER_RUN": "CLAGE_MAX_INPUT_BYTES_PER_RUN",
    "HISTORY_TURNS": "CLAGE_HISTORY_TURNS",
    "HISTORY_MAX_CHARS": "CLAGE_HISTORY_MAX_CHARS",
    "PEER_MAX_CHARS": "CLAGE_PEER_MAX_CHARS",
    "MAX_CONCURRENT_RUNS": "CLAGE_MAX_CONCURRENT_RUNS",
    "RATE_LIMIT_PER_MINUTE": "CLAGE_RATE_LIMIT_PER_MINUTE",
    "SSE_PING_SEC": "CLAGE_SSE_PING_SEC",
    "RUN_RETENTION_SEC": "CLAGE_RUN_RETENTION_SEC",
    "SYNTHESIZER_PROVIDER": "CLAGE_SYNTHESIZER_PROVIDER",
    **{
        f"SYNTHESIZER_MODEL_{tier}": f"CLAGE_SYNTHESIZER_MODEL_{tier}"
        for tier in ("LOW", "BALANCED", "HIGH")
    },
    **{
        f"{provider}_MODEL_{tier}": f"CLAGE_{provider}_MODEL_{tier}"
        for provider in ("CLAUDE", "GEMINI", "CHATGPT", "GROK")
        for tier in ("LOW", "BALANCED", "HIGH")
    },
    **{
        f"{provider}_MAX_OUTPUT_TOKENS_{tier}": (
            f"CLAGE_{provider}_MAX_OUTPUT_TOKENS_{tier}"
        )
        for provider in ("CLAUDE", "GEMINI", "CHATGPT", "GROK")
        for tier in ("LOW", "BALANCED", "HIGH")
    },
}


def env_adjustment_for(name: str) -> dict[str, str] | None:
    """指定した環境変数が、指定値と違う実効値になっていれば記録を返す。"""
    for adjustment in ENV_ADJUSTMENTS:
        if adjustment["name"] == name:
            return adjustment
    return None


def deprecated_env_names() -> list[tuple[str, str]]:
    """現在の環境に残っている旧名の (旧名, 新名) 一覧を返す。"""
    return [
        (old, new)
        for old, new in sorted(RENAMED_ENV_NAMES.items())
        if os.getenv(old) is not None and os.getenv(new) is None
    ]


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
INCLUDE_MOCKS_WHEN_MIXED = _env_bool("CLAGE_INCLUDE_MOCK_PROVIDERS", False)
LIVE_API_ENABLED = _env_bool("CLAGE_LIVE_API_ENABLED", False)
HTTP_TIMEOUT_SEC = _env_float("CLAGE_HTTP_TIMEOUT_SEC", 600.0, 5.0, 1800.0)
# 生成APIは応答喪失時に同じ処理を再度課金され得るため、自動再試行は既定OFF。
HTTP_RETRIES = _env_int("CLAGE_HTTP_RETRIES", 0, 0, 4)
MOCK_DELAY_SEC = _env_float("CLAGE_MOCK_DELAY_SEC", 0.08, 0.0, 5.0)
MAX_MESSAGE_CHARS = _env_int("CLAGE_MAX_MESSAGE_CHARS", 50_000, 1_000, 500_000)
TIERS = ("low", "balanced", "high")
REASONING_MODES = ("auto", "low", "medium", "high")
REASONING_POLICY_VERSION = 1

# Reasoning modelでは内部思考も生成上限を消費する。全Provider共通の小さな
# 上限では本文がほとんど残らないため、Provider特性ごとに十分な余白を持つ。
# 値は契約上の保証ではなく、このアプリが1 callへ許可する安全上限である。
_DEFAULT_MAX_OUTPUT_TOKENS = {
    "claude": {"low": 4_096, "balanced": 8_192, "high": 16_384},
    "gemini": {"low": 8_192, "balanced": 16_384, "high": 32_768},
    "chatgpt": {"low": 4_096, "balanced": 8_192, "high": 16_384},
    "grok": {"low": 4_096, "balanced": 8_192, "high": 16_384},
}

# AUTOは質問文を分類せず、model familyごとの推奨値だけを使う。これにより
# 回答内容へ文体・結論の方向性を加えず、plan時に決定論的に解決できる。
# Claudeのeffort対応modelはmodel_capabilities.pyの表が唯一のソース。
# providers/anthropic.pyの実送信判定と同じ表を参照する(issue #21-1)。
_AUTO_REASONING_POLICIES = {
    "claude": (
        (CLAUDE_EFFORT_MODEL_PREFIXES, "high"),
        (("claude-haiku-",), None),
        (
            ("claude-sonnet-", "claude-opus-", "claude-fable-", "claude-mythos-"),
            None,
        ),
    ),
    "gemini": ((("gemini-",), "medium"),),
    "chatgpt": ((("gpt-", "o1", "o3", "o4"), "medium"),),
    "grok": (
        (("grok-4.5", "grok-4.20"), "high"),
        (("grok-",), "medium"),
    ),
}
MAX_OUTPUT_TOKENS = {
    provider: {
        tier: _env_int(
            f"CLAGE_{provider.upper()}_MAX_OUTPUT_TOKENS_{tier.upper()}",
            default,
            128,
            128_000,
        )
        for tier, default in tiers.items()
    }
    for provider, tiers in _DEFAULT_MAX_OUTPUT_TOKENS.items()
}
MAX_PROVIDER_CALLS_PER_RUN = _env_int("CLAGE_MAX_PROVIDER_CALLS_PER_RUN", 9, 1, 100)
MAX_OUTPUT_TOKENS_PER_RUN = _env_int(
    "CLAGE_MAX_OUTPUT_TOKENS_PER_RUN", 196_608, 128, 1_000_000
)
MAX_INPUT_BYTES_PER_RUN = _env_int(
    "CLAGE_MAX_INPUT_BYTES_PER_RUN", 3_200_000, 1_024, 100_000_000
)
HISTORY_TURNS = _env_int("CLAGE_HISTORY_TURNS", 10, 0, 50)
HISTORY_MAX_CHARS = _env_int("CLAGE_HISTORY_MAX_CHARS", 60_000, 2_000, 500_000)
PEER_MAX_CHARS = _env_int("CLAGE_PEER_MAX_CHARS", 12_000, 1_000, 100_000)
MAX_CONCURRENT_RUNS = _env_int("CLAGE_MAX_CONCURRENT_RUNS", 2, 1, 20)
RATE_LIMIT_PER_MINUTE = _env_int("CLAGE_RATE_LIMIT_PER_MINUTE", 10, 1, 600)
SSE_PING_SEC = _env_float("CLAGE_SSE_PING_SEC", 15.0, 2.0, 60.0)
RUN_RETENTION_SEC = _env_int("CLAGE_RUN_RETENTION_SEC", 3600, 60, 86_400)
PRICE_TABLE_FILE = os.getenv("CLAGE_PRICE_TABLE_FILE", "").strip()
PER_RUN_BUDGET_USD = os.getenv("CLAGE_PER_RUN_BUDGET_USD", "").strip()
DAILY_BUDGET_USD = os.getenv("CLAGE_DAILY_BUDGET_USD", "").strip()

def _env_choice(name: str, default: str, allowed: frozenset[str]) -> str:
    """列挙型の設定値。不正値は帯域内の魔法値にせず、記録して既定へ落とす。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in allowed:
        return value
    _record_adjustment(name, raw, default, "not_an_allowed_choice")
    return default


BUDGET_UNKNOWN_POLICY = _env_choice(
    "CLAGE_BUDGET_UNKNOWN_POLICY",
    "block",
    frozenset({"block", "allow"}),
)
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
        "tool": "web_search_20260318",
        "fallback_tool": "web_search_20250305",
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


@dataclass(frozen=True, slots=True)
class ReasoningResolution:
    requested: str
    effective: str
    api_effort: str | None
    source: str
    pinned: bool

    def public_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "effective": self.effective,
            "source": self.source,
            "pinned": self.pinned,
            "policy_version": REASONING_POLICY_VERSION,
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


def normalized_tier(value: str) -> str:
    return value if value in TIERS else "balanced"


def normalized_reasoning_mode(value: str) -> str:
    return value if value in REASONING_MODES else "auto"


def max_output_tokens_for(name: str, tier: str) -> int:
    """Provider/tierに対応する1 callの生成上限を返す。"""
    resolved_tier = normalized_tier(tier)
    if name == "synthesizer":
        return max(values[resolved_tier] for values in MAX_OUTPUT_TOKENS.values())
    try:
        return MAX_OUTPUT_TOKENS[name][resolved_tier]
    except KeyError as exc:
        raise ValueError(f"不明なプロバイダ: {name}") from exc


def resolve_reasoning(
    name: str,
    model: str,
    requested: str,
    *,
    mock: bool = False,
) -> ReasoningResolution:
    """UIのreasoning modeを、外部APIへ渡す固定effortへ解決する。"""
    mode = normalized_reasoning_mode(requested)
    if mock or name == "synthesizer":
        return ReasoningResolution(mode, "none", None, "mock", True)
    normalized_model = model.strip().lower()
    matched_policy = False
    auto_effort: str | None = None
    for prefixes, effort in _AUTO_REASONING_POLICIES.get(name, ()):
        if any(normalized_model.startswith(prefix) for prefix in prefixes):
            matched_policy = True
            auto_effort = effort
            break
    if matched_policy:
        if auto_effort is None:
            return ReasoningResolution(
                mode,
                "provider_default",
                None,
                "model_unsupported",
                True,
            )
        effort = auto_effort if mode == "auto" else mode
        return ReasoningResolution(
            mode,
            effort,
            effort,
            "model_policy" if mode == "auto" else "explicit",
            True,
        )
    # runtime設定では任意の安全なmodel IDを許可するため、未知modelへ未確認の
    # reasoning fieldを送らない。planへ警告可能なunpinned状態として公開する。
    return ReasoningResolution(
        mode,
        "provider_default",
        None,
        "unknown_model",
        False,
    )


def model_for(
    name: str,
    tier: str,
    *,
    runtime: dict[str, Any] | None = None,
) -> str:
    tier = normalized_tier(tier)
    runtime = runtime_settings.snapshot() if runtime is None else runtime
    override = (runtime.get("models") or {}).get(name, {}).get(tier)
    if isinstance(override, str) and override:
        return override
    env_name = f"CLAGE_{name.upper()}_MODEL_{tier.upper()}"
    return os.getenv(env_name, DEFAULT_MODELS[name][tier]).strip() or DEFAULT_MODELS[name][tier]


def provider_status(
    name: str,
    *,
    runtime: dict[str, Any] | None = None,
) -> ProviderStatus:
    runtime = runtime_settings.snapshot() if runtime is None else runtime
    configured = has_key(name)
    available_as_mock = not LIVE_API_ENABLED or INCLUDE_MOCKS_WHEN_MIXED
    return ProviderStatus(
        name=name,
        label=LABELS[name],
        configured=configured,
        mode=(
            "live"
            if LIVE_API_ENABLED and configured
            else ("mock" if available_as_mock else "disabled")
        ),
        models={
            tier: model_for(name, tier, runtime=runtime)
            for tier in TIERS
        },
    )


def statuses(*, runtime: dict[str, Any] | None = None) -> list[dict]:
    runtime = runtime_settings.snapshot() if runtime is None else runtime
    return [
        provider_status(name, runtime=runtime).public_dict()
        for name in WORKERS
    ]


def mode() -> str:
    if not LIVE_API_ENABLED:
        return "mock"
    keyed = sum(1 for name in WORKERS if has_key(name))
    if keyed == len(WORKERS):
        return "live"
    return "mixed"


def active_workers() -> list[str]:
    if not LIVE_API_ENABLED or INCLUDE_MOCKS_WHEN_MIXED:
        return list(WORKERS)
    return [name for name in WORKERS if has_key(name)]


def get_provider(name: str, tier: str = "balanced") -> Provider:
    if name not in WORKERS:
        raise ValueError(f"不明なプロバイダ: {name}")
    model = model_for(name, tier)
    key = os.getenv(_ENV_KEYS[name], "").strip()
    if not LIVE_API_ENABLED:
        return MockProvider(name, delay=MOCK_DELAY_SEC)
    if not key:
        if INCLUDE_MOCKS_WHEN_MIXED:
            return MockProvider(name, delay=MOCK_DELAY_SEC)
        raise RuntimeError(
            f"{LABELS[name]} APIキーが未設定のためLIVEモードでは利用できません"
        )
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


def synthesizer_name(*, runtime: dict[str, Any] | None = None) -> str:
    if not LIVE_API_ENABLED:
        return "synthesizer"
    runtime = runtime_settings.snapshot() if runtime is None else runtime
    runtime_requested = runtime.get("synthesizer_provider")
    requested = (
        str(runtime_requested).strip().lower()
        if runtime_requested not in {None, "auto"}
        else os.getenv("CLAGE_SYNTHESIZER_PROVIDER", "auto").strip().lower()
    )
    if requested in WORKERS and has_key(requested):
        return requested
    for name in ("claude", "chatgpt", "gemini", "grok"):
        if has_key(name):
            return name
    return "synthesizer"


def get_synthesizer(
    tier: str = "balanced",
    *,
    provider_name: str | None = None,
) -> Provider:
    """統合役を返す。provider_name指定時はplan確定時の選択を維持する。"""
    name = provider_name or synthesizer_name()
    if name not in {*WORKERS, "synthesizer"}:
        raise RuntimeError("統合Providerの指定が不正です")
    if name == "synthesizer":
        if LIVE_API_ENABLED and not INCLUDE_MOCKS_WHEN_MIXED:
            raise RuntimeError(
                "統合に使えるAPIキーが未設定のためLIVEモードでは実行できません"
            )
        return MockProvider(name, delay=MOCK_DELAY_SEC)
    provider = get_provider(name, tier)
    provider.model = (
        synthesizer_model_for(tier)
        if provider_name is None
        else model_for(name, tier)
    )
    return provider


def synthesizer_model_for(
    tier: str = "balanced",
    *,
    runtime: dict[str, Any] | None = None,
    synthesizer: str | None = None,
) -> str:
    """統合役が実際に使うモデル名を、APIクライアント生成なしで返す。"""
    tier = normalized_tier(tier)
    runtime = runtime_settings.snapshot() if runtime is None else runtime
    name = synthesizer or synthesizer_name(runtime=runtime)
    if name == "synthesizer":
        return "mock"
    runtime_override = (runtime.get("synthesizer_models") or {}).get(tier)
    if isinstance(runtime_override, str) and runtime_override:
        return runtime_override
    override = os.getenv(f"CLAGE_SYNTHESIZER_MODEL_{tier.upper()}", "").strip()
    return override or model_for(name, tier, runtime=runtime)


def public_settings() -> dict:
    """秘密値を一切含まない設定スナップショット。"""
    runtime = runtime_settings.snapshot()
    synth = synthesizer_name(runtime=runtime)
    catalog = {
        provider: sorted(
            {
                *DEFAULT_MODELS[provider].values(),
                *(runtime.get("models") or {}).get(provider, {}).values(),
                *(
                    model_for(provider, tier, runtime=runtime)
                    for tier in TIERS
                ),
            }
        )
        for provider in WORKERS
    }
    return {
        "mode": mode(),
        "live_api_enabled": LIVE_API_ENABLED,
        "providers": statuses(runtime=runtime),
        "active_workers": active_workers(),
        "include_mock_providers": INCLUDE_MOCKS_WHEN_MIXED,
        "synthesizer": synth,
        "runtime_settings": {
            **runtime,
            "writable": True,
            "catalog": catalog,
            "effective_synthesizer_models": {
                tier: synthesizer_model_for(
                    tier,
                    runtime=runtime,
                    synthesizer=synth,
                )
                for tier in TIERS
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
            "max_output_tokens": {
                provider: dict(values)
                for provider, values in MAX_OUTPUT_TOKENS.items()
            },
            "reasoning_modes": list(REASONING_MODES),
            "reasoning_policy_version": REASONING_POLICY_VERSION,
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
