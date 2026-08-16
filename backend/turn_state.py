# -*- coding: utf-8 -*-
"""ターンの状態表現を `status` 単一ソースへ正規化する純粋ヘルパー。

0.2.0までのturnは `status` に加えて `cancelled` / `failed` / `interrupted` を
並列に持ち、しかも書き手ごとに意味が違った(起動時recoverは
status="interrupted" かつ failed=True を書き、通常完了は failed=(status=="failed")
を書く)。派生値が保存されるとどれが正なのか決まらないため、**保存するのは
`status` だけ**にし、派生booleanはAPI応答を作るときにここで算出する。

`usage_may_be_incomplete` は派生値ではない。「Providerのusage(課金根拠)を
観測できたか」という課金台帳側の事実を表すため、保存し続ける。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


#: 保存turnが取り得る状態。これ以外は保存しない。
TURN_STATUSES = ("running", "completed", "cancelled", "failed", "interrupted")

#: statusから算出される、保存しない派生フィールド。
DERIVED_TURN_FLAGS = ("cancelled", "failed", "interrupted")


def normalized_status(turn: dict[str, Any]) -> str:
    """保存turnの正規のstatusを返す。旧形式の派生booleanからも復元する。"""
    status = turn.get("status")
    if isinstance(status, str) and status in TURN_STATUSES:
        return status
    # schema_version 1 の互換: statusが無い/未知なら派生booleanから復元する。
    if turn.get("cancelled") is True:
        return "cancelled"
    if turn.get("interrupted") is True:
        return "interrupted"
    if turn.get("failed") is True:
        return "failed"
    return "completed"


def derived_flags(status: str) -> dict[str, bool]:
    """statusから、API応答へ載せる派生booleanを算出する。

    0.2.0以前に2人の書き手が実際に書いていた組み合わせをそのまま再現する
    (cancelledなturnは failed=False、interruptedなturnは failed=True)。
    """
    return {
        "cancelled": status == "cancelled",
        "failed": status in {"failed", "interrupted"},
        "interrupted": status == "interrupted",
    }


def replay_outcome(status: str) -> dict[str, bool]:
    """SSE replayの error / done イベントへ載せる終端判定。

    保存turnの派生booleanとは意味が違う点に注意。SSEの `done` における
    `failed` は「完了しなかった」を表すので、cancelledもfailed扱いになる。
    """
    return {
        "failed": status != "completed",
        "cancelled": status == "cancelled",
        "interrupted": status in {"running", "interrupted"},
    }


def normalize_turn_for_storage(turn: dict[str, Any]) -> None:
    """保存直前のturnを、statusだけを持つ正規形へその場で書き換える。"""
    if not isinstance(turn, dict):
        return
    turn["status"] = normalized_status(turn)
    for key in DERIVED_TURN_FLAGS:
        turn.pop(key, None)
    # SSEイベントの丸ごとコピー。0.2.0で廃止(replayはturnフィールドから再構成)。
    turn.pop("event_log", None)


def public_turn(turn: dict[str, Any]) -> dict[str, Any]:
    """保存turnから、派生booleanを復元したAPI応答用のcopyを作る。"""
    if not isinstance(turn, dict):
        return turn
    public = deepcopy(turn)
    status = normalized_status(public)
    public["status"] = status
    public.update(derived_flags(status))
    return public


def public_conversation(conversation: dict[str, Any]) -> dict[str, Any]:
    """会話全体をAPI応答用に整える(turnの派生booleanを復元する)。"""
    if not isinstance(conversation, dict):
        return conversation
    public = deepcopy(conversation)
    turns = public.get("turns")
    if isinstance(turns, list):
        public["turns"] = [
            public_turn(turn) if isinstance(turn, dict) else turn for turn in turns
        ]
    return public
