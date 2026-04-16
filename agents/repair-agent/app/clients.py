from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx

from .llm_retry import classify_retryable_error, extract_request_id, sleep_with_backoff
from .models import IssueTicket, KnowledgeRepairSuggestionRequest, PromptSnapshotResponse
from services.repair_agent.app.analysis import build_diagnosis_context

logger = logging.getLogger("repair_service")


def _dedupe_lines(text: str) -> str:
    seen: set[str] = set()
    compacted: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        normalized = line.strip()
        if normalized and normalized in seen:
            continue
        if normalized:
            seen.add(normalized)
        if line or (compacted and compacted[-1] != ""):
            compacted.append(line)
    while compacted and compacted[-1] == "":
        compacted.pop()
    return "\n".join(compacted)


def _compact_prompt_snapshot(prompt_snapshot: str, max_chars: int = 1200) -> str:
    compacted = _dedupe_lines(prompt_snapshot.strip())
    if len(compacted) <= max_chars:
        return compacted
    head_budget = max_chars // 2
    tail_budget = max_chars - head_budget - len("\n...[prompt truncated]...\n")
    return compacted[:head_budget].rstrip() + "\n...[prompt truncated]...\n" + compacted[-tail_budget:].lstrip()


def _trim_text(value: str | None, max_chars: int) -> str | None:
    if not value:
        return value
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def _compact_json_value(value: Any, max_chars: int = 600) -> Any:
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    if len(serialized) <= max_chars:
        return value
    if isinstance(value, list):
        compacted = value[:1]
    elif isinstance(value, dict):
        compacted = {key: value[key] for key in list(value)[:6]}
    else:
        compacted = str(value)
    serialized = json.dumps(compacted, ensure_ascii=False, default=str)
    if len(serialized) <= max_chars:
        return compacted
    return _trim_text(serialized, max_chars)


def _build_krss_ticket_payload(ticket: IssueTicket) -> dict[str, Any]:
    execution = ticket.actual.execution
    return {
        "ticket_id": ticket.ticket_id,
        "id": ticket.id,
        "difficulty": ticket.difficulty,
        "question": ticket.question,
        "expected": {"cypher": ticket.expected.cypher},
        "actual": {
            "generated_cypher": ticket.actual.generated_cypher,
            "execution": {
                "success": execution.success,
                "row_count": execution.row_count,
                "error_message": _trim_text(execution.error_message, 240),
                "elapsed_ms": execution.elapsed_ms,
            },
        },
        "evaluation": {
            "verdict": ticket.evaluation.verdict,
            "dimensions": ticket.evaluation.dimensions.model_dump(mode="json"),
            "symptom": _trim_text(ticket.evaluation.symptom, 240),
            "evidence_preview": ticket.evaluation.evidence[:2],
        },
    }


def _build_krss_ticket_payload_from_context(context: Dict[str, Any]) -> dict[str, Any]:
    evaluation_summary = context.get("evaluation_summary") or {}
    return {
        "ticket_id": context.get("ticket_id") or "diagnosis-context",
        "id": context.get("id") or "unknown",
        "difficulty": context.get("difficulty") or "L1",
        "question": context.get("question") or "",
        "expected": {"cypher": (context.get("sql_pair") or {}).get("expected_cypher", "")},
        "actual": {"generated_cypher": (context.get("sql_pair") or {}).get("actual_cypher", "")},
        "evaluation": {
            "verdict": evaluation_summary.get("verdict") or "fail",
            "dimensions": evaluation_summary.get("dimensions") or {},
            "symptom": _trim_text(str(evaluation_summary.get("symptom") or ""), 240),
            "evidence_preview": (evaluation_summary.get("evidence_preview") or [])[:2],
        },
    }


def _compact_relevant_prompt_fragments(fragments: Dict[str, Any]) -> Dict[str, Any]:
    compacted: Dict[str, Any] = {}
    seen_lines: set[str] = set()
    for key, value in fragments.items():
        if isinstance(value, str):
            unique_lines: list[str] = []
            for raw_line in value.splitlines():
                line = raw_line.strip()
                if not line or line in seen_lines:
                    continue
                seen_lines.add(line)
                unique_lines.append(raw_line)
            compacted[key] = _compact_prompt_snapshot("\n".join(unique_lines), max_chars=320)
        else:
            compacted[key] = value
    return compacted


def _compact_recent_repairs(repairs: Any) -> list[dict[str, Any]]:
    if not isinstance(repairs, list):
        return []
    compacted: list[dict[str, Any]] = []
    for repair in repairs[:2]:
        if not isinstance(repair, dict):
            continue
        compacted.append(
            {
                "knowledge_type": repair.get("knowledge_type"),
                "suggestion": _trim_text(str(repair.get("suggestion") or ""), 180),
            }
        )
    return compacted


