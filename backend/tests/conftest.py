"""Test-suite safety defaults applied before application modules are imported.

Local ``backend/.env`` files may intentionally enable paid APIs for manual
development. Tests must never inherit that authority implicitly.
"""

from __future__ import annotations

import os


_SAFE_TEST_ENV = {
    "CLAGE_LIVE_API_ENABLED": "false",
    "CLAGE_ADMIN_TELEMETRY_ENABLED": "false",
    "INCLUDE_MOCK_PROVIDERS": "false",
    "CLAGE_AUTH_TOKEN": "",
    "ANTHROPIC_API_KEY": "",
    "GEMINI_API_KEY": "",
    "OPENAI_API_KEY": "",
    "XAI_API_KEY": "",
}

os.environ.update(_SAFE_TEST_ENV)
