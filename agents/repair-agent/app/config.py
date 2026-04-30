from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .llm_config import enforce_required_llm_config


class Settings(BaseSettings):
    __test__ = False
    app_name: str = "repair-service"
    host: str = "0.0.0.0"
    port: int = 8002
    data_dir: str = "data/repair_service"
    knowledge_agent_repairs_apply_url: str = "http://127.0.0.1:8010/api/knowledge/repairs/apply"
    knowledge_agent_repairs_apply_capture_dir: Optional[str] = None
    knowledge_agent_repairs_apply_max_attempts: int = 5
    request_timeout_seconds: float = 120.0

    llm_enabled: bool = True
    llm_provider: str = "openai_chat"
    llm_base_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model_name: Optional[str] = None
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
