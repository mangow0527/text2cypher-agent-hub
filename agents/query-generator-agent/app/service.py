from __future__ import annotations

import json
from functools import lru_cache
from typing import Dict, Tuple

from .models import (
    EvaluationSubmissionRequest,
    PromptSnapshotResponse,
    QAQuestionRequest,
    QueryGeneratorRepairReceipt,
    QueryQuestionResponse,
    RepairPlan,
)

from .clients import (
    OpenAICompatibleCypherGenerator,
    PromptServiceClient,
    QwenGeneratorClient,
    TestingServiceClient,
)
from .config import Settings, get_settings
from .repository import QueryGeneratorRepository


class QueryWorkflowService:
    def __init__(
        self,
        prompt_client: PromptServiceClient,
        generator_client: QwenGeneratorClient,
        testing_client: TestingServiceClient,
        repository: QueryGeneratorRepository,
    ) -> None:
        self.prompt_client = prompt_client
        self.generator_client = generator_client
        self.testing_client = testing_client
        self.repository = repository

    async def ingest_question(self, request: QAQuestionRequest) -> QueryQuestionResponse:
        generation_run_id = self.repository.next_generation_run_id()
        attempt_no = self.repository.next_attempt_no(request.id)
        self.repository.upsert_question(id=request.id, question=request.question, status="received")

        prompt_response, generation_prompt = await self._fetch_prompt(
            request=request,
            generation_run_id=generation_run_id,
            attempt_no=attempt_no,
        )
        if prompt_response is not None:
            return self._persist_and_return(request=request, response=prompt_response)

        readiness_response = self._validate_prompt_readiness(
            request=request,
            generation_run_id=generation_run_id,
            attempt_no=attempt_no,
            generation_prompt=generation_prompt,
        )
        if readiness_response is not None:
            return self._persist_and_return(request=request, response=readiness_response)

        self.repository.update_question_status(request.id, "prompt_ready")

        invocation_response, raw_output = await self._invoke_model(
            request=request,
            generation_run_id=generation_run_id,
            attempt_no=attempt_no,
            generation_prompt=generation_prompt,
        )
        if invocation_response is not None:
            return self._persist_and_return(request=request, response=invocation_response)

        parsing_response, generated_cypher, parse_summary = self._parse_generation_output(
            request=request,
            generation_run_id=generation_run_id,
            attempt_no=attempt_no,
            generation_prompt=generation_prompt,
            raw_output=raw_output,
        )
        if parsing_response is not None:
            return self._persist_and_return(request=request, response=parsing_response)

        guardrail_response, guardrail_summary = self._check_guardrail(
            request=request,
            generation_run_id=generation_run_id,
            attempt_no=attempt_no,
            generation_prompt=generation_prompt,
            generated_cypher=generated_cypher,
            parse_summary=parse_summary,
            raw_output=raw_output,
        )
        if guardrail_response is not None:
            return self._persist_and_return(request=request, response=guardrail_response)

        self.repository.save_generation_run(
            id=request.id,
            generation_run_id=generation_run_id,
            attempt_no=attempt_no,
            generation_status="generated",
            generated_cypher=generated_cypher,
            parse_summary=parse_summary,
            guardrail_summary=guardrail_summary,
            raw_output_snapshot=raw_output,
            failure_stage=None,
            failure_reason_summary=None,
            input_prompt_snapshot=generation_prompt,
        )

        await self.testing_client.submit(
            payload=EvaluationSubmissionRequest(
                id=request.id,
                question=request.question,
                generation_run_id=generation_run_id,
                attempt_no=attempt_no,
                generated_cypher=generated_cypher,
                parse_summary=parse_summary,
                guardrail_summary=guardrail_summary,
                raw_output_snapshot=raw_output,
                input_prompt_snapshot=generation_prompt,
            )
        )

        response = self._build_response(
            id=request.id,
            generation_run_id=generation_run_id,
            attempt_no=attempt_no,
            generation_status="submitted_to_testing",
            generated_cypher=generated_cypher,
            parse_summary=parse_summary,
            guardrail_summary=guardrail_summary,
            raw_output_snapshot=raw_output,
            input_prompt_snapshot=generation_prompt,
        )
        return self._persist_and_return(request=request, response=response)

    def get_run(self, id: str) -> QueryQuestionResponse | None:
        return self.repository.get_generation_run(id)

    def get_prompt_snapshot(self, id: str) -> PromptSnapshotResponse | None:
        snapshot = self.repository.get_generation_prompt_snapshot(id)
        if snapshot is None:
            return None
        return PromptSnapshotResponse.model_validate(snapshot)

    def accept_repair_plan(self, plan: RepairPlan) -> QueryGeneratorRepairReceipt:
        self.repository.save_repair_plan_receipt(plan)
        return QueryGeneratorRepairReceipt(status="accepted", plan_id=plan.plan_id, id=plan.id)

    async def _fetch_prompt(
        self,
        *,
        request: QAQuestionRequest,
        generation_run_id: str,
        attempt_no: int,
    ) -> Tuple[QueryQuestionResponse | None, str]:
        try:
            prompt = await self.prompt_client.fetch_prompt(id=request.id, question=request.question)
        except Exception as exc:
            return (
                self._build_response(
                    id=request.id,
                    generation_run_id=generation_run_id,
                    attempt_no=attempt_no,
                    generation_status="prompt_fetch_failed",
                    generated_cypher="",
                    parse_summary="prompt_not_fetched",
                    guardrail_summary="prompt_fetch_failed",
                    raw_output_snapshot="",
                    failure_stage="prompt_fetch",
                    failure_reason_summary=str(exc),
                    input_prompt_snapshot="",
                ),
                "",
            )
        return None, prompt

    def _validate_prompt_readiness(
        self,
        *,
        request: QAQuestionRequest,
        generation_run_id: str,
        attempt_no: int,
        generation_prompt: str,
    ) -> QueryQuestionResponse | None:
        if generation_prompt.strip():
            return None
        return self._build_response(
            id=request.id,
            generation_run_id=generation_run_id,
            attempt_no=attempt_no,
            generation_status="failed",
            generated_cypher="",
            parse_summary="prompt_empty",
            guardrail_summary="prompt_not_ready",
            raw_output_snapshot="",
            failure_stage="prompt_readiness_check",
            failure_reason_summary="Generation prompt is empty.",
            input_prompt_snapshot=generation_prompt,
        )

    async def _invoke_model(
        self,
        *,
        request: QAQuestionRequest,
        generation_run_id: str,
        attempt_no: int,
        generation_prompt: str,
    ) -> Tuple[QueryQuestionResponse | None, str]:
        try:
            raw_generation = await self.generator_client.generate_from_prompt(
                task_id=request.id,
                question_text=request.question,
                generation_prompt=generation_prompt,
            )
        except Exception as exc:
            return (
                self._build_response(
                    id=request.id,
                    generation_run_id=generation_run_id,
                    attempt_no=attempt_no,
                    generation_status="model_invocation_failed",
                    generated_cypher="",
                    parse_summary="model_invocation_failed",
                    guardrail_summary="not_checked",
                    raw_output_snapshot="",
                    failure_stage="model_invocation",
                    failure_reason_summary=str(exc),
                    input_prompt_snapshot=generation_prompt,
                ),
                "",
            )
        return None, raw_generation.get("raw_output", "")

    def _parse_generation_output(
        self,
        *,
        request: QAQuestionRequest,
        generation_run_id: str,
        attempt_no: int,
        generation_prompt: str,
        raw_output: str,
    ) -> Tuple[QueryQuestionResponse | None, str, str]:
        generated_cypher, parse_summary = _extract_cypher(raw_output)
        if generated_cypher:
            return None, generated_cypher, parse_summary
        return (
            self._build_response(
                id=request.id,
                generation_run_id=generation_run_id,
                attempt_no=attempt_no,
                generation_status="output_parsing_failed",
                generated_cypher="",
                parse_summary=parse_summary,
                guardrail_summary="not_checked",
                raw_output_snapshot=raw_output,
                failure_stage="output_parsing",
                failure_reason_summary="Unable to parse Cypher from model output.",
                input_prompt_snapshot=generation_prompt,
            ),
            "",
            parse_summary,
        )

    def _check_guardrail(
        self,
        *,
        request: QAQuestionRequest,
        generation_run_id: str,
        attempt_no: int,
        generation_prompt: str,
        generated_cypher: str,
        parse_summary: str,
        raw_output: str,
    ) -> Tuple[QueryQuestionResponse | None, str]:
        guardrail_summary, guardrail_error = _run_minimal_guardrail(generated_cypher)
        if guardrail_error is None:
            return None, guardrail_summary
        return (
            self._build_response(
                id=request.id,
                generation_run_id=generation_run_id,
                attempt_no=attempt_no,
                generation_status="guardrail_rejected",
                generated_cypher=generated_cypher,
                parse_summary=parse_summary,
                guardrail_summary=guardrail_summary,
                raw_output_snapshot=raw_output,
                failure_stage="guardrail_check",
                failure_reason_summary=guardrail_error,
                input_prompt_snapshot=generation_prompt,
            ),
            guardrail_summary,
        )

    def _build_response(
        self,
        *,
        id: str,
        generation_run_id: str,
        attempt_no: int,
        generation_status: str,
        generated_cypher: str,
        parse_summary: str,
        guardrail_summary: str,
        raw_output_snapshot: str,
        failure_stage: str | None = None,
        failure_reason_summary: str | None = None,
        input_prompt_snapshot: str,
    ) -> QueryQuestionResponse:
        return QueryQuestionResponse(
            id=id,
            generation_run_id=generation_run_id,
            attempt_no=attempt_no,
            generation_status=generation_status,
            generated_cypher=generated_cypher,
            parse_summary=parse_summary,
            guardrail_summary=guardrail_summary,
            raw_output_snapshot=raw_output_snapshot,
            failure_stage=failure_stage,
            failure_reason_summary=failure_reason_summary,
            input_prompt_snapshot=input_prompt_snapshot,
        )

    def _persist_and_return(self, *, request: QAQuestionRequest, response: QueryQuestionResponse) -> QueryQuestionResponse:
        self._persist(request, response)
        return response

    def _persist(self, request: QAQuestionRequest, response: QueryQuestionResponse) -> None:
        self.repository.update_question_status(request.id, response.generation_status)
        self.repository.save_generation_run(
            id=request.id,
            generation_run_id=response.generation_run_id,
            attempt_no=response.attempt_no,
            generation_status=response.generation_status,
            generated_cypher=response.generated_cypher,
            parse_summary=response.parse_summary,
            guardrail_summary=response.guardrail_summary,
            raw_output_snapshot=response.raw_output_snapshot,
            failure_stage=response.failure_stage,
            failure_reason_summary=response.failure_reason_summary,
            input_prompt_snapshot=response.input_prompt_snapshot,
        )


