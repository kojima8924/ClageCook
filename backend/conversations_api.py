# -*- coding: utf-8 -*-
"""会話CRUD・添付・エクスポートAPIのAPIRouter。

main.pyから分離。

設計上の重要事項: store・attachment_store・_hold_conversation_lock・
_reject_destructive_change_during_run等は、テストが
「monkeypatch.setattr(main, "store", ...)」のようにmainモジュールのglobalを
差し替える前提のため、本モジュールでは import時に束縛せず、必ず呼出時に
`main.store` のように遅延参照する。mainは本モジュールを末尾でimportして
`app.include_router(router)` する(mainより先に本モジュールをimportしない)。
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.background import BackgroundTask

import attachments
import config
import exporting
import policy
import main
import api_schemas
from api_errors import api_error
from api_models import (
    ConversationMemoryUpdateRequest,
    RenameRequest,
    SearchRequest,
    _valid_request_id,
)
from sanitizing import _scrub_public, _scrub_public_dict
import turn_state
from storage import ConversationNotFound, utc_now


logger = logging.getLogger("clage_cook")

router = APIRouter(responses=api_schemas.ERROR_RESPONSES)


def _check_auth(request: Request) -> None:
    """main.check_authへの遅延委譲(認証設定の一元管理を維持)。"""
    main.check_auth(request)


def _turn_index_by_request_id(
    conversation: dict[str, Any],
    request_id: str,
) -> int:
    for index, turn in enumerate(conversation.get("turns") or []):
        if isinstance(turn, dict) and turn.get("request_id") == request_id:
            return index
    raise api_error(404, "turn_not_found", "対象ターンが見つかりません")


def _attachment_http_exception(exc: attachments.AttachmentError) -> HTTPException:
    if exc.code in {"attachment_not_found", "attachment_owner_mismatch"}:
        code = status.HTTP_404_NOT_FOUND
    elif exc.code == "attachment_expired":
        code = status.HTTP_410_GONE
    elif exc.code in {
        "attachment_too_large",
        "attachment_total_exceeded",
        "attachment_count_exceeded",
    }:
        code = 413
    elif exc.code in {
        "attachment_type_not_allowed",
        "attachment_mime_mismatch",
    }:
        code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    else:
        code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return api_error(code, exc.code, str(exc))


def _attachment_context_for_turn(
    conversation: dict[str, Any],
    turn: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    attachment_ids = turn.get("attachment_ids")
    if not isinstance(attachment_ids, list) or not attachment_ids:
        return "", []
    owner = str(
        turn.get("attachment_conversation_id") or conversation.get("id") or ""
    )
    try:
        return main.attachment_store.build_context(
            owner,
            [str(item) for item in attachment_ids],
        )
    except attachments.AttachmentError as exc:
        raise _attachment_http_exception(exc) from exc


@router.post(
    "/api/conversations",
    dependencies=[Depends(_check_auth)],
    response_model=api_schemas.Conversation,
)
def create_draft_conversation() -> dict[str, Any]:
    """添付や編集分岐の送信前draftとして、空の会話を作る。"""
    return _scrub_public_dict(main.store.create())


@router.get(
    "/api/conversations",
    dependencies=[Depends(_check_auth)],
    response_model=api_schemas.ConversationListResponse,
)
def conversations() -> dict[str, Any]:
    """会話一覧と、読み取れず一覧に載せられなかったファイルの件数を返す。

    破損ファイルを黙って読み飛ばすと、利用者は会話が消えたようにしか見えない。
    件数と対象を同じ封筒で明示する(issue #18)。
    """
    stored, corrupt = main.store.scan_all()
    summaries = sorted(
        (main.store.summary(item) for item in stored),
        key=lambda item: item["updated_at"],
        reverse=True,
    )
    return _scrub_public_dict(
        {
            "items": summaries,
            "corrupt_count": len(corrupt),
            "corrupt": corrupt,
        },
        default={"items": [], "corrupt_count": 0, "corrupt": []},
    )


@router.post(
    "/api/conversations/search",
    dependencies=[Depends(_check_auth)],
    response_model=api_schemas.ConversationSearchResponse,
)
def search_conversations(req: SearchRequest) -> dict[str, Any]:
    """検索語をURLや通常のaccess logへ残さず、保存JSONだけを検索する。"""
    return _scrub_public_dict(
        {"query": req.q, "items": main.store.search(req.q, req.limit)},
        default={"query": "", "items": []},
    )


@router.get(
    "/api/conversations/{conversation_id}",
    dependencies=[Depends(_check_auth)],
    response_model=api_schemas.Conversation,
)
def conversation(conversation_id: str) -> dict[str, Any]:
    try:
        return _scrub_public_dict(
            turn_state.public_conversation(main.store.load(conversation_id))
        )
    except ConversationNotFound as exc:
        raise api_error(404, "conversation_not_found", "会話が見つかりません") from exc


@router.get(
    "/api/conversations/{conversation_id}/attachments",
    dependencies=[Depends(_check_auth)],
    response_model=api_schemas.AttachmentListResponse,
)
def list_attachments(conversation_id: str) -> dict[str, Any]:
    canonical_id = main._require_conversation_id(conversation_id)
    try:
        main.store.load(canonical_id)
        items = main.attachment_store.list(canonical_id)
    except ConversationNotFound as exc:
        raise api_error(404, "conversation_not_found", "会話が見つかりません") from exc
    return _scrub_public_dict({"items": items}, default={"items": []})


@router.post(
    "/api/conversations/{conversation_id}/attachments",
    dependencies=[Depends(_check_auth)],
    response_model=api_schemas.Attachment,
)
async def upload_attachment(
    conversation_id: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    canonical_id = main._require_conversation_id(conversation_id)
    async with main._hold_conversation_lock(canonical_id):
        try:
            await main._blocking_call(main.store.load, canonical_id)
        except ConversationNotFound as exc:
            raise api_error(404, "conversation_not_found", "会話が見つかりません") from exc
        try:
            item = await main.attachment_store.save_upload(canonical_id, file)
        except attachments.AttachmentError as exc:
            raise _attachment_http_exception(exc) from exc
    return _scrub_public_dict(item)


@router.get(
    "/api/conversations/{conversation_id}/attachments/{attachment_id}",
    dependencies=[Depends(_check_auth)],
)
def download_attachment(conversation_id: str, attachment_id: str) -> FileResponse:
    canonical_id = main._require_conversation_id(conversation_id)
    try:
        metadata = main.attachment_store.metadata(canonical_id, attachment_id)
        path = main.attachment_store.content_path(canonical_id, attachment_id)
    except attachments.AttachmentError as exc:
        raise _attachment_http_exception(exc) from exc
    return FileResponse(
        path,
        media_type=str(metadata["mime_type"]),
        filename=str(metadata["name"]),
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete(
    "/api/conversations/{conversation_id}/attachments/{attachment_id}",
    dependencies=[Depends(_check_auth)],
    response_model=api_schemas.OkResponse,
)
async def delete_attachment(
    conversation_id: str,
    attachment_id: str,
) -> dict[str, bool]:
    canonical_id = main._require_conversation_id(conversation_id)
    async with main._hold_conversation_lock(canonical_id):
        try:
            await main._blocking_call(main.store.load, canonical_id)
        except ConversationNotFound as exc:
            raise api_error(404, "conversation_not_found", "会話が見つかりません") from exc
        try:
            await main._blocking_call(
                main.attachment_store.delete,
                canonical_id,
                attachment_id,
            )
        except attachments.AttachmentError as exc:
            raise _attachment_http_exception(exc) from exc
    return {"ok": True}


@router.get(
    "/api/conversations/{conversation_id}/export",
    dependencies=[Depends(_check_auth)],
    response_class=JSONResponse,
)
def export_conversation(
    conversation_id: str,
    format: Literal["json", "md", "zip"] = "json",
):
    """会話を JSON / Markdown / ZIP で書き出す。

    形式はpath拡張子(`/export.md`, `/export.zip`)ではなく `format` クエリで
    指定する。同じリソースの表現違いを別URLにしない。
    """
    canonical_id = main._require_conversation_id(conversation_id)
    try:
        data = _scrub_public(
            turn_state.public_conversation(main.store.load(canonical_id))
        )
    except ConversationNotFound as exc:
        raise api_error(404, "conversation_not_found", "会話が見つかりません") from exc
    if not isinstance(data, dict):
        raise api_error(500, "export_failed", "会話をエクスポートできません")
    stem = f"clage-cook-{canonical_id[:8]}"

    if format == "json":
        response = JSONResponse(data)
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{stem}.json"'
        )
        return response

    if format == "md":
        response = PlainTextResponse(
            exporting.conversation_markdown(data),
            media_type="text/markdown; charset=utf-8",
        )
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{stem}.md"'
        )
        return response

    try:
        path = exporting.build_conversation_zip(
            data_dir=config.DATA_DIR,
            conversation=data,
            attachment_store=main.attachment_store,
        )
    except Exception as exc:
        logger.error(
            "conversation export failed conversation_id=%s exception_type=%s",
            canonical_id,
            type(exc).__name__,
        )
        raise api_error(500, "export_archive_failed", "ZIPを作成できません") from exc
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"{stem}.zip",
        background=BackgroundTask(exporting.remove_export, path),
    )


@router.patch(
    "/api/conversations/{conversation_id}",
    dependencies=[Depends(_check_auth)],
    response_model=api_schemas.ConversationSummary,
)
async def rename_conversation(
    conversation_id: str, req: RenameRequest
) -> dict[str, Any]:
    canonical_id = main._require_conversation_id(conversation_id)
    async with main._hold_conversation_lock(canonical_id):
        try:
            data = await main._blocking_call(main.store.load, canonical_id)
        except ConversationNotFound as exc:
            raise api_error(404, "conversation_not_found", "会話が見つかりません") from exc
        data["title"] = req.title.strip()
        await main._blocking_call(main.store.save, data)
        return _scrub_public_dict(main.store.summary(data))


@router.patch(
    "/api/conversations/{conversation_id}/memory",
    dependencies=[Depends(_check_auth)],
    response_model=api_schemas.Conversation,
)
async def update_conversation_memory(
    conversation_id: str,
    req: ConversationMemoryUpdateRequest,
) -> dict[str, Any]:
    """会話ごとのローカルメモを楽観lock付きで更新する。"""
    canonical_id = main._require_conversation_id(conversation_id)
    async with main._hold_conversation_lock(canonical_id):
        try:
            conversation_data = await main._blocking_call(
                main.store.load, canonical_id
            )
        except ConversationNotFound as exc:
            raise api_error(404, "conversation_not_found", "会話が見つかりません") from exc
        current = conversation_data.get("memory")
        if not isinstance(current, dict):
            current = {"revision": 0, "text": "", "updated_at": ""}
        revision = current.get("revision")
        revision = revision if isinstance(revision, int) and revision >= 0 else 0
        if req.expected_revision != revision:
            raise api_error(
                status.HTTP_409_CONFLICT,
                "conversation_memory_conflict",
                "ローカルメモが別の画面で更新されています。再読込してください。",
                current_revision=revision,
            )
        text_value = req.text.strip()
        scan = policy.scan_text(text_value)
        stored_text = (
            scan["redacted_text"] if scan["action"] == "block" else text_value
        )
        conversation_data["memory"] = {
            "revision": revision + 1,
            "text": stored_text,
            "updated_at": utc_now(),
            "secret_candidates_redacted": scan["action"] == "block",
        }
        await main._blocking_call(main.store.save, conversation_data)
        return _scrub_public_dict(
            turn_state.public_conversation(conversation_data)
        )


@router.post(
    "/api/conversations/{conversation_id}/turns/{turn_request_id}/fork",
    dependencies=[Depends(_check_auth)],
    response_model=api_schemas.Conversation,
)
async def fork_conversation_at_turn(
    conversation_id: str,
    turn_request_id: str,
) -> dict[str, Any]:
    """親履歴を破壊せず、対象turnの直前から編集を続けるbranchを作る。"""
    canonical_id = main._require_conversation_id(conversation_id)
    if not _valid_request_id(turn_request_id):
        raise api_error(404, "turn_not_found", "対象ターンが見つかりません")
    async with main._hold_conversation_lock(canonical_id):
        await main._reject_destructive_change_during_run(canonical_id)
        try:
            parent = await main._blocking_call(main.store.load, canonical_id)
        except ConversationNotFound as exc:
            raise api_error(404, "conversation_not_found", "会話が見つかりません") from exc
        turn_index = _turn_index_by_request_id(parent, turn_request_id)
        target = parent["turns"][turn_index]
        if not isinstance(target, dict) or target.get("status") != "completed":
            raise api_error(
                status.HTTP_409_CONFLICT,
                "turn_not_completed",
                "完了済みターンだけを編集分岐できます",
            )
        branch = await main._blocking_call(
            main.store.create_branch,
            parent,
            before_turn_index=turn_index,
            parent_turn_request_id=turn_request_id,
        )
        return _scrub_public_dict(turn_state.public_conversation(branch))


@router.delete(
    "/api/conversations/{conversation_id}",
    dependencies=[Depends(_check_auth)],
    response_model=api_schemas.OkResponse,
)
async def delete_conversation(conversation_id: str) -> dict[str, bool]:
    canonical_id = main._require_conversation_id(conversation_id)
    async with main._hold_conversation_lock(canonical_id):
        await main._reject_destructive_change_during_run(canonical_id)
        try:
            await main._blocking_call(
                main.attachment_store.delete_conversation, canonical_id
            )
            await main._blocking_call(main.store.delete, canonical_id)
        except ConversationNotFound as exc:
            raise api_error(404, "conversation_not_found", "会話が見つかりません") from exc
        return {"ok": True}
