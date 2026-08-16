# -*- coding: utf-8 -*-
"""API応答のPydanticモデル。FastAPIのOpenAPIに実際の契約を出すためのもの。

方針:

- **一覧系は `{"items": [...], ...メタ}`**。裸の配列は返さない(後からメタ情報を
  足せず、破損件数のような「一覧に載らなかったもの」を報告できないため)。
- 会話・ターン・添付のように、Providerや将来の機能で**キーが増える**構造は
  `extra="allow"` にする。response_modelで宣言していないキーを黙って落とすと、
  クライアントから見て「あるはずの情報が消える」事故になるため。
- エラーは全て :class:`ErrorResponse`。codeの一覧は `docs/API_ERRORS.md`。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    """`{code, message, ...復旧に必要な追加情報}`。"""

    model_config = ConfigDict(extra="allow")

    code: str = Field(description="機械判別用の安定したsnake_case識別子")
    message: str = Field(description="人間向けの説明。分岐条件には使わない")


class ErrorResponse(BaseModel):
    detail: ErrorDetail


class ConversationSummary(BaseModel):
    """会話一覧・検索結果の1件。ターン本文は含まない。"""

    model_config = ConfigDict(extra="allow")

    id: str
    title: str
    created_at: str
    updated_at: str
    turn_count: int
    preview: str


class CorruptConversation(BaseModel):
    """読み取れなかった会話ファイル。正本は移動せずその場に残す。"""

    id: str = Field(description="ファイル名から推定した会話ID")
    file: str
    reason: str = Field(
        description=(
            "unreadable / invalid_json / not_an_object / missing_turns / "
            "invalid_id / id_mismatch / unsupported_schema_version"
        )
    )


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]
    corrupt_count: int = Field(
        default=0,
        description="読み取れず一覧に載せられなかったファイル数。0でないなら要対処",
    )
    corrupt: list[CorruptConversation] = Field(default_factory=list)


class ConversationSearchResponse(BaseModel):
    query: str
    items: list[ConversationSummary]


class Turn(BaseModel):
    """1回の会議。保存形との違いは `docs`/HANDOFF の表を参照。

    `cancelled` / `failed` / `interrupted` は保存されず、`status` から算出した
    派生値としてAPI応答にだけ現れる。
    """

    model_config = ConfigDict(extra="allow")

    request_id: str
    status: str = Field(
        description="running / completed / cancelled / failed / interrupted"
    )
    cancelled: bool = False
    failed: bool = False
    interrupted: bool = False
    message: str | None = None
    answers: dict[str, Any] = Field(default_factory=dict)
    synthesis: dict[str, Any] | None = None
    insights: dict[str, Any] | None = None


class ConversationMemory(BaseModel):
    model_config = ConfigDict(extra="allow")

    revision: int = 0
    text: str = ""
    updated_at: str = ""


class Conversation(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int
    id: str
    title: str
    created_at: str
    updated_at: str
    memory: ConversationMemory
    turns: list[Turn] = Field(default_factory=list)


class Attachment(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    mime_type: str
    bytes: int | None = None


class AttachmentListResponse(BaseModel):
    items: list[Attachment]


class OkResponse(BaseModel):
    """副作用だけを持つ操作の共通応答。"""

    model_config = ConfigDict(extra="allow")

    ok: bool = True


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool
    version: str
    mode: str
    active_workers: list[str]
    synthesizer: str
    auth_required: bool
    single_process_enforced: bool


class CancelRunResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool
    request_id: str
    cancelled: bool
    terminal_outcome: str | None = None


#: 全endpoint共通で登録するエラー応答(OpenAPIへ契約として出す)。
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "リクエストが不正"},
    401: {"model": ErrorResponse, "description": "認証に失敗"},
    404: {"model": ErrorResponse, "description": "対象が存在しない"},
    409: {"model": ErrorResponse, "description": "状態またはIDの競合"},
    422: {"model": ErrorResponse, "description": "検証・ガードにより実行不可"},
    429: {"model": ErrorResponse, "description": "レート制限"},
}
