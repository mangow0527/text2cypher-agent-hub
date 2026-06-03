from __future__ import annotations

import logging
import time
from typing import Any, Dict

import httpx

from services.cypher_generator_agent.app.api.models import (
    CgaGenerationNonSuccessReport,
    CgaQuestionReceivedReport,
    GeneratedCypherSubmissionRequest,
)


logger = logging.getLogger("cypher_generator_agent")


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

    async def submit_question_received(self, payload: CgaQuestionReceivedReport) -> Dict[str, Any]:
        return await self._submit_with_retries(
            payload=payload,
            endpoint_path="/api/v1/evaluations/questions",
            target="testing_agent.question_received",
        )

    async def submit_generation_failure(self, payload: CgaGenerationNonSuccessReport) -> Dict[str, Any]:
        return await self._submit_with_retries(
            payload=payload,
            endpoint_path="/api/v1/evaluations/generation-failures",
            target="testing_agent.generation_failure",
        )

    async def _submit_with_retries(
        self,
        *,
        payload: GeneratedCypherSubmissionRequest | CgaQuestionReceivedReport | CgaGenerationNonSuccessReport,
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
        payload: GeneratedCypherSubmissionRequest | CgaQuestionReceivedReport | CgaGenerationNonSuccessReport,
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
