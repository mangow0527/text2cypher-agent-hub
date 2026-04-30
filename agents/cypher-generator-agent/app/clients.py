from __future__ import annotations

import logging
import time
from typing import Any, Dict

import httpx

from .models import GeneratedCypherSubmissionRequest, GenerationRunFailureReport


logger = logging.getLogger("cypher_generator_agent")


def _extract_request_id(headers: object) -> str | None:
    if not headers:
        return None
    for key in ("x-request-id", "request-id", "x-trace-id"):
        value = getattr(headers, "get", lambda _key, _default=None: None)(key, None)
        if value:
            return str(value)
    return None


class OpenAIChatCompletionCypherGenerator:
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

    async def generate_from_prompt(
        self,
        *,
        task_id: str,
        question_text: str,
        llm_prompt: str,
    ) -> Dict[str, str]:
        started = time.monotonic()
        logger.warning(
            "llm_call_started target=%s qa_id=%s model=%s base_url=%s",
            "cypher_generator_agent.llm",
            task_id,
            self.model,
            self.base_url,
            extra={
                "target": "cypher_generator_agent.llm",
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
                        "messages": [{"role": "user", "content": llm_prompt}],
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.warning(
                    "llm_call_failed target=%s qa_id=%s model=%s base_url=%s elapsed_ms=%s error=%s",
                    "cypher_generator_agent.llm",
                    task_id,
                    self.model,
                    self.base_url,
                    elapsed_ms,
                    str(exc),
                    extra={
                        "target": "cypher_generator_agent.llm",
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
            "cypher_generator_agent.llm",
            task_id,
            self.model,
            self.base_url,
            elapsed_ms,
            request_id,
            extra={
                "target": "cypher_generator_agent.llm",
                "qa_id": task_id,
                "model": self.model,
                "base_url": self.base_url,
                "elapsed_ms": elapsed_ms,
                "request_id": request_id,
            },
        )
        return {"raw_output": content, "model_name": self.model}


class CypherLLMClient:
    def __init__(self, llm_generator: OpenAIChatCompletionCypherGenerator | None = None) -> None:
        self.llm_generator = llm_generator

    async def generate_from_prompt(
        self,
        *,
        task_id: str,
        question_text: str,
        llm_prompt: str,
    ) -> Dict[str, str]:
        if self.llm_generator is None:
            raise RuntimeError("LLM generator is required for cypher-generator-agent.")
        return await self.llm_generator.generate_from_prompt(
            task_id=task_id,
            question_text=question_text,
            llm_prompt=llm_prompt,
        )


class TestingAgentClient:
    __test__ = False

    def __init__(self, base_url: str, timeout_seconds: float, max_submit_attempts: int = 3) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_submit_attempts = max_submit_attempts

    async def submit(self, payload: GeneratedCypherSubmissionRequest) -> Dict[str, Any]:
        return await self._submit_with_retries(
            payload=payload,
            endpoint_path="/api/v1/evaluations/submissions",
            target="testing_agent.submission",
        )

    async def submit_generation_failure(self, payload: GenerationRunFailureReport) -> Dict[str, Any]:
        return await self._submit_with_retries(
            payload=payload,
            endpoint_path="/api/v1/evaluations/generation-failures",
            target="testing_agent.generation_failure",
        )

    async def _submit_with_retries(
        self,
        *,
        payload: GeneratedCypherSubmissionRequest | GenerationRunFailureReport,
        endpoint_path: str,
        target: str,
    ) -> Dict[str, Any]:
        last_error: Exception | None = None
        for submit_index in range(1, self.max_submit_attempts + 1):
            try:
                return await self._submit_once(
                    payload=payload,
                    endpoint_path=endpoint_path,
                    target=target,
                    submit_index=submit_index,
                )
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status_code = exc.response.status_code
                if status_code < 500 or status_code == 409:
                    raise
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("testing-agent submission failed")

    async def _submit_once(
        self,
        *,
        payload: GeneratedCypherSubmissionRequest | GenerationRunFailureReport,
        endpoint_path: str,
        target: str,
        submit_index: int,
    ) -> Dict[str, Any]:
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.post(
                    f"{self.base_url}{endpoint_path}",
                    json=payload.model_dump(),
                )
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.warning(
                    "outbound_call_failed",
                    extra={
                        "target": target,
                        "qa_id": payload.id,
                        "submit_index": submit_index,
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
                    "target": target,
                    "qa_id": payload.id,
                    "submit_index": submit_index,
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                },
            )
            ack = response.json()
            if not isinstance(ack, dict) or ack.get("accepted") is not True:
                raise ValueError("testing-agent submission ack contract violation: expected {'accepted': true}")
            return {"accepted": True}
