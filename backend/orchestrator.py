# -*- coding: utf-8 -*-
"""会議オーケストレーション: 複数AIに並列で問い、統合役が1つにまとめる。

このアプリのコアバリュー。接続方式(モック/各社API)に依存せず、config経由で
取得したProviderだけを使う。イベントは emit(event, data) でSSEへ流す。
"""
import asyncio

import config


async def run_turn(message: str, emit):
    """1ターンを実行し、meta→answer(×N)→synthesis の順にイベントを流す。"""
    await emit("meta", {"backends": config.WORKERS, "mode": config.mode()})

    answers: dict[str, dict] = {}

    async def ask(name: str):
        provider = config.get_provider(name)
        try:
            text = await provider.complete(message)
            result = {"source": name, "ok": True, "text": text}
        except Exception as e:  # プロバイダ失敗は1AI分のエラーとして扱う
            result = {"source": name, "ok": False, "error": str(e)}
        answers[name] = result
        await emit("answer", result)

    # 4AIを並列実行(完了順は問わず、全て終わってから統合へ)
    await asyncio.gather(*[ask(name) for name in config.WORKERS])

    # 統合役: 成功した回答を1つにまとめる
    ok = {b: a["text"] for b, a in answers.items() if a.get("ok")}
    if not ok:
        await emit("synthesis", {"ok": False, "error": "全AIが失敗したため統合できません"})
        return
    synth = config.get_synthesizer()
    joined = "\n\n".join(f"[{b}の回答]\n{t}" for b, t in ok.items())
    prompt = (
        "以下は同じ質問への複数AIの回答です。一致点・相違点を踏まえ、"
        f"最も正確で有用な統合回答を作成してください。\n\n[質問]\n{message}\n\n{joined}"
    )
    try:
        text = await synth.complete(prompt)
        await emit("synthesis", {"ok": True, "text": text, "source": "synthesizer"})
    except Exception as e:
        await emit("synthesis", {"ok": False, "error": str(e)})
