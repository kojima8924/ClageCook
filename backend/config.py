# -*- coding: utf-8 -*-
"""プロバイダ解決。各社APIキーがあれば実プロバイダ、無ければモックを返す。

現状は全てモック。各社の実プロバイダ(anthropic/openai/gemini/xai)を実装したら
ここでキーの有無を見て差し替える(orchestratorは一切変更不要)。
"""
import os

from providers import MockProvider

# 会議に参加するAI(表示順)。名前の由来 Cla-ge-Co-ok
WORKERS = ["claude", "gemini", "chatgpt", "grok"]

# 各AIが参照する環境変数名(将来、実プロバイダ切替の判定に使う)
_ENV_KEYS = {
    "claude": "ANTHROPIC_API_KEY",
    "chatgpt": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "grok": "XAI_API_KEY",
}


def _has_key(name: str) -> bool:
    return bool(os.environ.get(_ENV_KEYS.get(name, ""), "").strip())


def get_provider(name: str):
    # TODO: _has_key(name) が真なら各社の実プロバイダを返す
    return MockProvider(name)


def get_synthesizer():
    # TODO: 統合役も実プロバイダに差し替え可能にする
    return MockProvider("synthesizer")


def mode() -> str:
    """現在の動作モード。全AIにキーがあれば live、一部なら mixed、無ければ mock。"""
    keyed = sum(1 for w in WORKERS if _has_key(w))
    if keyed == 0:
        return "mock"
    if keyed == len(WORKERS):
        return "live"
    return "mixed"
