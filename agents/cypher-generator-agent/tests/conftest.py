from __future__ import annotations

import pytest

from services.cypher_generator_agent.app.infrastructure.config import get_settings


@pytest.fixture(autouse=True)
def isolate_settings_env_file(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_DISABLE_ENV_FILE", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
