# -*- coding: utf-8 -*-
"""会話を1ファイル1JSONで安全に保存する小さなストア。"""

from __future__ import annotations

import json
import os
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _sync_sanitized_value(current: Any, sanitized: Any) -> Any:
    """既存container参照を保ちながら、scrub済みsnapshotへ同期する。"""
    if isinstance(current, dict) and isinstance(sanitized, dict):
        for key in list(current):
            if key not in sanitized:
                del current[key]
        for key, value in sanitized.items():
            if key in current:
                current[key] = _sync_sanitized_value(current[key], value)
            else:
                current[key] = deepcopy(value)
        return current
    if isinstance(current, list) and isinstance(sanitized, list):
        shared = min(len(current), len(sanitized))
        for index in range(shared):
            current[index] = _sync_sanitized_value(
                current[index],
                sanitized[index],
            )
        del current[len(sanitized) :]
        current.extend(deepcopy(sanitized[shared:]))
        return current
    return deepcopy(sanitized)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ConversationNotFound(KeyError):
    pass


class ConversationStore:
    def __init__(
        self,
        data_dir: Path,
        *,
        sanitizer: Callable[[Any], Any] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._sanitizer = sanitizer

    @staticmethod
    def _validate_id(conversation_id: str) -> str:
        try:
            return str(uuid.UUID(conversation_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ConversationNotFound(conversation_id) from exc

    def _path(self, conversation_id: str) -> Path:
        return self.data_dir / f"{self._validate_id(conversation_id)}.json"

    def create(self, first_message: str = "") -> dict[str, Any]:
        now = utc_now()
        conversation = {
            "schema_version": 1,
            "id": str(uuid.uuid4()),
            "title": self._title(first_message),
            "created_at": now,
            "updated_at": now,
            "memory": {"revision": 0, "text": "", "updated_at": now},
            "turns": [],
        }
        self.save(conversation)
        return deepcopy(conversation)

    def create_branch(
        self,
        parent: dict[str, Any],
        *,
        before_turn_index: int,
        parent_turn_request_id: str,
    ) -> dict[str, Any]:
        """親を変更せず、指定turnより前のsnapshotを持つ新しい会話を作る。"""
        turns = parent.get("turns")
        if not isinstance(turns, list) or not 0 <= before_turn_index <= len(turns):
            raise ValueError("branch turn index is invalid")
        now = utc_now()
        parent_title = str(parent.get("title") or "新しい会話").strip()
        title = f"{parent_title} · 分岐"[:120]
        conversation = {
            "schema_version": 1,
            "id": str(uuid.uuid4()),
            "title": title,
            "created_at": now,
            "updated_at": now,
            "memory": deepcopy(
                parent.get("memory")
                if isinstance(parent.get("memory"), dict)
                else {"revision": 0, "text": "", "updated_at": now}
            ),
            "turns": deepcopy(turns[:before_turn_index]),
            "branch": {
                "parent_conversation_id": str(parent.get("id") or ""),
                "parent_turn_request_id": parent_turn_request_id,
                "forked_at": now,
                "copied_turn_count": before_turn_index,
            },
        }
        self.save(conversation)
        return deepcopy(conversation)

    def load(self, conversation_id: str) -> dict[str, Any]:
        path = self._path(conversation_id)
        with self._lock:
            if not path.is_file():
                raise ConversationNotFound(conversation_id)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ConversationNotFound(conversation_id) from exc
        if not isinstance(data, dict) or data.get("id") != self._validate_id(conversation_id):
            raise ConversationNotFound(conversation_id)
        if not isinstance(data.get("turns"), list):
            data["turns"] = []
        if not isinstance(data.get("memory"), dict):
            data["memory"] = {"revision": 0, "text": "", "updated_at": ""}
        return data

    def save(self, conversation: dict[str, Any]) -> None:
        conversation_id = self._validate_id(str(conversation.get("id", "")))
        path = self._path(conversation_id)
        temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        with self._lock:
            try:
                snapshot = deepcopy(conversation)
                updated_at = utc_now()
                snapshot["updated_at"] = updated_at
                if self._sanitizer is not None:
                    sanitized = self._sanitizer(snapshot)
                    if not isinstance(sanitized, dict):
                        raise TypeError("conversation sanitizer must return a dict")
                    snapshot = deepcopy(sanitized)
                payload = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
                with temp.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, path)
                if self._sanitizer is None:
                    conversation["updated_at"] = updated_at
                else:
                    _sync_sanitized_value(conversation, snapshot)
            finally:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass

    def delete(self, conversation_id: str) -> None:
        path = self._path(conversation_id)
        with self._lock:
            if not path.is_file():
                raise ConversationNotFound(conversation_id)
            path.unlink()

    def list(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        with self._lock:
            paths = list(self.data_dir.glob("*.json"))
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("turns"), list):
                    continue
                summaries.append(self.summary(data))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(summaries, key=lambda item: item["updated_at"], reverse=True)

    def search(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        terms = [part.casefold() for part in query.split() if part]
        if not terms:
            return []
        results: list[dict[str, Any]] = []
        for summary in self.list():
            try:
                conversation = self.load(summary["id"])
            except ConversationNotFound:
                continue
            chunks = [str(conversation.get("title", ""))]
            memory = conversation.get("memory")
            if isinstance(memory, dict):
                chunks.append(str(memory.get("text", "")))
            for turn in conversation.get("turns", []):
                chunks.append(str(turn.get("message", "")))
                for answer in (turn.get("answers") or {}).values():
                    if isinstance(answer, dict):
                        chunks.append(str(answer.get("text", "")))
                synthesis = turn.get("synthesis") or {}
                if isinstance(synthesis, dict):
                    chunks.append(str(synthesis.get("text", "")))
            haystack = "\n".join(chunks).casefold()
            if all(term in haystack for term in terms):
                results.append(summary)
                if len(results) >= max(1, min(limit, 100)):
                    break
        return results

    @staticmethod
    def summary(conversation: dict[str, Any]) -> dict[str, Any]:
        turns = conversation.get("turns") or []
        preview = ""
        if turns:
            synthesis = turns[-1].get("synthesis") or {}
            preview = str(synthesis.get("text") or turns[-1].get("message") or "")
        preview = " ".join(preview.split())[:160]
        return {
            "id": conversation["id"],
            "title": conversation.get("title") or "新しい会話",
            "created_at": conversation.get("created_at", ""),
            "updated_at": conversation.get("updated_at", ""),
            "turn_count": len(turns),
            "preview": preview,
        }

    @staticmethod
    def find_turn_by_request_id(
        conversation: dict[str, Any], request_id: str
    ) -> dict[str, Any] | None:
        for turn in conversation.get("turns") or []:
            if turn.get("request_id") == request_id:
                return turn
        return None

    def find_conversation_by_request_id(
        self, request_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """再起動後もrequest_idを既存の保存済みターンへ結び付ける。"""
        matches: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        with self._lock:
            for path in self.data_dir.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict) or not isinstance(data.get("turns"), list):
                        continue
                    canonical_id = self._validate_id(str(data.get("id", "")))
                    if path.stem != canonical_id:
                        continue
                    turn = self.find_turn_by_request_id(data, request_id)
                    if turn is not None:
                        matches.append((str(data.get("updated_at", "")), data, turn))
                except (
                    OSError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    ConversationNotFound,
                ):
                    continue
        if not matches:
            return None
        _, conversation, turn = max(matches, key=lambda item: item[0])
        return conversation, turn

    def recover_interrupted_turns(self) -> int:
        """前プロセスが残したrunningターンを、再実行せず中断として確定する。"""
        recovered = 0
        with self._lock:
            paths = list(self.data_dir.glob("*.json"))
            for path in paths:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict) or not isinstance(data.get("turns"), list):
                        continue
                    canonical_id = self._validate_id(str(data.get("id", "")))
                    if path.stem != canonical_id:
                        continue
                    file_recovered = 0
                    for turn in data["turns"]:
                        if not isinstance(turn, dict):
                            continue
                        attempts = turn.get("attempts")
                        if isinstance(attempts, list):
                            for attempt in attempts:
                                if (
                                    not isinstance(attempt, dict)
                                    or attempt.get("status")
                                    not in {"running", "dispatching"}
                                ):
                                    continue
                                attempt["status"] = "interrupted"
                                attempt["interrupted"] = True
                                attempt["usage_may_be_incomplete"] = True
                                attempt["updated_at"] = utc_now()
                                file_recovered += 1
                        if turn.get("status") != "running":
                            continue
                        turn["status"] = "interrupted"
                        turn["interrupted"] = True
                        turn["cancelled"] = False
                        turn["failed"] = True
                        turn["usage_may_be_incomplete"] = True
                        synthesis = turn.get("synthesis")
                        if not isinstance(synthesis, dict) or synthesis.get("pending") is True:
                            turn["synthesis"] = {
                                "ok": False,
                                "error": "前回の会議はサーバー停止により完了しませんでした",
                                "source": "none",
                                "usage": {},
                                "skipped": False,
                                "interrupted": True,
                            }
                        file_recovered += 1
                    if file_recovered:
                        self.save(data)
                        recovered += file_recovered
                except (
                    OSError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    ConversationNotFound,
                ):
                    continue
        return recovered

    @staticmethod
    def _title(message: str) -> str:
        clean = " ".join(message.split()).strip()
        return clean[:60] or "新しい会話"
