from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "runtime-results-service"
    host: str = "0.0.0.0"
    port: int = 8001
    query_generator_data_dir: str = "data/query_generator_service"
    testing_data_dir: str = "data/testing_service"
    repair_data_dir: str = "data/repair_service"
    poll_interval_seconds: int = 5
    query_generator_base_url: str = "http://127.0.0.1:8000"
    testing_service_base_url: str = "http://127.0.0.1:8003"
    repair_service_base_url: str = "http://127.0.0.1:8002"
    knowledge_ops_base_url: str = "http://127.0.0.1:8010"
    qa_generator_base_url: str = "http://127.0.0.1:8020"

    model_config = SettingsConfigDict(
        env_prefix="RUNTIME_RESULTS_SERVICE_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
