# -*- coding: utf-8 -*-

import json

import pytest
from fastapi.testclient import TestClient

import config
import main
import runtime_settings
from storage import ConversationStore


def test_runtime_store_updates_atomically_and_rejects_stale_revision(tmp_path):
    store = runtime_settings.RuntimeSettingsStore(tmp_path, config.WORKERS)
    updated = store.update(
        expected_revision=0,
        models={"claude": {"balanced": "claude-runtime-test"}},
        synthesizer_provider="chatgpt",
        synthesizer_models={"high": "gpt-runtime-high"},
    )
    assert updated["revision"] == 1
    assert updated["models"]["claude"]["balanced"] == "claude-runtime-test"
    assert store.snapshot()["synthesizer_provider"] == "chatgpt"
    with pytest.raises(runtime_settings.RuntimeSettingsConflict):
        store.update(
            expected_revision=0,
            models={},
            synthesizer_provider=None,
            synthesizer_models={},
        )


@pytest.mark.parametrize(
    "bad_model",
    ["", "contains space", "../escape", "model?key=value", "x" * 161],
)
def test_runtime_store_rejects_unsafe_model_ids(tmp_path, bad_model):
    store = runtime_settings.RuntimeSettingsStore(tmp_path, config.WORKERS)
    with pytest.raises(runtime_settings.RuntimeSettingsError):
        store.update(
            expected_revision=0,
            models={"claude": {"balanced": bad_model}},
            synthesizer_provider=None,
            synthesizer_models={},
        )


def test_runtime_store_rejects_tampered_json(tmp_path):
    control = tmp_path / ".control"
    control.mkdir()
    (control / "runtime-settings.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "revision": 1,
                "models": {"claude": {"balanced": "bad model"}},
                "synthesizer_provider": "auto",
                "synthesizer_models": {},
            }
        ),
        encoding="utf-8",
    )
    store = runtime_settings.RuntimeSettingsStore(tmp_path, config.WORKERS)
    with pytest.raises(runtime_settings.RuntimeSettingsError):
        store.snapshot()


def test_runtime_settings_api_updates_effective_models(tmp_path, monkeypatch):
    runtime = runtime_settings.RuntimeSettingsStore(tmp_path / "runtime", config.WORKERS)
    monkeypatch.setattr(config, "runtime_settings", runtime)
    monkeypatch.setattr(main, "store", ConversationStore(tmp_path / "conversations"))
    monkeypatch.setattr(config, "AUTH_TOKEN", "")
    response = TestClient(main.app).patch(
        "/api/settings/runtime",
        json={
            "expected_revision": 0,
            "models": {"claude": {"balanced": "claude-api-test"}},
            "synthesizer_provider": "auto",
            "synthesizer_models": {},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["runtime_settings"]["revision"] == 1
    claude = next(item for item in body["providers"] if item["name"] == "claude")
    assert claude["models"]["balanced"] == "claude-api-test"

    stale = TestClient(main.app).patch(
        "/api/settings/runtime",
        json={
            "expected_revision": 0,
            "models": {},
            "synthesizer_models": {},
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "runtime_settings_conflict"
