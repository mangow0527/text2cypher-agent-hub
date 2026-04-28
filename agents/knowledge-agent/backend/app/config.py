from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT_DIR / "artifacts"
KNOWLEDGE_DIR = ROOT_DIR / "knowledge"
ENV_FILE = ROOT_DIR / ".env"
QA_AGENT_ENV_FILE = ROOT_DIR.parent.parent / "qa-agent" / ".env"

load_dotenv(ENV_FILE, override=True)
QA_AGENT_ENV = dotenv_values(QA_AGENT_ENV_FILE)


def env_or_qa_agent(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value not in {None, ""}:
        return value
    qa_value = QA_AGENT_ENV.get(name)
    if isinstance(qa_value, str) and qa_value:
        return qa_value
    return default


@dataclass(frozen=True)
class Settings:
    app_name: str = "knowledge-agent"
    artifacts_dir: Path = ARTIFACTS_DIR
    knowledge_dir: Path = KNOWLEDGE_DIR
    openai_api_key: str = env_or_qa_agent("OPENAI_API_KEY", "")
    openai_base_url: str = env_or_qa_agent("OPENAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    openai_model: str = env_or_qa_agent("OPENAI_MODEL", "glm-5")
    qa_agent_base_url: str = os.getenv("QA_AGENT_BASE_URL", "http://127.0.0.1:8020")
    host: str = os.getenv("APP_HOST", "0.0.0.0")
    port: int = int(os.getenv("APP_PORT", "8010"))
    slow_request_threshold_ms: int = int(os.getenv("SLOW_REQUEST_THRESHOLD_MS", "5000"))


settings = Settings()
