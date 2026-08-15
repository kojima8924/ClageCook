# -*- coding: utf-8 -*-
"""APIリクエストのPydanticモデルとrequest ID検証。

main.pyから分離した入力検証専用モジュール。config以外のサーバー内部状態
(store・budget_guard等)には依存しない。
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

import config


_REQUEST_ID_ALLOWED = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
)


def _valid_request_id(value: str) -> bool:
    return 8 <= len(value) <= 80 and all(char in _REQUEST_ID_ALLOWED for char in value)


class PlanRequest(BaseModel):
    message: str = Field(min_length=1)
    tier: Literal["low", "balanced", "high"] = "balanced"
    reasoning_mode: Literal["auto", "low", "medium", "high"] = "auto"
    debate: bool = False
    providers: list[Literal["claude", "gemini", "chatgpt", "grok"]] | None = None
    synthesize: bool = True
    blind: bool = False
    web_search: bool = False
    conversation_id: str | None = None
    attachment_ids: list[str] = Field(
        default_factory=list,
        max_length=config.ATTACHMENT_MAX_PER_TURN,
    )

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("質問本文が空です")
        if len(value) > config.MAX_MESSAGE_CHARS:
            raise ValueError(f"質問は{config.MAX_MESSAGE_CHARS}文字以下にしてください")
        return value

    @field_validator("attachment_ids")
    @classmethod
    def validate_attachment_ids(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for item in value:
            try:
                canonical = str(uuid.UUID(item))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("attachment IDが不正です") from exc
            if canonical not in result:
                result.append(canonical)
        return result


class ChatRequest(PlanRequest):
    request_id: str | None = Field(default=None, min_length=8, max_length=80)
    confirm_live_api: bool = False
    confirm_sensitive_data: bool = False

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _valid_request_id(value):
            raise ValueError("request_idの長さまたは文字が不正です")
        return value


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ConversationMemoryUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    text: str = Field(default="", max_length=config.CONVERSATION_MEMORY_MAX_CHARS)


class RuntimeSettingsUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    models: dict[str, dict[str, str | None]] = Field(default_factory=dict)
    synthesizer_provider: Literal[
        "auto", "claude", "gemini", "chatgpt", "grok"
    ] | None = None
    synthesizer_models: dict[str, str | None] = Field(default_factory=dict)


class ReconciliationReleaseRequest(BaseModel):
    confirmed_no_unobserved_charge: bool = False
    note: str = Field(default="", max_length=200)


class PolicyScanRequest(BaseModel):
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if len(value) > config.MAX_MESSAGE_CHARS:
            raise ValueError(f"本文は{config.MAX_MESSAGE_CHARS}文字以下にしてください")
        return value


class SearchRequest(BaseModel):
    q: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=30, ge=1, le=100)

    @field_validator("q")
    @classmethod
    def validate_query(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("検索語が空です")
        return cleaned


class RegenerationPlanRequest(BaseModel):
    target: Literal["answer", "synthesis"]
    provider: str | None = None

    @field_validator("provider")
    @classmethod
    def valid_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if cleaned not in config.WORKERS:
            raise ValueError("invalid provider")
        return cleaned


class RegenerationRequest(RegenerationPlanRequest):
    regeneration_id: str = Field(min_length=8, max_length=80)
    confirm_live_api: bool = False
    confirm_sensitive_data: bool = False

    @field_validator("regeneration_id")
    @classmethod
    def valid_regeneration_id(cls, value: str) -> str:
        if not _valid_request_id(value):
            raise ValueError("invalid regeneration id")
        return value
