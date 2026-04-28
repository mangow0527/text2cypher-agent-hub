from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.domain.models import ModelConfig
from app.errors import AppError


class ModelGateway:
    def __init__(self, prompts_dir: Path | None = None) -> None:
        self.prompts_dir = prompts_dir or settings.prompts_dir
        self._client = httpx.Client(timeout=60)

    def render_prompt(self, name: str, **kwargs: Any) -> str:
        template = (self.prompts_dir / f"{name}.txt").read_text(encoding="utf-8")
        return template.format(**kwargs)

    def generate_text(self, prompt_name: str, model_config: ModelConfig, **kwargs: Any) -> str:
        prompt = self.render_prompt(prompt_name, **kwargs)
        if not settings.openai_api_key:
            raise AppError("OPENAI_NOT_CONFIGURED", "Set OPENAI_API_KEY before generating QA questions.")

        last_exc: httpx.HTTPError | None = None
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                response = self._client.post(
                    f"{settings.openai_base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.openai_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_config.model,
                        "messages": [
                            {
                                "role": "user",
                                "content": prompt,
                            }
                        ],
                        "thinking": {"type": "disabled"},
                        "temperature": model_config.temperature,
                        "max_tokens": model_config.max_output_tokens,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                return self._extract_output_text(payload)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code not in {429, 500, 502, 503, 504} or attempt == max_attempts - 1:
                    break
                time.sleep(2.0 * (attempt + 1))
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt == max_attempts - 1:
                    break
                time.sleep(2.0 * (attempt + 1))
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt == max_attempts - 1:
                    break
                time.sleep(2.0 * (attempt + 1))
        raise AppError("OPENAI_REQUEST_ERROR", str(last_exc)) from last_exc

    def judge_consistency(self, prompt_name: str, model_config: ModelConfig, **kwargs: Any) -> bool:
        result = self.generate_text(prompt_name, model_config, **kwargs)
        return result.strip().upper().startswith("PASS")

    def _extract_output_text(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices", [])
        texts: list[str] = []
        for item in choices:
            message = item.get("message", {})
            content = message.get("content", "")
            if isinstance(content, str) and content.strip():
                texts.append(content)
        if texts:
            return "\n".join(texts).strip()
        return json.dumps(payload, ensure_ascii=False)
