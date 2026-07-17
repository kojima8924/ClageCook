# -*- coding: utf-8 -*-
"""Clage Cook OSS backend (FastAPI)。

4AI会議をSSEでストリーミングする最小API。接続方式はprovidersで抽象化され、
現状はモック(キー不要)。各社APIキーを .env に入れると実AIに切り替わる(予定)。
"""
import asyncio
import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import config
import orchestrator

app = FastAPI(title="Clage Cook OSS")

# Flutter(Web/デスクトップ/モバイル)からのアクセスを許可する
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"ok": True, "backends": config.WORKERS, "mode": config.mode()}


class ChatRequest(BaseModel):
    message: str


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """会議を実行し、meta/answer/synthesis/done をSSEで返す。"""

    async def stream():
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(event: str, data: dict):
            await queue.put((event, data))

        async def run():
            try:
                await orchestrator.run_turn(req.message, emit)
            finally:
                await queue.put(None)  # 終端マーカー

        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, data = item
                yield _sse(event, data)
            yield _sse("done", {})
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream")
