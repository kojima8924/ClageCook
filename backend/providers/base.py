# -*- coding: utf-8 -*-
"""AIプロバイダの抽象基底。

各社の公式API(Anthropic/OpenAI/Gemini/xAI)やモックを、同じインターフェースで
扱えるようにする。orchestrator はこの抽象だけに依存し、接続方式を知らない。
"""
from abc import ABC, abstractmethod


class Provider(ABC):
    """1つのAIバックエンドを表す抽象プロバイダ。"""

    #: 表示名/識別子(例: "claude", "chatgpt", "gemini", "grok")
    name: str

    @abstractmethod
    async def complete(self, prompt: str) -> str:
        """プロンプトに対する回答テキストを返す(失敗時は例外)。"""
        raise NotImplementedError
