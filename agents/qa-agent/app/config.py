from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT_DIR / "artifacts"
PROMPTS_DIR = ROOT_DIR / "prompts"
ENV_FILE = ROOT_DIR / ".env"

load_dotenv(ENV_FILE)


@dataclass(frozen=True)
class Settings:
    app_name: str = "text2cypher-qa-agent"
    artifacts_dir: Path = ARTIFACTS_DIR
    prompts_dir: Path = PROMPTS_DIR
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    openai_model: str = os.getenv("OPENAI_MODEL", "glm-5")
    test_agent_host: str = os.getenv("TEST_AGENT_HOST", "")
    test_agent_question_port: int = int(os.getenv("TEST_AGENT_QUESTION_PORT", "8000"))
    test_agent_golden_port: int = int(os.getenv("TEST_AGENT_GOLDEN_PORT", "8001"))
    host: str = os.getenv("APP_HOST", "127.0.0.1")
    port: int = int(os.getenv("APP_PORT", "8020"))


settings = Settings()
