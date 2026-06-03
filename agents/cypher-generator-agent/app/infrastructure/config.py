from functools import lru_cache
import os
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEFAULT_ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "semantic_model" / "artifacts"


class Settings(BaseSettings):
    __test__ = False
    app_name: str = "cypher-generator-agent"
    host: str = "0.0.0.0"
    port: int = 8000
    testing_agent_url: str = "http://127.0.0.1:8003"
    request_timeout_seconds: float = 120.0
    graph_model_path: Path = _DEFAULT_ARTIFACT_DIR / "tugraph_network_semantic_model.yaml"
    value_index_path: Path = _DEFAULT_ARTIFACT_DIR / "tugraph_value_index.json"
    llm_enabled: bool = Field(
        default=False,
        validation_alias="CYPHER_GENERATOR_AGENT_LLM_ENABLED",
    )
    llm_provider: str = Field(
        default="mock",
        validation_alias="CYPHER_GENERATOR_AGENT_LLM_PROVIDER",
    )
    llm_base_url: str | None = Field(
        default=None,
        validation_alias="CYPHER_GENERATOR_AGENT_LLM_BASE_URL",
    )
    llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="CYPHER_GENERATOR_AGENT_LLM_API_KEY",
    )
    llm_model: str = Field(
        default="qwen3-32b",
        validation_alias="CYPHER_GENERATOR_AGENT_LLM_MODEL",
    )
    llm_temperature: float = Field(
        default=0.1,
        validation_alias="CYPHER_GENERATOR_AGENT_LLM_TEMPERATURE",
    )
    llm_timeout_seconds: float = Field(
        default=120.0,
        validation_alias="CYPHER_GENERATOR_AGENT_LLM_TIMEOUT_SECONDS",
    )
    llm_max_schema_retries: int = Field(
        default=2,
        validation_alias="CYPHER_GENERATOR_AGENT_LLM_MAX_SCHEMA_RETRIES",
    )

    model_config = SettingsConfigDict(
        env_prefix="CYPHER_GENERATOR_AGENT_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    if os.getenv("CYPHER_GENERATOR_AGENT_DISABLE_ENV_FILE", "").casefold() in {"1", "true", "yes"}:
        return Settings(_env_file=None)
    return Settings()
