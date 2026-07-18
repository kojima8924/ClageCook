# -*- coding: utf-8 -*-
"""キーなしでも画面と会議フローを試せる明示的なモック。"""

from __future__ import annotations

import asyncio
import re
import time

from .base import CompletionRequest, CompletionResult, Provider


_FLAVOR = {
    "claude": "前提と注意点を整理すると、",
    "gemini": "複数の観点から見ると、",
    "chatgpt": "結論を先にまとめると、",
    "grok": "率直に検討すると、",
    "synthesizer": "各回答の共通点と相違点を統合すると、",
}


class MockProvider(Provider):
    is_mock = True

    def __init__(self, name: str, model: str = "mock", delay: float = 0.08) -> None:
        self.name = name
        self.model = model
        self._delay = max(0.0, delay)

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        started = time.perf_counter()
        await asyncio.sleep(self._delay)
        subject = _subject(request.prompt)
        if self.name == "synthesizer":
            text = (
                f"**デモ統合回答:** 「{subject}」について、各AIの視点を"
                "比較して共通点と注意点をまとめました。\n\n"
                "これは安全なモックです。実APIを使うには `.env` のAPIキーに加えて、"
                "`CLAGE_LIVE_API_ENABLED=true` を明示してください。"
            )
        elif "相互批評ラウンド" in request.system:
            text = (
                f"{_FLAVOR.get(self.name, '')}他の回答と照合し、「{subject}」に関する"
                "初回回答の注意点を補ったデモ最終回答です。\n\n"
                "実APIキーを設定すると、ここで実際に相互批評します。"
            )
        else:
            text = (
                f"{_FLAVOR.get(self.name, '')}「{subject}」へのデモ回答です。\n\n"
                "これは安全なモックです。実APIを使うには `.env` のAPIキーに加えて、"
                "`CLAGE_LIVE_API_ENABLED=true` を明示してください。"
            )
        return CompletionResult(
            provider=self.name,
            model=self.model,
            text=text,
            elapsed_sec=round(time.perf_counter() - started, 3),
            finish_reason="completed",
            request_audit={
                "http_attempts": 0,
                "retry_count": 0,
                "outcome": "mock",
                "usage_may_be_incomplete": False,
            },
            mock=True,
        )


def _subject(prompt: str) -> str:
    question = re.search(r"<question>\s*(.*?)\s*</question>", prompt, re.DOTALL)
    if question:
        value = question.group(1)
    elif "[今回の質問]" in prompt:
        value = prompt.rsplit("[今回の質問]", 1)[1]
    else:
        quoted = re.search(r"「([^」]{1,300})」", prompt)
        value = quoted.group(1) if quoted else prompt
    value = re.sub(r"<[^>]+>", " ", value)
    value = " ".join(value.strip().split())[:120]
    return value or "この質問"
