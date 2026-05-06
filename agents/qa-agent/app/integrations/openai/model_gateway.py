from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.domain.models import ModelConfig
from app.errors import AppError
from app.logging import ModuleLogStore


class ModelGateway:
    def __init__(self, prompts_dir: Path | None = None, module_logs: ModuleLogStore | None = None) -> None:
        self.prompts_dir = prompts_dir or settings.prompts_dir
        self._client = httpx.Client(timeout=60)
        self._module_logs = module_logs or ModuleLogStore()

    def render_prompt(self, name: str, **kwargs: Any) -> str:
        template = (self.prompts_dir / f"{name}.txt").read_text(encoding="utf-8")
        return template.format(**kwargs)

    def generate_text(self, prompt_name: str, model_config: ModelConfig, **kwargs: Any) -> str:
        prompt = self.render_prompt(prompt_name, **kwargs)
        if not settings.openai_api_key:
            raise AppError("OPENAI_NOT_CONFIGURED", "Set OPENAI_API_KEY before generating QA questions.")

        last_exc: httpx.HTTPError | None = None
        max_attempts = 5
        request_body = self._request_log_body(prompt_name, model_config, prompt, kwargs)
        for attempt in range(max_attempts):
            attempt_number = attempt + 1
            self._log_attempt(
                level="info",
                operation="llm_request_started",
                status="started",
                request_body=request_body,
                attempt=attempt_number,
            )
            started = time.perf_counter()
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
                output_text = self._extract_output_text(payload)
                self._log_attempt(
                    level="info",
                    operation="llm_request_completed",
                    status="success",
                    request_body=request_body,
                    attempt=attempt_number,
                    duration_ms=self._duration_ms(started),
                    response_body={
                        "status_code": response.status_code,
                        "output_chars": len(output_text),
                    },
                )
                return output_text
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                should_retry = exc.response.status_code in {429, 500, 502, 503, 504} and attempt < max_attempts - 1
                self._log_attempt(
                    level="warning" if should_retry else "error",
                    operation="llm_request_retry" if should_retry else "llm_request_failed",
                    status="retry" if should_retry else "failed",
                    request_body=request_body,
                    attempt=attempt_number,
                    duration_ms=self._duration_ms(started),
                    response_body={
                        "error_type": exc.__class__.__name__,
                        "status_code": exc.response.status_code,
                    },
                )
                if not should_retry:
                    break
                time.sleep(2.0 * (attempt + 1))
            except httpx.TimeoutException as exc:
                last_exc = exc
                should_retry = attempt < max_attempts - 1
                self._log_attempt(
                    level="warning" if should_retry else "error",
                    operation="llm_request_retry" if should_retry else "llm_request_failed",
                    status="retry" if should_retry else "failed",
                    request_body=request_body,
                    attempt=attempt_number,
                    duration_ms=self._duration_ms(started),
                    response_body={"error_type": exc.__class__.__name__},
                )
                if not should_retry:
                    break
                time.sleep(2.0 * (attempt + 1))
            except httpx.HTTPError as exc:
                last_exc = exc
                should_retry = attempt < max_attempts - 1
                self._log_attempt(
                    level="warning" if should_retry else "error",
                    operation="llm_request_retry" if should_retry else "llm_request_failed",
                    status="retry" if should_retry else "failed",
                    request_body=request_body,
                    attempt=attempt_number,
                    duration_ms=self._duration_ms(started),
                    response_body={"error_type": exc.__class__.__name__},
                )
                if not should_retry:
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

    def _request_log_body(
        self,
        prompt_name: str,
        model_config: ModelConfig,
        prompt: str,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        requests = self._requests_json_items(kwargs.get("requests_json"))
        request_ids = [
            str(item.get("request_id", "")).strip()
            for item in requests[:5]
            if isinstance(item, dict) and str(item.get("request_id", "")).strip()
        ]
        return {
            "prompt_name": prompt_name,
            "model": model_config.model,
            "temperature": model_config.temperature,
            "max_tokens": model_config.max_output_tokens,
            "prompt_chars": len(prompt),
            "batch_size": len(requests),
            "request_ids": request_ids,
        }

    def _requests_json_items(self, payload: Any) -> list[dict[str, Any]]:
        if not payload:
            return []
        try:
            data = json.loads(str(payload))
        except json.JSONDecodeError:
            return []
        items = data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []
        return [item for item in items if isinstance(item, dict)]

    def _log_attempt(
        self,
        *,
        level: str,
        operation: str,
        status: str,
        request_body: dict[str, Any],
        attempt: int,
        duration_ms: int | None = None,
        response_body: dict[str, Any] | None = None,
    ) -> None:
        self._module_logs.append(
            module="openai",
            level=level,
            operation=operation,
            status=status,
            request_body=request_body,
            response_body=response_body,
            attempt=attempt,
            duration_ms=duration_ms,
        )

    def _duration_ms(self, started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
