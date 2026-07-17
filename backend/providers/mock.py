# -*- coding: utf-8 -*-
"""モックプロバイダ。APIキーが無くても動くデモ用のダミー応答を返す。

ポートフォリオの閲覧者がキー無しで会議フローを試せるようにするための最小実装。
擬似的なストリーミング遅延を入れて、実際のAI呼び出しに近い体験にする。
"""
import asyncio

from .base import Provider

# 各AIの「らしさ」を軽く出すための定型フレーズ(デモ用)
_FLAVOR = {
    "claude": "丁寧に前提を整理すると、",
    "chatgpt": "要点を先に述べると、",
    "gemini": "多角的に見ると、",
    "grok": "率直に言えば、",
    "synthesizer": "各AIの回答を統合すると、",
}


class MockProvider(Provider):
    def __init__(self, name: str, delay: float = 0.8):
        self.name = name
        self._delay = delay

    async def complete(self, prompt: str) -> str:
        await asyncio.sleep(self._delay)  # 擬似レイテンシ
        head = _FLAVOR.get(self.name, "")
        excerpt = prompt.strip().replace("\n", " ")[:40]
        return (
            f"{head}「{excerpt}」についてのデモ回答です。"
            f"(これは {self.name} のモック応答。APIキーを設定すると実際のAIが答えます)"
        )
