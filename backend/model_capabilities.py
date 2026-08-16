# -*- coding: utf-8 -*-
"""Provider modelのcapability表を1箇所に持つ、依存ゼロのモジュール。

`config.py` は plan 表示のために、`providers/anthropic.py` は実送信payloadの
組み立てのために同じ「どのmodelがどの機能に対応するか」を必要とする。両者が
それぞれ独自のprefix tupleを持つと、片方だけ更新されたときに
「planでは pinned な effort を表示するのに、実送信では output_config が落ちる」
といった不整合が起きる(issue #21-1)。

`config` は `providers` をimportするため、`providers` から `config` を
importすると循環参照になる。そのため本モジュールは何にも依存しない葉として
置き、双方がここを参照する。
"""

from __future__ import annotations


# Claude model familyのcapability表(prefix → 対応capability)。
#
# - ``effort``            : ``output_config.effort`` を送れる(=AUTO推論policyが
#                           固定effortへ解決できる)。
# - ``adaptive_thinking`` : ``thinking={"type": "adaptive"}`` を明示送信する。
#                           Fable / Mythosは常時adaptiveのため送らない。
#                           Opus 4.5はmanual thinking世代のため送らない。
# - ``dynamic_web_search``: 新しい ``web_search_20260318`` tool版を使う。
#
# 新しいClaude modelを足すときは、この表の1行だけを編集すればよい。
CLAUDE_MODEL_CAPABILITIES: tuple[tuple[str, frozenset[str]], ...] = (
    ("claude-fable-5", frozenset({"effort", "dynamic_web_search"})),
    ("claude-mythos-5", frozenset({"effort", "dynamic_web_search"})),
    ("claude-mythos-preview", frozenset({"effort", "dynamic_web_search"})),
    ("claude-opus-4-5", frozenset({"effort"})),
    (
        "claude-opus-4-6",
        frozenset({"effort", "adaptive_thinking", "dynamic_web_search"}),
    ),
    (
        "claude-opus-4-7",
        frozenset({"effort", "adaptive_thinking", "dynamic_web_search"}),
    ),
    (
        "claude-opus-4-8",
        frozenset({"effort", "adaptive_thinking", "dynamic_web_search"}),
    ),
    (
        "claude-sonnet-4-6",
        frozenset({"effort", "adaptive_thinking", "dynamic_web_search"}),
    ),
    (
        "claude-sonnet-5",
        frozenset({"effort", "adaptive_thinking", "dynamic_web_search"}),
    ),
)


def claude_prefixes_with(capability: str) -> tuple[str, ...]:
    """指定capabilityに対応するClaude model prefixを表から導出する。"""
    return tuple(
        prefix
        for prefix, capabilities in CLAUDE_MODEL_CAPABILITIES
        if capability in capabilities
    )


def matches_model_prefix(model: str, prefixes: tuple[str, ...]) -> bool:
    """model IDが、いずれかのprefixで始まるかを大小文字無視で判定する。"""
    normalized = model.strip().lower()
    return any(normalized.startswith(prefix) for prefix in prefixes)


CLAUDE_EFFORT_MODEL_PREFIXES = claude_prefixes_with("effort")
CLAUDE_ADAPTIVE_THINKING_MODEL_PREFIXES = claude_prefixes_with("adaptive_thinking")
CLAUDE_DYNAMIC_WEB_SEARCH_MODEL_PREFIXES = claude_prefixes_with("dynamic_web_search")
