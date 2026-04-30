from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, Protocol
from uuid import uuid4

import httpx

from .clients import (
    CypherLLMClient,
    OpenAIChatCompletionCypherGenerator,
    TestingAgentClient,
)
from .config import Settings, get_settings
from .knowledge_context import FileKnowledgeContextProvider
from .models import (
    GeneratedCypherSubmissionRequest,
    GenerationFailureReason,
    GenerationRunFailureReport,
    GenerationRunResult,
    QAQuestionRequest,
)
from .outbox import DeliveryOutbox
from .parser import parse_model_output
from .preflight import run_preflight_check
from .prompt_runtime import render_llm_prompt


MAX_GENERATION_ATTEMPTS = 3


class KnowledgeContextProvider(Protocol):
    async def fetch_context(self, id: str, question: str) -> str:
        ...


class CypherModelInvoker(Protocol):
    async def generate_from_prompt(self, *, task_id: str, question_text: str, llm_prompt: str) -> Dict[str, str]:
        ...


class GeneratedCypherSubmitter(Protocol):
    async def submit(self, payload: GeneratedCypherSubmissionRequest) -> Dict[str, object]:
        ...

    async def submit_generation_failure(self, payload: GenerationRunFailureReport) -> Dict[str, object]:
        ...


class CypherGeneratorAgentService:
    def __init__(
        self,
        *,
        knowledge_context_provider: KnowledgeContextProvider,
        llm_client: CypherModelInvoker,
        testing_client: GeneratedCypherSubmitter,
        generation_run_id_factory: Callable[[], str] | None = None,
        readonly_call_whitelist: set[str] | None = None,
        delivery_outbox: DeliveryOutbox | None = None,
    ) -> None:
        self.knowledge_context_provider = knowledge_context_provider
        self.llm_client = llm_client
        self.testing_client = testing_client
        self.generation_run_id_factory = generation_run_id_factory or (lambda: str(uuid4()))
        self.readonly_call_whitelist = readonly_call_whitelist or set()
        self.delivery_outbox = delivery_outbox
        self._delivery_retry_lock = asyncio.Lock()

    async def ingest_question(self, request: QAQuestionRequest) -> GenerationRunResult:
        generation_run_id = self.generation_run_id_factory()
        try:
            ko_context = await self.knowledge_context_provider.fetch_context(id=request.id, question=request.question)
        except Exception:
            await self._submit_service_failure_report(
                request=request,
                generation_run_id=generation_run_id,
                reason="knowledge_context_unavailable",
                input_prompt_snapshot="",
                last_llm_raw_output="",
            )
            return GenerationRunResult(
                generation_run_id=generation_run_id,
                generation_status="service_failed",
                reason="knowledge_context_unavailable",
            )
        if not ko_context.strip():
            await self._submit_service_failure_report(
                request=request,
                generation_run_id=generation_run_id,
                reason="knowledge_context_unavailable",
                input_prompt_snapshot="",
                last_llm_raw_output="",
            )
            return GenerationRunResult(
                generation_run_id=generation_run_id,
                generation_status="service_failed",
                reason="knowledge_context_unavailable",
            )

        last_reason: GenerationFailureReason | None = None
        last_llm_raw_output = ""
        last_prompt_snapshot = ""
        last_parsed_cypher = ""
        generation_failure_reasons: list[GenerationFailureReason] = []
        for _generation_attempt_index in range(1, MAX_GENERATION_ATTEMPTS + 1):
            llm_prompt = render_llm_prompt(
                question=request.question,
                ko_context=ko_context,
                extra_constraint_reason=last_reason,
            )
            last_prompt_snapshot = llm_prompt
            try:
                raw_generation = await self.llm_client.generate_from_prompt(
                    task_id=request.id,
                    question_text=request.question,
                    llm_prompt=llm_prompt,
                )
            except Exception:
                await self._submit_service_failure_report(
                    request=request,
                    generation_run_id=generation_run_id,
                    reason="model_invocation_failed",
                    input_prompt_snapshot=llm_prompt,
                    last_llm_raw_output="",
                )
                return GenerationRunResult(
                    generation_run_id=generation_run_id,
                    generation_status="service_failed",
                    reason="model_invocation_failed",
                )

            raw_output_value = raw_generation.get("raw_output")
            if not isinstance(raw_output_value, str):
                await self._submit_service_failure_report(
                    request=request,
                    generation_run_id=generation_run_id,
                    reason="model_invocation_failed",
                    input_prompt_snapshot=llm_prompt,
                    last_llm_raw_output="",
                )
                return GenerationRunResult(
                    generation_run_id=generation_run_id,
                    generation_status="service_failed",
                    reason="model_invocation_failed",
                )
            raw_output = raw_output_value
            last_llm_raw_output = raw_output
            parsed = parse_model_output(raw_output)
            last_parsed_cypher = parsed.parsed_cypher
            if parsed.reason is not None:
                last_reason = parsed.reason
                generation_failure_reasons.append(parsed.reason)
                continue

            preflight_check = run_preflight_check(
                parsed.parsed_cypher,
                readonly_call_whitelist=self.readonly_call_whitelist,
            )
            if not preflight_check.accepted:
                last_reason = preflight_check.reason
                if preflight_check.reason is not None:
                    generation_failure_reasons.append(preflight_check.reason)
                continue

            submission = GeneratedCypherSubmissionRequest(
                id=request.id,
                question=request.question,
                generation_run_id=generation_run_id,
                generated_cypher=parsed.parsed_cypher,
                input_prompt_snapshot=llm_prompt,
                last_llm_raw_output=raw_output,
                generation_retry_count=len(generation_failure_reasons),
                generation_failure_reasons=generation_failure_reasons,
            )
            if not await self._deliver_or_outbox(
                payload_type="GeneratedCypherSubmissionRequest",
                payload=submission,
            ):
                return GenerationRunResult(
                    generation_run_id=generation_run_id,
                    generation_status="service_failed",
                    reason="testing_agent_submission_failed",
                )
            return GenerationRunResult(
                generation_run_id=generation_run_id,
                generation_status="submitted_to_testing",
            )

        if last_reason is not None:
            failure_report = GenerationRunFailureReport(
                id=request.id,
                question=request.question,
                generation_run_id=generation_run_id,
                generation_status="generation_failed",
                failure_reason="generation_retry_exhausted",
                last_generation_failure_reason=last_reason,
                input_prompt_snapshot=last_prompt_snapshot,
                last_llm_raw_output=last_llm_raw_output,
                generation_retry_count=max(0, len(generation_failure_reasons) - 1),
                generation_failure_reasons=generation_failure_reasons,
                parsed_cypher=last_parsed_cypher or None,
                gate_passed=False,
            )
            await self._deliver_or_outbox(
                payload_type="GenerationRunFailureReport",
                payload=failure_report,
            )

        return GenerationRunResult(
            generation_run_id=generation_run_id,
            generation_status="generation_failed",
            reason="generation_retry_exhausted",
            last_reason=last_reason,
        )

    async def retry_pending_deliveries(self) -> None:
        if self.delivery_outbox is None:
            return
        async with self._delivery_retry_lock:
            for record in self.delivery_outbox.list_retryable():
                self.delivery_outbox.mark_retrying(record["delivery_id"])
                try:
                    payload_type = record["payload_type"]
                    if payload_type == "GeneratedCypherSubmissionRequest":
                        payload = GeneratedCypherSubmissionRequest(**record["payload"])
                        result = await self.testing_client.submit(payload=payload)
                    elif payload_type == "GenerationRunFailureReport":
                        payload = GenerationRunFailureReport(**record["payload"])
                        result = await self.testing_client.submit_generation_failure(payload=payload)
                    else:
                        self.delivery_outbox.mark_dead_letter(record["delivery_id"], f"unknown payload_type: {payload_type}")
                        continue
                except FileNotFoundError:
                    continue
                except Exception as exc:
                    error = str(exc)
                    if _is_non_retryable_http_error(exc):
                        self.delivery_outbox.mark_dead_letter(record["delivery_id"], error)
                    else:
                        self.delivery_outbox.mark_pending(record["delivery_id"], error)
                    continue

                if result.get("accepted") is True:
                    self.delivery_outbox.delete(record["delivery_id"])

    async def _deliver_or_outbox(
        self,
        *,
        payload_type: str,
        payload: GeneratedCypherSubmissionRequest | GenerationRunFailureReport,
    ) -> bool:
        try:
            if payload_type == "GeneratedCypherSubmissionRequest":
                await self.testing_client.submit(payload=payload)
            else:
                await self.testing_client.submit_generation_failure(payload=payload)
            return True
        except Exception as exc:
            if self.delivery_outbox is None:
                return False
            status = "dead_letter" if _is_non_retryable_http_error(exc) else "pending"
            self.delivery_outbox.save(
                payload_type=payload_type,
                payload=payload.model_dump(),
                status=status,
                error=str(exc),
            )
            if status == "pending":
                asyncio.create_task(self.retry_pending_deliveries())
            return False

    async def _submit_service_failure_report(
        self,
        *,
        request: QAQuestionRequest,
        generation_run_id: str,
        reason: str,
        input_prompt_snapshot: str,
        last_llm_raw_output: str,
    ) -> None:
        failure_report = GenerationRunFailureReport(
            id=request.id,
            question=request.question,
            generation_run_id=generation_run_id,
            input_prompt_snapshot=input_prompt_snapshot,
            last_llm_raw_output=last_llm_raw_output,
            generation_status="service_failed",
            failure_reason=reason,
            generation_retry_count=0,
            generation_failure_reasons=[],
            gate_passed=False,
        )
        await self._deliver_or_outbox(
            payload_type="GenerationRunFailureReport",
            payload=failure_report,
        )