def _extract_cypher(content: str) -> tuple[str, str]:
    cleaned = content.strip()
    if not cleaned:
        return "", "raw_output_empty"

    fenced = _strip_fence(cleaned)
    if fenced != cleaned:
        cleaned = fenced

    try:
        parsed = json.loads(cleaned)
        cypher = str(parsed.get("cypher", "")).strip()
        if cypher:
            return cypher, "parsed_json"
    except json.JSONDecodeError:
        pass

    if cleaned.upper().startswith(("MATCH", "WITH", "CALL")):
        return cleaned, "parsed_plain_text"

    for line in cleaned.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(("MATCH", "WITH", "CALL")):
            return stripped, "parsed_first_query_line"

    return "", "parse_failed"


def _strip_fence(content: str) -> str:
    if not content.startswith("```"):
        return content
    stripped = content.strip("`")
    if stripped.startswith("json"):
        return stripped[4:].strip()
    if stripped.startswith("cypher"):
        return stripped[6:].strip()
    return stripped.strip()


def _run_minimal_guardrail(generated_cypher: str) -> tuple[str, str | None]:
    if not generated_cypher.strip():
        return "empty_cypher", "Generated Cypher is empty."
    if not generated_cypher.lstrip().upper().startswith(("MATCH", "WITH", "CALL")):
        return "invalid_cypher_start", "Generated Cypher does not start with a supported clause."
    return "accepted", None


