from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from typing import Dict, Optional, Protocol
from uuid import uuid4

from .models import (
    CgaGenerationNonSuccessReport,
    CgaQuestionReceivedReport,
    GeneratedCypherSubmissionRequest,
    GenerationRunResult,
    QAQuestionRequest,
)
from services.cypher_generator_agent.app.core.pipeline import run_pipeline
from services.cypher_generator_agent.app.core.result import GenerationOutput
from services.cypher_generator_agent.app.infrastructure.clients import TestingAgentClient
from services.cypher_generator_agent.app.infrastructure.config import get_settings
from services.cypher_generator_agent.app.observability.trace import GraphTraceRecord


GENERATED_STAGE_ORDER = [
    "graph_model_loader",
    "input_clarification_gate",
    "question_decomposer",
    "candidate_retrieval",
    "candidate_reranker",
    "literal_resolver",
    "deterministic_assembler",
    "grounded_understanding",
    "semantic_binder",
    "semantic_validator",
    "dsl_builder",
    "dsl_parser",
    "dsl_structural_coverage_gate",
    "cypher_compiler",
    "cypher_self_validation",
    "output",
]

DETERMINISTIC_GENERATED_STAGE_ORDER = [
    "graph_model_loader",
    "input_clarification_gate",
    "question_decomposer",
    "candidate_retrieval",
    "candidate_reranker",
    "literal_resolver",
    "deterministic_assembler",
    "dsl_parser",
    "dsl_structural_coverage_gate",
    "cypher_compiler",
    "cypher_self_validation",
    "output",
]


class GeneratedCypherSubmitter(Protocol):
    async def submit_question_received(self, payload: CgaQuestionReceivedReport) -> Dict[str, object]:
        ...

    async def submit(self, payload: GeneratedCypherSubmissionRequest) -> Dict[str, object]:
        ...

    async def submit_generation_failure(self, payload: CgaGenerationNonSuccessReport) -> Dict[str, object]:
        ...


class CypherGeneratorAgentService:
    def __init__(
        self,
        *,
        testing_client: GeneratedCypherSubmitter,
    ) -> None:
        self.testing_client = testing_client

    async def accept_question(self, request: QAQuestionRequest) -> GenerationRunResult:
        generation_run_id = str(uuid4())
        await self.testing_client.submit_question_received(
            CgaQuestionReceivedReport(
                id=request.id,
                question=request.question,
                generation_run_id=generation_run_id,
            )
        )
        return GenerationRunResult(
            generation_run_id=generation_run_id,
            submission_status="submitted_to_testing",
        )

    async def generate_and_submit_question(
        self,
        request: QAQuestionRequest,
        *,
        generation_run_id: str,
    ) -> GenerationRunResult:
        output = await asyncio.to_thread(
            run_pipeline,
            qa_id=request.id,
            question=request.question,
            generation_run_id=generation_run_id,
        )
        await self.submit_generation_output(
            qa_id=request.id,
            question=request.question,
            generation_run_id=generation_run_id,
            output=output,
        )
        return GenerationRunResult(
            generation_run_id=generation_run_id,
            submission_status="submitted_to_testing",
            generation_status=output.status,
            reason=None if output.failure is None else output.failure.reason,
        )

    async def submit_generation_output(
        self,
        *,
        qa_id: str,
        question: str,
        generation_run_id: str,
        output: GenerationOutput,
    ) -> Dict[str, object]:
        payload = build_testing_agent_payload(
            qa_id=qa_id,
            question=question,
            generation_run_id=generation_run_id,
            output=output,
        )
        if isinstance(payload, GeneratedCypherSubmissionRequest):
            return await self.testing_client.submit(payload)
        return await self.testing_client.submit_generation_failure(payload)