def build_workflow_service(settings: Settings) -> CypherGeneratorAgentService:
    outbox_dir = settings.delivery_outbox_dir or str(Path(settings.data_dir) / "delivery_outbox")
    return CypherGeneratorAgentService(
        knowledge_context_provider=FileKnowledgeContextProvider(knowledge_dir=settings.knowledge_docs_dir),
        llm_client=CypherLLMClient(
            llm_generator=OpenAIChatCompletionCypherGenerator(
                base_url=settings.llm_base_url or "",
                api_key=settings.llm_api_key or "",
                model=settings.llm_model or "",
                timeout_seconds=settings.request_timeout_seconds,
                temperature=settings.llm_temperature,
            ),
        ),
        testing_client=TestingAgentClient(
            base_url=settings.testing_agent_url,
            timeout_seconds=settings.request_timeout_seconds,
        ),
        readonly_call_whitelist=set(settings.readonly_call_whitelist),
        delivery_outbox=DeliveryOutbox(outbox_dir),
    )


@lru_cache(maxsize=1)
def get_workflow_service() -> CypherGeneratorAgentService:
    return build_workflow_service(get_settings())


def get_generator_status() -> Dict[str, object]:
    settings = get_settings()
    knowledge_context_provider = FileKnowledgeContextProvider(knowledge_dir=settings.knowledge_docs_dir)
    return {
        "llm_enabled": settings.llm_enabled,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "active_mode": "llm" if settings.llm_enabled else "disabled",
        "knowledge_context_source": "file",
        "knowledge_docs_dir_configured": knowledge_context_provider.is_available(),
        "testing_agent_configured": bool(settings.testing_agent_url),
    }


def _is_non_retryable_http_error(exc: Exception) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    status_code = exc.response.status_code
    return 400 <= status_code < 500
