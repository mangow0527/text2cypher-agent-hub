from __future__ import annotations

import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _first_nonempty(*values: str | None) -> str | None:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return None


class Settings(BaseSettings):
    app_name: str = "runtime-results-service"
    host: str = "0.0.0.0"
    port: int = 8001
    cypher_generator_agent_data_dir: str = "data/cypher_generator_agent"
    testing_data_dir: str = "data/testing_service"
    user_query_data_dir: str = "data/runtime_console/user_queries"
    poll_interval_seconds: int = 5
    cypher_generator_agent_base_url: str = "http://127.0.0.1:8000"
    testing_service_base_url: str = "http://127.0.0.1:8003"
    qa_generator_base_url: str = "http://127.0.0.1:8020"
    cga_trace_profile: str = "all"
    diagnostic_llm_base_url: str | None = None
    diagnostic_llm_api_key: str | None = None
    diagnostic_llm_model: str | None = None
    diagnostic_llm_temperature: float = 0.1
    diagnostic_llm_timeout_seconds: float = 120.0
    testing_service_llm_base_url: str | None = Field(default=None, validation_alias="TESTING_SERVICE_LLM_BASE_URL")
    testing_service_llm_api_key: str | None = Field(default=None, validation_alias="TESTING_SERVICE_LLM_API_KEY")
    testing_service_llm_model: str | None = Field(default=None, validation_alias="TESTING_SERVICE_LLM_MODEL")

    @property
    def resolved_diagnostic_llm_base_url(self) -> str | None:
        return _first_nonempty(
            self.diagnostic_llm_base_url,
            self.testing_service_llm_base_url,
            os.getenv("TESTING_SERVICE_LLM_BASE_URL"),
        )

    @property
    def resolved_diagnostic_llm_api_key(self) -> str | None:
        return _first_nonempty(
            self.diagnostic_llm_api_key,
            self.testing_service_llm_api_key,
            os.getenv("TESTING_SERVICE_LLM_API_KEY"),
        )

    @property
    def resolved_diagnostic_llm_model(self) -> str | None:
        return _first_nonempty(
            self.diagnostic_llm_model,
            self.testing_service_llm_model,
            os.getenv("TESTING_SERVICE_LLM_MODEL"),
        )

    model_config = SettingsConfigDict(
        env_prefix="RUNTIME_RESULTS_SERVICE_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
