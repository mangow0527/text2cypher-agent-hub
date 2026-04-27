from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict

import httpx

from services.repair_agent.app.models import RepairIssueTicketResponse

from .models import IssueTicket

logger = logging.getLogger("testing_service")


class RepairServiceClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def submit_issue_ticket(self, ticket: IssueTicket) -> RepairIssueTicketResponse:
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/issue-tickets",
                json=ticket.model_dump(mode="json"),
            )
            response.raise_for_status()
            logger.info(
                "repair_issue_ticket_submitted",
                extra={
                    "target": "repair.issue_tickets",
                    "qa_id": ticket.id,
                    "ticket_id": ticket.ticket_id,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                },
            )
            return RepairIssueTicketResponse.model_validate(response.json())


class OpenAICompatibleLLMClient:
    def __init__(
        self,
        *,
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

    async def complete_json(self, prompt: str, *, qa_id: str | None = None, target: str = "testing.llm") -> Dict[str, Any]:
        started = time.monotonic()
        logger.info(
            "llm_call_started target=%s qa_id=%s model=%s",
            target,
            qa_id,
            self.model,
            extra={"target": target, "qa_id": qa_id, "model": self.model},
        )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
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
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:].strip()
        payload = json.loads(content)
        request_id = None
        headers = getattr(response, "headers", None) or {}
        request_id = headers.get("request-id") or headers.get("x-request-id")
        logger.info(
            "llm_call_succeeded target=%s qa_id=%s model=%s elapsed_ms=%s request_id=%s",
            target,
            qa_id,
            self.model,
            int((time.monotonic() - started) * 1000),
            request_id,
            extra={
                "target": target,
                "qa_id": qa_id,
                "model": self.model,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "request_id": request_id,
            },
        )
        return payload


class LLMEvaluationClient(OpenAICompatibleLLMClient):
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
        prompt = (
            "你是 testing-agent 的语义评测器。"
            "请返回 JSON，包含 result_correctness、question_alignment、reasoning、confidence。\n\n"
            f"Question: {question}\n"
            f"Expected Cypher: {expected_cypher}\n"
            f"Expected Answer: {json.dumps(expected_answer, ensure_ascii=False, default=str)}\n"
            f"Actual Cypher: {actual_cypher}\n"
            f"Actual Result: {json.dumps(actual_result, ensure_ascii=False, default=str)}\n"
            f"Rule Verdict: {rule_based_verdict}\n"
            f"Rule Dimensions: {json.dumps(rule_based_dimensions, ensure_ascii=False)}"
        )
        return await self.complete_json(prompt, qa_id=qa_id, target="testing.llm_evaluation")


class GrammarExplanationClient:
    def __init__(self, llm: OpenAICompatibleLLMClient) -> None:
        self.llm = llm

    async def explain(self, generated_cypher: str, parser_error: str) -> str:
        prompt = (
            "你是 testing-agent 中的 Cypher 语法错误解释器。"
            "只解释语法错误，不参与 grammar 判定，不提供修复建议。"
            "输入必须包含 generated_cypher 与 parser_error。"
            "输出必须是 JSON，且只能包含 message 一个字段。\n\n"
            f"Generated Cypher:\n{generated_cypher}\n\nParser Error:\n{parser_error}"
        )
        payload = await self.llm.complete_json(prompt)
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            raise RuntimeError("Grammar explanation returned invalid message.")
        return message.strip()


class SemanticReviewClient:
    def __init__(self, llm: OpenAICompatibleLLMClient) -> None:
        self.llm = llm

    async def review(
        self,
        *,
        question: str,
        gold_cypher: str,
        gold_answer: Any,
        generated_cypher: str,
        actual_answer: Any,
        strict_check_message: str | None,
        strict_diff: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        prompt = (
            "你是 testing-agent 中的 Cypher 语义复核器。"
            "只在 strict compare 已失败的前提下，判断 actual_answer 是否仍满足 question。"
            "不提供修复建议，不讨论模型行为、prompt 设计或 schema 问题。"
            "输出必须是 JSON，并至少包含 judgement 与 reasoning。\n\n"
            f"Question:\n{question}\n\n"
            f"Gold Cypher:\n{gold_cypher}\n\n"
            f"Gold Answer:\n{json.dumps(gold_answer, ensure_ascii=False, default=str)}\n\n"
            f"Generated Cypher:\n{generated_cypher}\n\n"
            f"Actual Answer:\n{json.dumps(actual_answer, ensure_ascii=False, default=str)}\n\n"
            f"Strict Check Message:\n{strict_check_message}\n\n"
            f"Strict Diff:\n{json.dumps(strict_diff, ensure_ascii=False, default=str)}"
        )
        payload = await self.llm.complete_json(prompt)
        judgement = _normalize_semantic_judgement(payload)
        if judgement is None:
            raise RuntimeError("Semantic review returned invalid judgement.")
        payload["judgement"] = judgement
        return payload


def _normalize_semantic_judgement(payload: Dict[str, Any]) -> str | None:
    for key in ("judgement", "judgment", "result_correctness", "semantic_equivalence"):
        normalized = _normalize_pass_fail_token(payload.get(key))
        if normalized is not None:
            return normalized

    for key in ("is_equivalent", "equivalent", "is_correct", "correct"):
        value = payload.get(key)
        if isinstance(value, bool):
            return "pass" if value else "fail"

    return None


def _normalize_pass_fail_token(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = value.strip().lower()
    if normalized in {"pass", "passed", "ok", "true", "yes", "equivalent", "correct"}:
        return "pass"
    if normalized in {"fail", "failed", "false", "no", "not_equivalent", "incorrect"}:
        return "fail"
    return None
