# -*- coding: utf-8 -*-
"""秘密を含まないruntimeモデル設定のatomic JSON store。"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from storage import utc_now


TIERS = ("low", "balanced", "high")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")


class RuntimeSettingsError(ValueError):
    pass


class RuntimeSettingsConflict(RuntimeSettingsError):
    pass


class RuntimeSettingsStore:
    """model IDと統合役だけを保存し、API keyは一切扱わない。"""

    def __init__(self, data_dir: Path, workers: tuple[str, ...]) -> None:
        self.workers = tuple(workers)
        self._control_dir = Path(data_dir).resolve() / ".control"
        self._path = self._control_dir / "runtime-settings.json"
        self._lock = threading.RLock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._read())

    def update(
        self,
        *,
        expected_revision: int,
        models: dict[str, dict[str, str | None]],
        synthesizer_provider: str | None,
        synthesizer_models: dict[str, str | None],
    ) -> dict[str, Any]:
        with self._lock:
            current = self._read()
            if expected_revision != current["revision"]:
                raise RuntimeSettingsConflict("runtime設定が別の操作で更新されています")

            next_models = deepcopy(current["models"])
            for provider, tier_values in models.items():
                if provider not in self.workers or not isinstance(tier_values, dict):
                    raise RuntimeSettingsError("provider指定が不正です")
                bucket = next_models.setdefault(provider, {})
                for tier, value in tier_values.items():
                    if tier not in TIERS:
                        raise RuntimeSettingsError("tier指定が不正です")
                    if value is None:
                        bucket.pop(tier, None)
                    else:
                        bucket[tier] = _validated_model_id(value)
                if not bucket:
                    next_models.pop(provider, None)

            next_synth = current["synthesizer_provider"]
            if synthesizer_provider is not None:
                cleaned = synthesizer_provider.strip().lower()
                if cleaned not in {"auto", *self.workers}:
                    raise RuntimeSettingsError("統合役指定が不正です")
                next_synth = cleaned

            next_synth_models = deepcopy(current["synthesizer_models"])
            for tier, value in synthesizer_models.items():
                if tier not in TIERS:
                    raise RuntimeSettingsError("統合modelのtier指定が不正です")
                if value is None:
                    next_synth_models.pop(tier, None)
                else:
                    next_synth_models[tier] = _validated_model_id(value)

            updated = {
                "schema_version": 1,
                "revision": current["revision"] + 1,
                "updated_at": utc_now(),
                "models": next_models,
                "synthesizer_provider": next_synth,
                "synthesizer_models": next_synth_models,
            }
            self._write(updated)
            return deepcopy(updated)

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return _empty_snapshot()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeSettingsError("runtime設定を安全に読み取れません") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise RuntimeSettingsError("runtime設定の形式が不正です")
        revision = raw.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise RuntimeSettingsError("runtime設定のrevisionが不正です")
        models = raw.get("models")
        synth = raw.get("synthesizer_provider")
        synth_models = raw.get("synthesizer_models")
        if not isinstance(models, dict) or not isinstance(synth_models, dict):
            raise RuntimeSettingsError("runtime設定のmodel定義が不正です")
        if synth not in {"auto", *self.workers}:
            raise RuntimeSettingsError("runtime設定の統合役が不正です")
        # 保存済みJSONも毎回検証し、手編集による危険な値を採用しない。
        for provider, tiers in models.items():
            if provider not in self.workers or not isinstance(tiers, dict):
                raise RuntimeSettingsError("runtime設定のproviderが不正です")
            for tier, value in tiers.items():
                if tier not in TIERS or not isinstance(value, str):
                    raise RuntimeSettingsError("runtime設定のtierが不正です")
                _validated_model_id(value)
        for tier, value in synth_models.items():
            if tier not in TIERS or not isinstance(value, str):
                raise RuntimeSettingsError("runtime統合modelが不正です")
            _validated_model_id(value)
        return {
            "schema_version": 1,
            "revision": revision,
            "updated_at": raw.get("updated_at"),
            "models": models,
            "synthesizer_provider": synth,
            "synthesizer_models": synth_models,
        }

    def _write(self, data: dict[str, Any]) -> None:
        self._control_dir.mkdir(parents=True, exist_ok=True)
        temp = self._control_dir / f"runtime-settings-{uuid.uuid4().hex}.tmp"
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self._path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


def _empty_snapshot() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "revision": 0,
        "updated_at": None,
        "models": {},
        "synthesizer_provider": "auto",
        "synthesizer_models": {},
    }


def _validated_model_id(value: str) -> str:
    cleaned = value.strip()
    if not _MODEL_ID.fullmatch(cleaned):
        raise RuntimeSettingsError("model IDの形式が不正です")
    return cleaned
