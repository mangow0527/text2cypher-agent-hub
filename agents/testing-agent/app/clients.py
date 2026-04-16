from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict

import httpx

from .llm_retry import classify_retryable_error, extract_request_id, sleep_with_backoff
from .models import IssueTicket
from .models import KRSSIssueTicketResponse
from .models import QueryQuestionResponse

logger = logging.getLogger("testing_service")


class QueryGeneratorConsoleClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def submit_question(self, *, id: str, question: str) -> QueryQuestionResponse:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/qa/questions",
                json={"id": id, "question": question},
            )
            response.raise_for_status()
            return QueryQuestionResponse.model_validate(response.json())

    async def get_question_run(self, id: str) -> QueryQuestionResponse:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{self.base_url}/api/v1/questions/{id}")
            response.raise_for_status()
            return QueryQuestionResponse.model_validate(response.json())


class ServiceHealthClient:
    async def read_health(self, base_url: str, timeout_seconds: float) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(f"{base_url.rstrip('/')}/health")
            response.raise_for_status()
            return response.json()


class RepairServiceClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def submit_issue_ticket(self, ticket: IssueTicket) -> KRSSIssueTicketResponse:
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/api/v1/issue-tickets",
                    json=ticket.model_dump(),
                )
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.warning(
                    "outbound_call_failed",
                    extra={
                        "target": "krss.issue_tickets",
                        "qa_id": ticket.id,
                        "ticket_id": ticket.ticket_id,
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
                    "target": "krss.issue_tickets",
                    "qa_id": ticket.id,
                    "ticket_id": ticket.ticket_id,
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                },
            )
            return KRSSIssueTicketResponse.model_validate(response.json())


class LLMEvaluationClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        temperature: float,
        *,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_retries: int = 2,
        retry_base_delay_seconds: float = 1.0,
        max_concurrency: int = 1,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.sleep_fn = sleep_fn
        self.max_retries = max_retries
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def evaluate(
        self,
        *,
        qa_id: str | None = None,
        question: str,
        expected_cypher: str,
        expected_answer: Any,
        actual_cypher: str,
        actual_result: Any,
        rule_based_verdict: str,
        rule_based_dimensions: Dict[str, str],
    ) -> Dict[str, Any]:
        system_prompt = (
            "You are a Cypher query evaluation expert for a graph database (TuGraph). "
            "You are given a natural language question, a golden (expected) Cypher query and its expected answer, "
            "and the actual generated Cypher query with its execution result. "
            "You also receive the verdict from a rule-based evaluation system.\n\n"
            "Your task is to provide a semantic assessment that goes beyond exact string matching. "
            "Consider whether the actual query semantically answers the same question, even if the Cypher syntax differs. "
            "Consider whether the actual result is semantically equivalent to the expected result, even if field order, "
            "formatting, or extra fields differ.\n\n"
            "Return JSON only with these keys:\n"
            '- "result_correctness": "pass" or "fail" — is the actual result semantically equivalent to the expected answer?\n'
            '- "question_alignment": "pass" or "fail" — does the actual query target the same semantic intent as the question?\n'
            '- "reasoning": a brief explanation of your judgment\n'
            '- "confidence": a float between 0 and 1'
        )
        user_prompt = (
            f"Question: {question}\n\n"
            f"Expected Cypher: {expected_cypher}\n"
            f"Expected Answer: {json.dumps(expected_answer, ensure_ascii=False, default=str)}\n\n"
            f"Actual Cypher: {actual_cypher}\n"
            f"Actual Result: {json.dumps(actual_result, ensure_ascii=False, default=str)}\n\n"
            f"Rule-based verdict: {rule_based_verdict}\n"
            f"Rule-based dimensions: {json.dumps(rule_based_dimensions, ensure_ascii=False)}\n\n"
            "Provide your semantic evaluation."
        )

        started = time.monotonic()
        logger.warning(
            "llm_call_started target=%s qa_id=%s model=%s base_url=%s",
            "testing.llm_evaluation",
            qa_id,
            self.model,
            self.base_url,
            extra={
                "target": "testing.llm_evaluation",
                "qa_id": qa_id,
                "model": self.model,
                "base_url": self.base_url,
            },
        )
        async with self._semaphore:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                for attempt in range(self.max_retries + 1):
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
                                "enable_thinking": False,
                                "messages": [
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": user_prompt},
                                ],
                            },
                        )
                        response.raise_for_status()
                        payload = response.json()
                        break
                    except Exception as exc:
                        elapsed_ms = int((time.monotonic() - started) * 1000)
                        retry = classify_retryable_error(exc)
                        is_last_attempt = attempt >= self.max_retries
                        if retry.should_retry and not is_last_attempt:
                            delay_seconds = await sleep_with_backoff(
                                sleep_fn=self.sleep_fn,
                                base_delay_seconds=self.retry_base_delay_seconds,
                                attempt_index=attempt,
                                retry_after_seconds=retry.retry_after_seconds,
                            )
                            logger.warning(
                                "llm_call_retry target=%s qa_id=%s model=%s base_url=%s attempt=%s elapsed_ms=%s retry_reason=%s status_code=%s retry_delay_seconds=%s body_preview=%s",
                                "testing.llm_evaluation",
                                qa_id,
                                self.model,
                                self.base_url,
                                attempt + 1,
                                elapsed_ms,
                                retry.reason,
                                retry.status_code,
                                delay_seconds,
                                retry.body_preview,
                                extra={
                                    "target": "testing.llm_evaluation",
                                    "qa_id": qa_id,
                                    "model": self.model,
                                    "base_url": self.base_url,
                                    "attempt": attempt + 1,
                                    "elapsed_ms": elapsed_ms,
                                    "retry_reason": retry.reason,
                                    "status_code": retry.status_code,
                                    "retry_delay_seconds": delay_seconds,
                                    "body_preview": retry.body_preview,
                                },
                            )
                            continue
                        logger.warning(
                            "llm_call_failed target=%s qa_id=%s model=%s base_url=%s elapsed_ms=%s attempts=%s retry_reason=%s status_code=%s body_preview=%s error=%s",
                            "testing.llm_evaluation",
                            qa_id,
                            self.model,
                            self.base_url,
                            elapsed_ms,
                            attempt + 1,
                            retry.reason,
                            retry.status_code,
                            retry.body_preview,
                            str(exc),
                            extra={
                                "target": "testing.llm_evaluation",
                                "qa_id": qa_id,
                                "model": self.model,
                                "base_url": self.base_url,
                                "elapsed_ms": elapsed_ms,
                                "attempts": attempt + 1,
                                "retry_reason": retry.reason,
                                "status_code": retry.status_code,
                                "body_preview": retry.body_preview,
                                "error": str(exc),
                            },
                        )
                        raise

        content = payload["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:].strip()

        result = json.loads(content)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        request_id = extract_request_id(getattr(response, "headers", None))
        logger.warning(
            "llm_call_succeeded target=%s qa_id=%s model=%s base_url=%s elapsed_ms=%s request_id=%s",
            "testing.llm_evaluation",
            qa_id,
            self.model,
            self.base_url,
            elapsed_ms,
            request_id,
            extra={
                "target": "testing.llm_evaluation",
                "qa_id": qa_id,
                "model": self.model,
                "base_url": self.base_url,
                "elapsed_ms": elapsed_ms,
                "request_id": request_id,
            },
        )
        logger.info(
            "LLM evaluation: result_correctness=%s, question_alignment=%s, confidence=%s",
            result.get("result_correctness"),
            result.get("question_alignment"),
            result.get("confidence"),
        )
        return result
