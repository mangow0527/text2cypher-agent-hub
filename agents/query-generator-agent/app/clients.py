from __future__ import annotations

import json
import logging
import time
from typing import Dict, Optional

import httpx

from .models import EvaluationSubmissionRequest, EvaluationSubmissionResponse
logger = logging.getLogger("query_generator")


def _extract_request_id(headers: object) -> str | None:
    if not headers:
        return None
    for key in ("x-request-id", "request-id", "x-trace-id"):
        value = getattr(headers, "get", lambda _key, _default=None: None)(key, None)
        if value:
            return str(value)
    return None


class OpenAICompatibleCypherGenerator:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        temperature: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature

    async def generate_from_prompt(self, task_id: str, question_text: str, generation_prompt: str) -> Dict[str, str]:
        started = time.monotonic()
        logger.warning(
            "llm_call_started target=%s qa_id=%s model=%s base_url=%s",
            "query_generator.llm",
            task_id,
            self.model,
            self.base_url,
            extra={
                "target": "query_generator.llm",
                "qa_id": task_id,
                "model": self.model,
                "base_url": self.base_url,
            },
        )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "temperature": self.temperature,
                        "messages": [{"role": "user", "content": generation_prompt}],
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.warning(
                    "llm_call_failed target=%s qa_id=%s model=%s base_url=%s elapsed_ms=%s error=%s",
                    "query_generator.llm",
                    task_id,
                    self.model,
                    self.base_url,
                    elapsed_ms,
                    str(exc),
                    extra={
                        "target": "query_generator.llm",
                        "qa_id": task_id,
                        "model": self.model,
                        "base_url": self.base_url,
                        "elapsed_ms": elapsed_ms,
                        "error": str(exc),
                    },
                )
                raise

        content = payload["choices"][0]["message"]["content"]
        elapsed_ms = int((time.monotonic() - started) * 1000)
        request_id = _extract_request_id(getattr(response, "headers", None))
        logger.warning(
            "llm_call_succeeded target=%s qa_id=%s model=%s base_url=%s elapsed_ms=%s request_id=%s",
            "query_generator.llm",
            task_id,
            self.model,
            self.base_url,
            elapsed_ms,
            request_id,
            extra={
                "target": "query_generator.llm",
                "qa_id": task_id,
                "model": self.model,
                "base_url": self.base_url,
                "elapsed_ms": elapsed_ms,
                "request_id": request_id,
            },
        )
        return {
            "raw_output": content,
            "model_name": self.model,
        }


class QwenGeneratorClient:
    def __init__(self, llm_generator: Optional[OpenAICompatibleCypherGenerator] = None) -> None:
        self.llm_generator = llm_generator

    async def generate_from_prompt(self, task_id: str, question_text: str, generation_prompt: str) -> Dict[str, str]:
        if self.llm_generator is None:
            raise RuntimeError("LLM generator is required for Cypher generation service.")

        logger.info("LLM call started for id=%s", task_id)
        result = await self.llm_generator.generate_from_prompt(task_id, question_text, generation_prompt)
        logger.info("LLM call succeeded for id=%s", task_id)
        return result


class PromptServiceClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def fetch_prompt(self, id: str, question: str) -> str:
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/api/knowledge/rag/prompt-package",
                    json={"id": id, "question": question},
                )
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.warning(
                    "outbound_call_failed",
                    extra={
                        "target": "knowledge_ops.prompt_package",
                        "qa_id": id,
                        "elapsed_ms": elapsed_ms,
                        "error": str(exc),
                    },
                )
                raise
            response.raise_for_status()
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "outbound_call_ok",
                extra={
                    "target": "knowledge_ops.prompt_package",
                    "qa_id": id,
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                },
            )
            headers = getattr(response, "headers", {}) or {}
            content_type = str(headers.get("content-type", "")).lower()
            if "application/json" in content_type:
                raise ValueError("prompt-package contract violation: expected plain text prompt string, got JSON response")
            return response.text.strip()


class TestingServiceClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def submit(self, payload: EvaluationSubmissionRequest) -> EvaluationSubmissionResponse:
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/api/v1/evaluations/submissions",
                    json=payload.model_dump(),
                )
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.warning(
                    "outbound_call_failed",
                    extra={
                        "target": "testing.submission",
                        "qa_id": payload.id,
                        "elapsed_ms": elapsed_ms,
                        "error": str(exc),
                    },
                )
                raise
            response.raise_for_status()
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "outbound_call_ok",
                extra={
                    "target": "testing.submission",
                    "qa_id": payload.id,
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                },
            )
            return EvaluationSubmissionResponse.model_validate(response.json())