def build_testing_agent_payload(
    *,
    qa_id: str,
    question: str,
    generation_run_id: str,
    output: GenerationOutput,
) -> GeneratedCypherSubmissionRequest | CgaGenerationNonSuccessReport:
    trace = _validated_graph_trace(
        output,
        qa_id=qa_id,
        question=question,
        generation_run_id=generation_run_id,
    )
    snapshot = json.dumps(trace.model_dump(mode="json", exclude_none=False), ensure_ascii=False, indent=2)
    if output.status == "generated":
        return GeneratedCypherSubmissionRequest(
            id=qa_id,
            question=question,
            generation_run_id=generation_run_id,
            generated_cypher=output.cypher or "",
            input_prompt_snapshot=snapshot,
        )

    failure_reason = None if output.failure is None else output.failure.reason
    clarification = None if output.clarification is None else output.clarification.model_dump(mode="json")
    return CgaGenerationNonSuccessReport(
        id=qa_id,
        question=question,
        generation_run_id=generation_run_id,
        generation_status=output.status,
        failure_reason=failure_reason,
        clarification=clarification,
        parsed_cypher=None,
        input_prompt_snapshot=snapshot,
        gate_passed=False,
    )


def _validated_graph_trace(
    output: GenerationOutput,
    *,
    qa_id: str,
    question: str,
    generation_run_id: str,
) -> GraphTraceRecord:
    trace = GraphTraceRecord.model_validate(output.trace)
    if trace.question_id != qa_id:
        raise ValueError(f"trace question_id {trace.question_id} does not match qa_id {qa_id}")
    if trace.generation_run_id != generation_run_id:
        raise ValueError(
            f"trace generation_run_id {trace.generation_run_id} does not match generation_run_id {generation_run_id}"
        )
    if trace.source_question != question:
        raise ValueError("trace source_question does not match submitted question")
    if trace.final_status != output.status:
        raise ValueError(f"trace final_status {trace.final_status} does not match output.status {output.status}")
    _validate_stage_contract(trace)

    outputs = trace.final_outputs
    if outputs.user_visible_notices != output.user_visible_notices:
        raise ValueError("trace user_visible_notices does not match output.user_visible_notices")
    if output.status == "generated":
        if outputs.cypher != output.cypher:
            raise ValueError("generated trace cypher does not match output.cypher")
        if outputs.dsl != output.dsl:
            raise ValueError("generated trace DSL does not match output.dsl")
        return trace

    if output.status == "clarification_required" and output.clarification is not None:
        if outputs.clarification != output.clarification:
            raise ValueError("clarification trace payload does not match output.clarification")
        return trace

    if output.failure is not None and outputs.failure is not None:
        if outputs.failure != output.failure:
            raise ValueError("failure trace payload does not match output.failure")
    return trace


def _validate_stage_contract(trace: GraphTraceRecord) -> None:
    stage_names = [str(stage.stage) for stage in trace.stages]
    if trace.final_status == "generated":
        if stage_names not in (GENERATED_STAGE_ORDER, DETERMINISTIC_GENERATED_STAGE_ORDER):
            raise ValueError(
                "generated trace stages must match a cga_graph_trace_v1 generated stage order: "
                f"{GENERATED_STAGE_ORDER} or {DETERMINISTIC_GENERATED_STAGE_ORDER}"
            )
        return

    if not stage_names:
        raise ValueError("non-success trace stages must not be empty before testing-agent submission")

    if stage_names[-1] != "output":
        raise ValueError("non-success trace stages must end with output before testing-agent submission")


def get_generator_status() -> Dict[str, object]:
    return {
        "status": "ok",
        "pipeline": "ir12_deterministic_mvp",
        "internal_flow": {
            "semantic_parse": [
                "graph_model_loader",
                "input_clarification_gate",
                "question_decomposer",
                "candidate_retrieval",
                "literal_resolver",
                "grounded_understanding",
                "semantic_binder",
                "semantic_validator",
                "dsl_builder",
                "dsl_parser",
                "dsl_structural_coverage_gate",
                "cypher_compiler",
                "cypher_self_validation",
            ]
        },
    }


@lru_cache(maxsize=1)
def get_workflow_service() -> CypherGeneratorAgentService:
    settings = get_settings()
    return CypherGeneratorAgentService(
        testing_client=TestingAgentClient(
            base_url=settings.testing_agent_url,
            timeout_seconds=settings.request_timeout_seconds,
        ),
    )
