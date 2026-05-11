from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Protocol
from uuid import uuid4

import httpx

from .clients import (
    CypherLLMClient,
    OpenAIChatCompletionCypherGenerator,
    TestingAgentClient,
)
from .config import Settings, get_settings
from .knowledge_context import KnowledgeDocsValidator
from .knowledge_selection import RagKnowledgeSelector
from .models import (
    CgaGenerationNonSuccessReport,
    GeneratedCypherSubmissionRequest,
    GenerationFailureReason,
    GenerationRunResult,
    QAQuestionRequest,
)
from .outbox import DeliveryOutbox
from .semantic_alignment import SemanticAlignmentReport, validate_default_semantic_alignment
from .semantic_pipeline import SemanticPipeline, get_semantic_pipeline


class GeneratedCypherSubmitter(Protocol):
    async def submit(self, payload: GeneratedCypherSubmissionRequest) -> Dict[str, object]:
        ...

    async def submit_generation_failure(self, payload: CgaGenerationNonSuccessReport) -> Dict[str, object]:
        ...


class SemanticPipelineRunner(Protocol):
    async def parse_with_fallback(
        self,
        *,
        id: str | None = None,
        question: str,
        generation_run_id: str | None = None,
    ) -> Any:
        ...


class CypherGeneratorAgentService:
    def __init__(
        self,
        *,
        testing_client: GeneratedCypherSubmitter,
        generation_run_id_factory: Callable[[], str] | None = None,
        delivery_outbox: DeliveryOutbox | None = None,
        semantic_alignment_report_factory: Callable[[], SemanticAlignmentReport] | None = None,
        semantic_pipeline: SemanticPipelineRunner | None = None,
    ) -> None:
        self.testing_client = testing_client
        self.generation_run_id_factory = generation_run_id_factory or (lambda: str(uuid4()))
        self.delivery_outbox = delivery_outbox
        self.semantic_alignment_report_factory = semantic_alignment_report_factory
        self.semantic_pipeline = semantic_pipeline or get_semantic_pipeline()
        self._delivery_retry_lock = asyncio.Lock()

    async def ingest_question(self, request: QAQuestionRequest) -> GenerationRunResult:
        generation_run_id = self.generation_run_id_factory()
        if self.semantic_alignment_report_factory is not None:
            alignment_report = self.semantic_alignment_report_factory()
            if _alignment_report_blocks_generation(alignment_report):
                await self._submit_service_failure_report(
                    request=request,
                    generation_run_id=generation_run_id,
                    reason="semantic_contract_unaligned",
                    input_prompt_snapshot=json.dumps(alignment_report.to_dict(), ensure_ascii=False, indent=2),
                )
                return GenerationRunResult(
                    generation_run_id=generation_run_id,
                    generation_status="service_failed",
                    reason="semantic_contract_unaligned",
                )
            if _alignment_report_has_knowledge_context_unavailable(alignment_report):
                await self._submit_service_failure_report(
                    request=request,
                    generation_run_id=generation_run_id,
                    reason="knowledge_context_unavailable",
                    input_prompt_snapshot=json.dumps(alignment_report.to_dict(), ensure_ascii=False, indent=2),
                )
                return GenerationRunResult(
                    generation_run_id=generation_run_id,
                    generation_status="service_failed",
                    reason="knowledge_context_unavailable",
                )
        return await self._ingest_question_with_semantic_pipeline(
            request=request,
            generation_run_id=generation_run_id,
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
                    elif payload_type == "CgaGenerationNonSuccessReport":
                        payload = CgaGenerationNonSuccessReport(**record["payload"])
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
        payload: GeneratedCypherSubmissionRequest | CgaGenerationNonSuccessReport,
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
    ) -> None:
        failure_report = CgaGenerationNonSuccessReport(
            id=request.id,
            question=request.question,
            generation_run_id=generation_run_id,
            generation_status="service_failed",
            input_prompt_snapshot=input_prompt_snapshot,
            failure_reason=reason,
            gate_passed=False,
        )
        await self._deliver_or_outbox(
            payload_type="CgaGenerationNonSuccessReport",
            payload=failure_report,
        )

    async def _ingest_question_with_semantic_pipeline(
        self,
        *,
        request: QAQuestionRequest,
        generation_run_id: str,
    ) -> GenerationRunResult:
        try:
            semantic_result = await self.semantic_pipeline.parse_with_fallback(
                id=request.id,
                question=request.question,
                generation_run_id=generation_run_id,
            )
        except Exception:
            await self._submit_service_failure_report(
                request=request,
                generation_run_id=generation_run_id,
                reason="model_invocation_failed",
                input_prompt_snapshot="",
            )
            return GenerationRunResult(
                generation_run_id=generation_run_id,
                generation_status="service_failed",
                reason="model_invocation_failed",
            )

        snapshot = _semantic_result_snapshot(semantic_result)
        generated_cypher = getattr(semantic_result, "generated_cypher", None)
        preflight = getattr(semantic_result, "preflight", None)
        if isinstance(generated_cypher, str) and generated_cypher.strip() and getattr(preflight, "accepted", False):
            submission = GeneratedCypherSubmissionRequest(
                id=request.id,
                question=request.question,
                generation_run_id=generation_run_id,
                generated_cypher=generated_cypher,
                input_prompt_snapshot=snapshot,
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

        failure_reason = _semantic_generation_failure_reason(semantic_result)
        failure_report = CgaGenerationNonSuccessReport(
            id=request.id,
            question=request.question,
            generation_run_id=generation_run_id,
            generation_status="generation_failed",
            failure_reason=failure_reason,
            input_prompt_snapshot=snapshot,
            parsed_cypher=generated_cypher if isinstance(generated_cypher, str) and generated_cypher.strip() else None,
            gate_passed=False,
        )
        if not await self._deliver_or_outbox(
            payload_type="CgaGenerationNonSuccessReport",
            payload=failure_report,
        ):
            return GenerationRunResult(
                generation_run_id=generation_run_id,
                generation_status="service_failed",
                reason="testing_agent_submission_failed",
            )
        return GenerationRunResult(
            generation_run_id=generation_run_id,
            generation_status="generation_failed",
            reason=failure_reason,
        )


def build_workflow_service(settings: Settings) -> CypherGeneratorAgentService:
    outbox_dir = settings.delivery_outbox_dir or str(Path(settings.data_dir) / "delivery_outbox")
    return CypherGeneratorAgentService(
        testing_client=TestingAgentClient(
            base_url=settings.testing_agent_url,
            timeout_seconds=settings.request_timeout_seconds,
        ),
        delivery_outbox=DeliveryOutbox(outbox_dir),
        semantic_alignment_report_factory=lambda: validate_default_semantic_alignment(
            knowledge_dir=settings.knowledge_docs_dir
        ),
        semantic_pipeline=SemanticPipeline(
            llm_client=CypherLLMClient(
                llm_generator=OpenAIChatCompletionCypherGenerator(
                    base_url=settings.llm_base_url or "",
                    api_key=settings.llm_api_key or "",
                    model=settings.llm_model or "",
                    timeout_seconds=settings.request_timeout_seconds,
                    temperature=settings.llm_temperature,
                ),
            ),
            knowledge_selector=_build_knowledge_selector(settings),
        ),
    )


@lru_cache(maxsize=1)
def get_workflow_service() -> CypherGeneratorAgentService:
    return build_workflow_service(get_settings())


def get_generator_status() -> Dict[str, object]:
    settings = get_settings()
    knowledge_docs_validator = KnowledgeDocsValidator(knowledge_dir=settings.knowledge_docs_dir)
    semantic_alignment = validate_default_semantic_alignment(knowledge_dir=settings.knowledge_docs_dir)
    knowledge_context_source = settings.knowledge_context_source.lower()
    return {
        "llm_enabled": settings.llm_enabled,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "active_mode": "semantic_pipeline" if settings.llm_enabled else "disabled",
        "knowledge_context_source": knowledge_context_source,
        "knowledge_docs_dir_configured": knowledge_docs_validator.is_available(),
        "knowledge_selection_configured": knowledge_context_source == "rag",
        "rag_service_url": settings.rag_service_url if knowledge_context_source == "rag" else None,
        "semantic_alignment": semantic_alignment.to_dict(),
        "testing_agent_configured": bool(settings.testing_agent_url),
    }


def _build_knowledge_selector(settings: Settings) -> RagKnowledgeSelector | None:
    if settings.knowledge_context_source.lower() != "rag":
        return None
    return RagKnowledgeSelector(
        base_url=settings.rag_service_url,
        limit=settings.rag_retrieval_limit,
        timeout_seconds=settings.rag_request_timeout_seconds,
    )


def _is_non_retryable_http_error(exc: Exception) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    status_code = exc.response.status_code
    return 400 <= status_code < 500


def _alignment_report_blocks_generation(report: SemanticAlignmentReport) -> bool:
    if report.accepted:
        return False
    context_unavailable_codes = {"knowledge_context_unavailable"}
    return any(diagnostic.code not in context_unavailable_codes for diagnostic in report.diagnostics)


def _alignment_report_has_knowledge_context_unavailable(report: SemanticAlignmentReport) -> bool:
    return any(diagnostic.code == "knowledge_context_unavailable" for diagnostic in report.diagnostics)


def _semantic_result_snapshot(semantic_result: object) -> str:
    to_dict = getattr(semantic_result, "to_dict", None)
    payload = to_dict() if callable(to_dict) else semantic_result
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _semantic_generation_failure_reason(semantic_result: object) -> GenerationFailureReason:
    preflight = getattr(semantic_result, "preflight", None)
    reason = getattr(preflight, "reason", None)
    if reason in set(GenerationFailureReason.__args__):
        return reason
    return "semantic_match_rejected"
