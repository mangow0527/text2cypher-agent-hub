from __future__ import annotations

from typing import Any

import httpx

from app.config import settings
from app.errors import AppError


class ModelGateway:
    def __init__(self) -> None:
        self._client = httpx.Client(timeout=60)

    def generate_text(self, prompt_name: str, model_config: dict[str, Any], **kwargs: Any) -> str:
        prompt = kwargs["prompt"]
        if not settings.openai_api_key:
            raise AppError("OPENAI_NOT_CONFIGURED", "Set OPENAI_API_KEY before applying repair suggestions.")

        response = self._client.post(
            f"{settings.openai_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_config.get("model", settings.openai_model),
                "messages": [{"role": "user", "content": prompt}],
                "thinking": {"type": "disabled"},
                "temperature": model_config.get("temperature", 0.1),
                "max_tokens": model_config.get("max_output_tokens", 800),
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AppError("OPENAI_REQUEST_ERROR", str(exc)) from exc

        payload = response.json()
        choices = payload.get("choices", [])
        if not choices:
            raise AppError("OPENAI_EMPTY_RESPONSE", "Model response did not contain any choices.")
        return choices[0]["message"]["content"].strip()
