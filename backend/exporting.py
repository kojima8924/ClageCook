# -*- coding: utf-8 -*-
"""秘密を公開shapeへ落とした会話のMarkdown/ZIPエクスポート。"""

from __future__ import annotations

import json
import os
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any


_SAFE_ARCHIVE = re.compile(r"[^A-Za-z0-9._-]+")


def conversation_markdown(conversation: dict[str, Any]) -> str:
    title = str(conversation.get("title") or "Clage Cook conversation")
    lines = [f"# {title}", ""]
    lines.append(f"- Conversation ID: `{conversation.get('id', '')}`")
    lines.append(f"- Created: {conversation.get('created_at', '')}")
    lines.append(f"- Updated: {conversation.get('updated_at', '')}")
    memory = conversation.get("memory")
    memory_text = (
        str(memory.get("text") or "").strip() if isinstance(memory, dict) else ""
    )
    if memory_text:
        lines.extend(["", "## ローカルメモ", "", memory_text])

    for index, turn in enumerate(conversation.get("turns") or [], start=1):
        if not isinstance(turn, dict):
            continue
        lines.extend(
            [
                "",
                f"## Turn {index}",
                "",
                "### User",
                "",
                str(turn.get("message") or turn.get("clean_message") or ""),
            ]
        )
        attachments = turn.get("attachments")
        if isinstance(attachments, list) and attachments:
            lines.extend(["", "Attachments:"])
            for item in attachments:
                if isinstance(item, dict):
                    lines.append(
                        f"- {item.get('name', 'attachment')} "
                        f"(`{item.get('id', '')}`, {item.get('mime_type', '')})"
                    )
        answers = turn.get("answers")
        if isinstance(answers, dict):
            for provider in ("claude", "gemini", "chatgpt", "grok"):
                answer = answers.get(provider)
                if not isinstance(answer, dict):
                    continue
                lines.extend(["", f"### {provider}", ""])
                lines.append(
                    str(answer.get("text") or answer.get("error") or "（回答なし）")
                )
                citations = answer.get("citations")
                if isinstance(citations, list) and citations:
                    lines.extend(["", "Sources:"])
                    for citation in citations:
                        if not isinstance(citation, dict):
                            continue
                        url = str(citation.get("url") or "")
                        label = str(citation.get("title") or url)
                        if url:
                            lines.append(f"- [{label}]({url})")
        synthesis = turn.get("synthesis")
        if isinstance(synthesis, dict) and synthesis.get("skipped") is not True:
            lines.extend(["", "### Synthesis", ""])
            lines.append(
                str(synthesis.get("text") or synthesis.get("error") or "（統合なし）")
            )
    return "\n".join(lines).rstrip() + "\n"


def build_conversation_zip(
    *,
    data_dir: Path,
    conversation: dict[str, Any],
    attachment_store: Any,
) -> Path:
    export_dir = Path(data_dir).resolve() / ".exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"{uuid.uuid4()}.zip"
    conversation_id = str(conversation.get("id") or "")
    attachment_manifest: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(
            path,
            "x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            archive.writestr(
                "conversation.json",
                json.dumps(conversation, ensure_ascii=False, indent=2) + "\n",
            )
            archive.writestr("conversation.md", conversation_markdown(conversation))
            for metadata in attachment_store.list(
                conversation_id,
                purge_expired=True,
            ):
                attachment_id = str(metadata.get("id") or "")
                entry = dict(metadata)
                try:
                    content = attachment_store.content_path(
                        conversation_id,
                        attachment_id,
                    )
                    safe_name = _safe_archive_name(str(metadata.get("name") or "file"))
                    archive_name = f"attachments/{attachment_id}-{safe_name}"
                    archive.write(content, archive_name)
                    entry["archive_path"] = archive_name
                    entry["included"] = True
                except Exception:
                    entry["included"] = False
                    entry["reason"] = "unavailable"
                attachment_manifest.append(entry)
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "schema_version": 1,
                        "conversation_id": conversation_id,
                        "attachments": attachment_manifest,
                        "note": (
                            "Attachment bytes are user-provided originals and are not "
                            "secret-scrubbed during authenticated export."
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def remove_export(path: Path) -> None:
    try:
        resolved = path.resolve()
        if resolved.parent.name != ".exports" or resolved.suffix != ".zip":
            return
        os.remove(resolved)
    except FileNotFoundError:
        pass


def _safe_archive_name(value: str) -> str:
    leaf = Path(value.replace("\\", "/")).name.strip().strip(".")
    cleaned = _SAFE_ARCHIVE.sub("_", leaf)[:100]
    return cleaned or "attachment"
