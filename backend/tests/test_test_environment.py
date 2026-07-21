from __future__ import annotations

import os

import config


def test_suite_starts_with_paid_and_admin_integrations_disabled():
    assert config.LIVE_API_ENABLED is False
    assert config.ADMIN_TELEMETRY_ENABLED is False
    assert config.INCLUDE_MOCKS_WHEN_MIXED is False
    assert config.AUTH_TOKEN == ""
    for name in (
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
    ):
        assert os.getenv(name) == ""
