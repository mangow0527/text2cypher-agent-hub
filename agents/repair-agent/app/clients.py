from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx

from .llm_retry import classify_retryable_error, extract_request_id, sleep_with_backoff
from .models import IssueTicket, KnowledgeRepairSuggestionRequest
from .prompting import build_repair_diagnosis_prompt, compact_diagnosis_context
from services.repair_agent.app.analysis import build_diagnosis_context

logger = logging.getLogger("repair_service")


class OpenAIChatCompletionRepairAnalyzer:
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
        compact_context = compact_diagnosis_context(context)
        system_prompt, user_prompt = build_repair_diagnosis_prompt(context, ticket=ticket)
        started = time.monotonic()
        compact_ticket_chars = user_prompt.find("\nDiagnosisContext:")
        compact_context_chars = len(json.dumps(compact_context, ensure_ascii=False, default=str))
        logger.warning(
            "llm_call_started target=%s qa_id=%s ticket_id=%s model=%s base_url=%s context_chars=%s compact_ticket_chars=%s",
            "repair.diagnosis",
            ticket.id if ticket is not None else str(context.get("id") or "unknown"),
            ticket.ticket_id if ticket is not None else str(context.get("ticket_id") or "diagnosis-context"),
            self.model,
            self.base_url,
            compact_context_chars,
            compact_ticket_chars,
            extra={
                "target": "repair.diagnosis",
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
                        payload["_system_prompt"] = system_prompt
                        payload["_user_prompt"] = user_prompt
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
                                "repair.diagnosis",
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
                                    "target": "repair.diagnosis",
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
                            "repair.diagnosis",
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
                                "target": "repair.diagnosis",
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

        raw_content = payload["choices"][0]["message"]["content"].strip()
        content = raw_content
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:].strip()

        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("repair diagnosis response must be a JSON object")
        parsed["_system_prompt"] = system_prompt
        parsed["_user_prompt"] = user_prompt
        parsed["_raw_output"] = raw_content
        required_fields = {
            "repairable",
            "non_repairable_reason",
            "primary_knowledge_type",
            "secondary_knowledge_types",
            "confidence",
            "suggestion",
            "rationale",
        }
        missing_fields = sorted(required_fields - set(parsed))
        if missing_fields:
            raise ValueError(f"missing required diagnosis fields: {', '.join(missing_fields)}")
        _validate_chinese_diagnosis_text(parsed)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        request_id = extract_request_id(getattr(response, "headers", None))
        logger.warning(
            "llm_call_succeeded target=%s qa_id=%s ticket_id=%s model=%s base_url=%s elapsed_ms=%s request_id=%s",
            "repair.diagnosis",
            ticket.id if ticket is not None else str(context.get("id") or "unknown"),
            ticket.ticket_id if ticket is not None else str(context.get("ticket_id") or "diagnosis-context"),
            self.model,
            self.base_url,
            elapsed_ms,
            request_id,
            extra={
                "target": "repair.diagnosis",
                "qa_id": ticket.id if ticket is not None else str(context.get("id") or "unknown"),
                "ticket_id": ticket.ticket_id if ticket is not None else str(context.get("ticket_id") or "diagnosis-context"),
                "model": self.model,
                "base_url": self.base_url,
                "elapsed_ms": elapsed_ms,
                "request_id": request_id,
            },
        )
        return parsed


def _validate_chinese_diagnosis_text(parsed: Dict[str, Any]) -> None:
    text_fields = ["suggestion", "rationale"]
    if str(parsed.get("non_repairable_reason") or "").strip():
        text_fields.append("non_repairable_reason")
    for field in text_fields:
        value = parsed.get(field)
        if not isinstance(value, str) or not _contains_chinese(value):
            raise ValueError(f"repair diagnosis response must contain Chinese diagnosis text in {field}")


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


class KnowledgeAgentRepairApplyClient:
    def __init__(
        self,
        apply_url: str,
        timeout_seconds: float,
        capture_dir: Optional[str] = None,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retry_delay_seconds: float = 0.1,
        max_attempts: int = 5,
    ) -> None:
        self.apply_url = apply_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.capture_dir = capture_dir
        self.sleep_fn = sleep_fn
        self.retry_delay_seconds = retry_delay_seconds
        self.max_attempts = max(1, max_attempts)

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
                except httpx.RequestError as exc:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    logger.warning(
                        "outbound_call_failed",
                        extra={
                            "target": "knowledge_agent.repairs_apply",
                            "analysis_id": payload.id,
                            "knowledge_types": knowledge_types,
                            "attempt": attempts,
                            "elapsed_ms": elapsed_ms,
                            "error": "transport_error",
                        },
                    )
                    if attempts >= self.max_attempts:
                        raise RuntimeError(f"knowledge-agent repair apply failed after {attempts} attempts: transport_error") from exc
                    await self.sleep_fn(self.retry_delay_seconds)
                    continue
                if response.status_code == 200:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    logger.info(
                        "outbound_call_ok",
                        extra={
                            "target": "knowledge_agent.repairs_apply",
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
                paused_response = _knowledge_repair_apply_paused_response(response)
                if paused_response is not None:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    logger.warning(
                        "outbound_call_paused",
                        extra={
                            "target": "knowledge_agent.repairs_apply",
                            "analysis_id": payload.id,
                            "knowledge_types": knowledge_types,
                            "attempts": attempts,
                            "status_code": response.status_code,
                            "elapsed_ms": elapsed_ms,
                            "code": paused_response["code"],
                        },
                    )
                    return paused_response
                if 400 <= response.status_code < 500:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    logger.warning(
                        "outbound_call_failed",
                        extra={
                            "target": "knowledge_agent.repairs_apply",
                            "analysis_id": payload.id,
                            "knowledge_types": knowledge_types,
                            "attempts": attempts,
                            "status_code": response.status_code,
                            "elapsed_ms": elapsed_ms,
                            "error": "non_retryable_4xx",
                        },
                    )
                    response.raise_for_status()
                if attempts >= self.max_attempts:
                    raise RuntimeError(
                        f"knowledge-agent repair apply failed after {attempts} attempts: HTTP {response.status_code}"
                    )
                logger.warning(
                    "outbound_call_retry",
                    extra={
                        "target": "knowledge_agent.repairs_apply",
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


def _knowledge_repair_apply_paused_response(response: httpx.Response) -> Dict[str, Any] | None:
    try:
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("code") != "KNOWLEDGE_REPAIR_APPLY_DISABLED":
        return None
    message = payload.get("message")
    return {
        "status": "paused",
        "code": "KNOWLEDGE_REPAIR_APPLY_DISABLED",
        "message": message if isinstance(message, str) and message else "Knowledge repair apply is disabled.",
    }
