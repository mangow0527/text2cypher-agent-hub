from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .llm_config import enforce_required_llm_config


class Settings(BaseSettings):
    __test__ = False
    app_name: str = "repair-service"
    host: str = "0.0.0.0"
    port: int = 8002
    data_dir: str = "data/repair_service"
    cgs_base_url: str = "http://127.0.0.1:8000"
    knowledge_ops_repairs_apply_url: str = "http://127.0.0.1:8010/api/knowledge/repairs/apply"
    knowledge_ops_repairs_apply_capture_dir: Optional[str] = None
    cypher_generator_agent_url: str = "http://127.0.0.1:8000"
    knowledge_ops_feedback_url: Optional[str] = None
    qa_generation_feedback_url: Optional[str] = None
    request_timeout_seconds: float = 120.0

    tugraph_url: str = "http://118.196.92.128:7070"
    tugraph_username: str = "admin"
    tugraph_password: str = "admin"
    tugraph_graph: str = "default"
    mock_tugraph: bool = True

    qwen_model_name: str = "qwen-32b"

    generator_llm_enabled: bool = False
    generator_llm_base_url: Optional[str] = None
    generator_llm_api_key: Optional[str] = None
    generator_llm_model: Optional[str] = None
    generator_llm_temperature: float = 0.1

    llm_enabled: bool = True
    llm_provider: str = "openai_compatible"
    llm_base_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("REPAIR_SERVICE_LLM_MODEL_NAME", "REPAIR_SERVICE_LLM_MODEL"),
    )
    llm_temperature: float = 0.1
    llm_max_retries: int = 5
    llm_retry_base_delay_seconds: float = 2.0
    llm_max_concurrency: int = 1

    model_config = SettingsConfigDict(
        env_prefix="REPAIR_SERVICE_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    @model_validator(mode="after")
    def validate_llm_settings(self) -> "Settings":
        enforce_required_llm_config(
            service_name="repair_service",
            llm_enabled=self.llm_enabled,
            llm_base_url=self.llm_base_url,
            llm_api_key=self.llm_api_key,
            llm_model=self.llm_model_name,
        )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
