from __future__ import annotations

from services.cypher_generator_agent.app.infrastructure.config import Settings


def test_settings_accept_cypher_generator_agent_llm_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_API_KEY", "test-key")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_MODEL", "qwen3-32b")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_TEMPERATURE", "0.1")

    settings = Settings(_env_file=None)

    assert settings.llm_enabled is True
    assert settings.llm_provider == "openai_compatible"
    assert settings.llm_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "test-key"
    assert settings.llm_model == "qwen3-32b"
    assert settings.llm_temperature == 0.1


def test_llm_settings_default_to_mock_disabled() -> None:
    settings = Settings(_env_file=None)

    assert settings.llm_enabled is False
    assert settings.llm_provider == "mock"
    assert settings.llm_model == "qwen3-32b"


def test_settings_default_to_packaged_tugraph_semantic_artifacts() -> None:
    settings = Settings(_env_file=None)

    assert settings.graph_model_path.name == "tugraph_network_semantic_model.yaml"
    assert settings.graph_model_path.parent.name == "artifacts"
    assert settings.value_index_path.name == "tugraph_value_index.json"
    assert settings.graph_model_path.exists()
    assert settings.value_index_path.exists()
