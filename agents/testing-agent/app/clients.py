from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict

import httpx

from .models import IssueTicket, RepairAgentResponse

logger = logging.getLogger("testing_service")


@dataclass
class JSONCompletionResponse:
    payload: Dict[str, Any]
    raw_text: str
    request_id: str | None
    model: str


class InvalidSemanticReviewResponse(RuntimeError):
    def __init__(
        self,
        *,
        raw_text: str,
        payload: Dict[str, Any] | None,
        request_id: str | None,
        model: str,
        prompt_snapshot: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.payload = payload
        self.request_id = request_id
        self.model = model
        self.prompt_snapshot = prompt_snapshot


class RepairServiceClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def submit_issue_ticket(self, ticket: IssueTicket) -> RepairAgentResponse:
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
            return RepairAgentResponse.model_validate(response.json())


class OpenAIChatCompletionLLMClient:
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

    async def complete_json_response(
        self,
        prompt: str,
        *,
        qa_id: str | None = None,
        target: str = "testing.llm",
    ) -> JSONCompletionResponse:
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
        raw_text = response.json()["choices"][0]["message"]["content"].strip()
        payload = json.loads(_strip_code_fence(raw_text))
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
        return JSONCompletionResponse(
            payload=payload,
            raw_text=raw_text,
            request_id=request_id,
            model=self.model,
        )

    async def complete_json(self, prompt: str, *, qa_id: str | None = None, target: str = "testing.llm") -> Dict[str, Any]:
        response = await self.complete_json_response(prompt, qa_id=qa_id, target=target)
        return response.payload


class LLMEvaluationClient(OpenAIChatCompletionLLMClient):
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
    def __init__(self, llm: OpenAIChatCompletionLLMClient) -> None:
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
    def __init__(self, llm: OpenAIChatCompletionLLMClient) -> None:
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
            "输出必须是 JSON object，并且至少包含 judgement 与 reasoning。"
            "judgement 只能取字符串 \"pass\" 或 \"fail\"。"
            "reasoning 必须使用中文，不能使用英文解释；可以保留 label、relation、Cypher、ID 等原始英文术语。"
            "reasoning 应简洁说明 actual_answer 是否满足 question，以及与 Gold Answer/Strict Diff 的关键差异。"
            "不要输出 valid、invalid、correct、incorrect、equivalent、not_equivalent 或任何第三状态。"
            "如果你本来想表达正确/等价，请输出 \"pass\"；如果你本来想表达错误/不等价，请输出 \"fail\"。\n\n"
            f"Question:\n{question}\n\n"
            f"Gold Cypher:\n{gold_cypher}\n\n"
            f"Gold Answer:\n{json.dumps(gold_answer, ensure_ascii=False, default=str)}\n\n"
            f"Generated Cypher:\n{generated_cypher}\n\n"
            f"Actual Answer:\n{json.dumps(actual_answer, ensure_ascii=False, default=str)}\n\n"
            f"Strict Check Message:\n{strict_check_message}\n\n"
            f"Strict Diff:\n{json.dumps(strict_diff, ensure_ascii=False, default=str)}"
        )
        response = await self.llm.complete_json_response(prompt)
        payload = response.payload
        judgement = _normalize_semantic_judgement(payload)
        reasoning = payload.get("reasoning")
        if judgement is None or not isinstance(reasoning, str) or not reasoning.strip():
            raise InvalidSemanticReviewResponse(
                raw_text=response.raw_text,
                payload=payload,
                request_id=response.request_id,
                model=response.model,
                prompt_snapshot=prompt,
                message="Semantic review returned invalid judgement.",
            )
        if not _contains_chinese(reasoning):
            raise InvalidSemanticReviewResponse(
                raw_text=response.raw_text,
                payload=payload,
                request_id=response.request_id,
                model=response.model,
                prompt_snapshot=prompt,
                message="Semantic review returned invalid Chinese reasoning.",
            )
        payload["judgement"] = judgement
        payload["reasoning"] = reasoning.strip()
        payload["_raw_text"] = response.raw_text
        payload["_request_id"] = response.request_id
        payload["_model"] = response.model
        payload["_prompt_snapshot"] = prompt
        return payload


def _normalize_semantic_judgement(payload: Dict[str, Any]) -> str | None:
    return _normalize_pass_fail_token(payload.get("judgement"))


def _normalize_pass_fail_token(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = value.strip().lower()
    if normalized == "pass":
        return "pass"
    if normalized == "fail":
        return "fail"
    return None


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _strip_code_fence(content: str) -> str:
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:].strip()
    return content