def _compact_diagnosis_context(context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "question": context.get("question"),
        "difficulty": context.get("difficulty"),
        "sql_pair": context.get("sql_pair"),
        "evaluation_summary": context.get("evaluation_summary"),
        "failure_diff": context.get("failure_diff"),
        "relevant_prompt_fragments": _compact_relevant_prompt_fragments(context.get("relevant_prompt_fragments", {})),
        "recent_applied_repairs": _compact_recent_repairs(context.get("recent_applied_repairs")),
    }


class OpenAICompatibleKRSSAnalyzer:
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

    async def diagnose(
        self,
        context: Dict[str, Any] | None = None,
        *,
        ticket: IssueTicket | None = None,
        prompt_snapshot: str | None = None,
    ) -> Dict[str, Any]:
        if context is None:
            if ticket is None:
                raise ValueError("ticket is required when context is not provided")
            context = build_diagnosis_context(ticket, prompt_snapshot or "")
        compact_context = _compact_diagnosis_context(context)
        compact_ticket = _build_krss_ticket_payload(ticket) if ticket is not None else _build_krss_ticket_payload_from_context(context)
        system_prompt = (
            "You are the Knowledge Repair Suggestion Service for a Text2Cypher system. "
            "Diagnose root cause using only these knowledge types: cypher_syntax, few_shot, system_prompt, business_knowledge. "
            "Return JSON only with keys primary_knowledge_type, secondary_knowledge_types, candidate_patch_types, confidence, suggestion, rationale, need_validation."
        )
        user_prompt = (
            f"IssueTicketSummary: {json.dumps(compact_ticket, ensure_ascii=False)}\n"
            f"DiagnosisContext: {json.dumps(compact_context, ensure_ascii=False)}\n"
            "primary_knowledge_type, secondary_knowledge_types, and candidate_patch_types must use only: "
            "cypher_syntax, few_shot, system_prompt, business_knowledge."
        )
        started = time.monotonic()
        compact_ticket_chars = len(json.dumps(compact_ticket, ensure_ascii=False, default=str))
        compact_context_chars = len(json.dumps(compact_context, ensure_ascii=False, default=str))
        logger.warning(
            "llm_call_started target=%s qa_id=%s ticket_id=%s model=%s base_url=%s context_chars=%s compact_ticket_chars=%s",
            "repair.krss_diagnosis",
            ticket.id if ticket is not None else str(context.get("id") or "unknown"),
            ticket.ticket_id if ticket is not None else str(context.get("ticket_id") or "diagnosis-context"),
            self.model,
            self.base_url,
            compact_context_chars,
            compact_ticket_chars,
            extra={
                "target": "repair.krss_diagnosis",
                "qa_id": ticket.id if ticket is not None else str(context.get("id") or "unknown"),
                "ticket_id": ticket.ticket_id if ticket is not None else str(context.get("ticket_id") or "diagnosis-context"),
                "model": self.model,
                "base_url": self.base_url,
                "context_chars": compact_context_chars,
                "compact_ticket_chars": compact_ticket_chars,
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
                                "response_format": {"type": "json_object"},
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
                                "llm_call_retry target=%s qa_id=%s ticket_id=%s model=%s base_url=%s attempt=%s elapsed_ms=%s retry_reason=%s status_code=%s retry_delay_seconds=%s body_preview=%s",
                                "repair.krss_diagnosis",
                                ticket.id if ticket is not None else str(context.get("id") or "unknown"),
                                ticket.ticket_id if ticket is not None else str(context.get("ticket_id") or "diagnosis-context"),
                                self.model,
                                self.base_url,
                                attempt + 1,
                                elapsed_ms,
                                retry.reason,
                                retry.status_code,
                                delay_seconds,
                                retry.body_preview,
                                extra={
                                    "target": "repair.krss_diagnosis",
                                    "qa_id": ticket.id if ticket is not None else str(context.get("id") or "unknown"),
                                    "ticket_id": ticket.ticket_id if ticket is not None else str(context.get("ticket_id") or "diagnosis-context"),
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
                            "llm_call_failed target=%s qa_id=%s ticket_id=%s model=%s base_url=%s elapsed_ms=%s attempts=%s retry_reason=%s status_code=%s body_preview=%s error=%s",
                            "repair.krss_diagnosis",
                            ticket.id if ticket is not None else str(context.get("id") or "unknown"),
                            ticket.ticket_id if ticket is not None else str(context.get("ticket_id") or "diagnosis-context"),
                            self.model,
                            self.base_url,
                            elapsed_ms,
                            attempt + 1,
                            retry.reason,
                            retry.status_code,
                            retry.body_preview,
                            str(exc),
                            extra={
                                "target": "repair.krss_diagnosis",
                                "qa_id": ticket.id if ticket is not None else str(context.get("id") or "unknown"),
                                "ticket_id": ticket.ticket_id if ticket is not None else str(context.get("ticket_id") or "diagnosis-context"),
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

        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("KRSS diagnosis response must be a JSON object")
        if "primary_knowledge_type" not in parsed and parsed.get("knowledge_types"):
            knowledge_types = parsed.get("knowledge_types") or []
            primary = knowledge_types[0] if knowledge_types else "system_prompt"
            secondary = knowledge_types[1:3] if isinstance(knowledge_types, list) else []
            parsed["primary_knowledge_type"] = primary
            parsed["secondary_knowledge_types"] = secondary
        if "need_validation" not in parsed:
            parsed["need_validation"] = parsed.get("need_experiments", False)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        request_id = extract_request_id(getattr(response, "headers", None))
        logger.warning(
            "llm_call_succeeded target=%s qa_id=%s ticket_id=%s model=%s base_url=%s elapsed_ms=%s request_id=%s",
            "repair.krss_diagnosis",
            ticket.id if ticket is not None else str(context.get("id") or "unknown"),
            ticket.ticket_id if ticket is not None else str(context.get("ticket_id") or "diagnosis-context"),
            self.model,
            self.base_url,
            elapsed_ms,
            request_id,
            extra={
                "target": "repair.krss_diagnosis",
                "qa_id": ticket.id if ticket is not None else str(context.get("id") or "unknown"),
                "ticket_id": ticket.ticket_id if ticket is not None else str(context.get("ticket_id") or "diagnosis-context"),
                "model": self.model,
                "base_url": self.base_url,
                "elapsed_ms": elapsed_ms,
                "request_id": request_id,
            },
        )
        return parsed

class CGSPromptSnapshotClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def fetch(self, id: str) -> PromptSnapshotResponse:
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.get(f"{self.base_url}/api/v1/questions/{id}/prompt")
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.warning(
                    "outbound_call_failed",
                    extra={
                        "target": "cgs.prompt_snapshot",
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
                    "target": "cgs.prompt_snapshot",
                    "qa_id": id,
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                },
            )
            return PromptSnapshotResponse.model_validate(response.json())


class KnowledgeOpsRepairApplyClient:
    def __init__(
        self,
        apply_url: str,
        timeout_seconds: float,
        capture_dir: Optional[str] = None,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retry_delay_seconds: float = 0.1,
    ) -> None:
        self.apply_url = apply_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.capture_dir = capture_dir
        self.sleep_fn = sleep_fn
        self.retry_delay_seconds = retry_delay_seconds

    async def apply(self, payload: KnowledgeRepairSuggestionRequest) -> Dict[str, Any] | None:
        self._capture_payload(payload)
        started = time.monotonic()
        attempts = 0
        request_payload = payload.model_dump(mode="json")
        knowledge_types = request_payload.get("knowledge_types", []) or []
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            while True:
                attempts += 1
                try:
                    response = await client.post(self.apply_url, json=request_payload)
                except httpx.RequestError:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    logger.warning(
                        "outbound_call_failed",
                        extra={
                            "target": "knowledge_ops.repairs_apply",
                            "analysis_id": payload.id,
                            "knowledge_types": knowledge_types,
                            "attempt": attempts,
                            "elapsed_ms": elapsed_ms,
                            "error": "transport_error",
                        },
                    )
                    # Transport failures are retried the same way as non-200 responses.
                    await self.sleep_fn(self.retry_delay_seconds)
                    continue
                if response.status_code == 200:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    logger.info(
                        "outbound_call_ok",
                        extra={
                            "target": "knowledge_ops.repairs_apply",
                            "analysis_id": payload.id,
                            "knowledge_types": knowledge_types,
                            "attempts": attempts,
                            "status_code": response.status_code,
                            "elapsed_ms": elapsed_ms,
                        },
                    )
                    try:
                        return response.json()
                    except Exception:
                        return {"raw": response.text}
                if 400 <= response.status_code < 500:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    logger.warning(
                        "outbound_call_failed",
                        extra={
                            "target": "knowledge_ops.repairs_apply",
                            "analysis_id": payload.id,
                            "knowledge_types": knowledge_types,
                            "attempts": attempts,
                            "status_code": response.status_code,
                            "elapsed_ms": elapsed_ms,
                            "error": "non_retryable_4xx",
                        },
                    )
                    response.raise_for_status()
                logger.warning(
                    "outbound_call_retry",
                    extra={
                        "target": "knowledge_ops.repairs_apply",
                        "analysis_id": payload.id,
                        "knowledge_types": knowledge_types,
                        "attempt": attempts,
                        "status_code": response.status_code,
                    },
                )
                await self.sleep_fn(self.retry_delay_seconds)

    def _capture_payload(self, payload: KnowledgeRepairSuggestionRequest) -> None:
        if not self.capture_dir:
            return
        try:
            capture_path = Path(self.capture_dir)
            capture_path.mkdir(parents=True, exist_ok=True)
            file_path = capture_path / f"{payload.id}.json"
            file_path.write_text(
                json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            return