def build_workflow_service(settings: Settings) -> QueryWorkflowService:
    return QueryWorkflowService(
        prompt_client=PromptServiceClient(
            base_url=settings.knowledge_ops_service_url,
            timeout_seconds=settings.request_timeout_seconds,
        ),
        generator_client=QwenGeneratorClient(
            llm_generator=OpenAICompatibleCypherGenerator(
                base_url=settings.llm_base_url or "",
                api_key=settings.llm_api_key or "",
                model=settings.llm_model or "",
                timeout_seconds=settings.request_timeout_seconds,
                temperature=settings.llm_temperature,
            ),
        ),
        testing_client=TestingServiceClient(
            base_url=settings.testing_service_url,
            timeout_seconds=settings.request_timeout_seconds,
        ),
        repository=QueryGeneratorRepository(data_dir=settings.data_dir),
    )


@lru_cache(maxsize=1)
def get_workflow_service() -> QueryWorkflowService:
    return build_workflow_service(get_settings())


async def test_tugraph_connection() -> dict:
    return {
        "supported": False,
        "detail": "Cypher Generation Service no longer executes TuGraph queries directly.",
    }


def get_generator_status() -> Dict[str, object]:
    settings = get_settings()
    return {
        "llm_enabled": settings.llm_enabled,
        "llm_provider": settings.llm_provider,
        "llm_base_url": settings.llm_base_url,
        "llm_model": settings.llm_model,
        "llm_configured": True,
        "active_mode": "llm",
        "storage": settings.data_dir,
        "prompt_source": settings.knowledge_ops_service_url,
    }
