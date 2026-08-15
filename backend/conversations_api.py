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
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.background import BackgroundTask

import attachments
import config
import exporting
import policy
import main
from api_models import (
    ConversationMemoryUpdateRequest,
    RenameRequest,
    SearchRequest,
    _valid_request_id,
)
from sanitizing import _scrub_public, _scrub_public_dict
from storage import ConversationNotFound, utc_now


logger = logging.getLogger("clage_cook")

router = APIRouter()


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
    raise HTTPException(status_code=404, detail="対象ターンが見つかりません")


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
    return HTTPException(
        status_code=code,
        detail={"code": exc.code, "message": str(exc)},
    )


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


@router.post("/api/conversations", dependencies=[Depends(_check_auth)])
def create_draft_conversation() -> dict[str, Any]:
    """添付や編集分岐の送信前draftとして、空の会話を作る。"""
    return _scrub_public_dict(main.store.create())


@router.get("/api/conversations", dependencies=[Depends(_check_auth)])
def conversations() -> list[dict[str, Any]]:
    public = _scrub_public(main.store.list())
    return public if isinstance(public, list) else []


@router.post("/api/search", dependencies=[Depends(_check_auth)])
def search_conversations(req: SearchRequest) -> dict[str, Any]:
    """検索語をURLや通常のaccess logへ残さず、保存JSONだけを検索する。"""
    return _scrub_public_dict(
        {"query": req.q, "results": main.store.search(req.q, req.limit)},
        default={"query": "", "results": []},
    )


@router.get(
    "/api/conversations/{conversation_id}", dependencies=[Depends(_check_auth)]
)
def conversation(conversation_id: str) -> dict[str, Any]:
    try:
        return _scrub_public_dict(main.store.load(conversation_id))
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会話が見つかりません") from exc


@router.get(
    "/api/conversations/{conversation_id}/attachments",
    dependencies=[Depends(_check_auth)],
)
def list_attachments(conversation_id: str) -> dict[str, Any]:
    canonical_id = main._canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    try:
        main.store.load(canonical_id)
        items = main.attachment_store.list(canonical_id)
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
    return _scrub_public_dict({"items": items}, default={"items": []})


@router.post(
    "/api/conversations/{conversation_id}/attachments",
    dependencies=[Depends(_check_auth)],
)
async def upload_attachment(
    conversation_id: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    canonical_id = main._canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    async with main._hold_conversation_lock(canonical_id):
        try:
            await main._blocking_call(main.store.load, canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
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
    canonical_id = main._canonical_conversation_id(conversation_id)
    assert canonical_id is not None
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
)
async def delete_attachment(
    conversation_id: str,
    attachment_id: str,
) -> dict[str, bool]:
    canonical_id = main._canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    async with main._hold_conversation_lock(canonical_id):
        try:
            await main._blocking_call(main.store.load, canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
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
)
def export_conversation(conversation_id: str) -> JSONResponse:
    try:
        data = _scrub_public(main.store.load(conversation_id))
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
    response = JSONResponse(data)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="clage-cook-{conversation_id[:8]}.json"'
    )
    return response


@router.get(
    "/api/conversations/{conversation_id}/export.md",
    dependencies=[Depends(_check_auth)],
)
def export_conversation_markdown(conversation_id: str) -> PlainTextResponse:
    try:
        data = _scrub_public(main.store.load(conversation_id))
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="会話をエクスポートできません")
    response = PlainTextResponse(
        exporting.conversation_markdown(data),
        media_type="text/markdown; charset=utf-8",
    )
    response.headers["Content-Disposition"] = (
        f'attachment; filename="clage-cook-{conversation_id[:8]}.md"'
    )
    return response


@router.get(
    "/api/conversations/{conversation_id}/export.zip",
    dependencies=[Depends(_check_auth)],
)
def export_conversation_archive(conversation_id: str) -> FileResponse:
    canonical_id = main._canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    try:
        data = _scrub_public(main.store.load(canonical_id))
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="会話をエクスポートできません")
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
        raise HTTPException(status_code=500, detail="ZIPを作成できません") from exc
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"clage-cook-{canonical_id[:8]}.zip",
        background=BackgroundTask(exporting.remove_export, path),
    )


@router.patch(
    "/api/conversations/{conversation_id}", dependencies=[Depends(_check_auth)]
)
async def rename_conversation(
    conversation_id: str, req: RenameRequest
) -> dict[str, Any]:
    canonical_id = main._canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    async with main._hold_conversation_lock(canonical_id):
        try:
            data = await main._blocking_call(main.store.load, canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        data["title"] = req.title.strip()
        await main._blocking_call(main.store.save, data)
        return _scrub_public_dict(main.store.summary(data))


@router.patch(
    "/api/conversations/{conversation_id}/memory",
    dependencies=[Depends(_check_auth)],
)
async def update_conversation_memory(
    conversation_id: str,
    req: ConversationMemoryUpdateRequest,
) -> dict[str, Any]:
    """会話ごとのローカルメモを楽観lock付きで更新する。"""
    canonical_id = main._canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    async with main._hold_conversation_lock(canonical_id):
        try:
            conversation_data = await main._blocking_call(
                main.store.load, canonical_id
            )
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        current = conversation_data.get("memory")
        if not isinstance(current, dict):
            current = {"revision": 0, "text": "", "updated_at": ""}
        revision = current.get("revision")
        revision = revision if isinstance(revision, int) and revision >= 0 else 0
        if req.expected_revision != revision:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "conversation_memory_conflict",
                    "message": "ローカルメモが別の画面で更新されています。再読込してください。",
                    "current_revision": revision,
                },
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
        return _scrub_public_dict(conversation_data)


@router.post(
    "/api/conversations/{conversation_id}/turns/{turn_request_id}/fork",
    dependencies=[Depends(_check_auth)],
)
async def fork_conversation_at_turn(
    conversation_id: str,
    turn_request_id: str,
) -> dict[str, Any]:
    """親履歴を破壊せず、対象turnの直前から編集を続けるbranchを作る。"""
    canonical_id = main._canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    if not _valid_request_id(turn_request_id):
        raise HTTPException(status_code=404, detail="対象ターンが見つかりません")
    async with main._hold_conversation_lock(canonical_id):
        await main._reject_destructive_change_during_run(canonical_id)
        try:
            parent = await main._blocking_call(main.store.load, canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        turn_index = _turn_index_by_request_id(parent, turn_request_id)
        target = parent["turns"][turn_index]
        if not isinstance(target, dict) or target.get("status") != "completed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="完了済みターンだけを編集分岐できます",
            )
        branch = await main._blocking_call(
            main.store.create_branch,
            parent,
            before_turn_index=turn_index,
            parent_turn_request_id=turn_request_id,
        )
        return _scrub_public_dict(branch)


@router.delete(
    "/api/conversations/{conversation_id}", dependencies=[Depends(_check_auth)]
)
async def delete_conversation(conversation_id: str) -> dict[str, bool]:
    canonical_id = main._canonical_conversation_id(conversation_id)
    assert canonical_id is not None
    async with main._hold_conversation_lock(canonical_id):
        await main._reject_destructive_change_during_run(canonical_id)
        try:
            await main._blocking_call(
                main.attachment_store.delete_conversation, canonical_id
            )
            await main._blocking_call(main.store.delete, canonical_id)
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="会話が見つかりません") from exc
        return {"ok": True}
