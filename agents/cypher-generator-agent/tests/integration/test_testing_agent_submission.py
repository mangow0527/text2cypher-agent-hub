from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from services.cypher_generator_agent.app.api.models import (
    CgaGenerationNonSuccessReport,
    GeneratedCypherSubmissionRequest,
)
from services.cypher_generator_agent.app.api.service import (
    CypherGeneratorAgentService,
    build_testing_agent_payload,
)
from services.cypher_generator_agent.app.infrastructure.clients import TestingAgentClient
from services.cypher_generator_agent.app.observability.trace import GraphTraceBuilder, inline_ref


def test_generated_submission_snapshot_is_full_cga_graph_trace_v1() -> None:
    builder = _trace_builder("qa-generated", "run-generated", "Gold 服务使用了哪些隧道")
    _add_generated_trace_stages(builder)
    output = builder.finalize_generated(
        dsl={"schema_version": "restricted_query_dsl_v1", "query_shape": "single_hop_traversal"},
        cypher="MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun:Tunnel) RETURN tun.id AS tunnel_id",
    )

    payload = build_testing_agent_payload(
        qa_id="qa-generated",
        question="Gold 服务使用了哪些隧道",
        generation_run_id="run-generated",
        output=output,
    )

    assert isinstance(payload, GeneratedCypherSubmissionRequest)
    snapshot = json.loads(payload.input_prompt_snapshot)
    assert snapshot["trace_schema_version"] == "cga_graph_trace_v1"
    assert snapshot["final_status"] == "generated"
    assert snapshot["final_outputs"]["cypher"] == payload.generated_cypher
    assert snapshot["final_outputs"]["dsl"]["query_shape"] == "single_hop_traversal"


def test_clarification_submission_uses_non_success_report_without_parsed_cypher() -> None:
    builder = _trace_builder("qa-clarify", "run-clarify", "那个服务用了哪些隧道")
    _add_non_success_output_stage(builder)
    output = builder.finalize_clarification(
        clarification={"question": "你说的“那个服务”具体指哪个服务？"},
        user_visible_notices=["我需要先确认服务名称。"],
    )

    payload = build_testing_agent_payload(
        qa_id="qa-clarify",
        question="那个服务用了哪些隧道",
        generation_run_id="run-clarify",
        output=output,
    )

    assert isinstance(payload, CgaGenerationNonSuccessReport)
    assert payload.generation_status == "clarification_required"
    assert payload.failure_reason is None
    assert payload.parsed_cypher is None
    assert payload.clarification == {"question": "你说的“那个服务”具体指哪个服务？"}
    snapshot = json.loads(payload.input_prompt_snapshot)
    assert snapshot["final_outputs"]["clarification"]["question"] == "你说的“那个服务”具体指哪个服务？"
    assert snapshot["final_outputs"]["cypher"] is None


def test_generation_failed_submission_carries_validation_trace_without_clarification() -> None:
    builder = _trace_builder("qa-failed", "run-failed", "2024 年收入增长情况")
    builder.add_stage(
        stage="semantic_validator",
        status="failed",
        duration_ms=5,
        input_ref=inline_ref({"query_shape": "metric_aggregate"}),
        output_ref=inline_ref({"is_valid": False}),
        errors=[
            {
                "code": "coverage_failure",
                "message": "增长 is not covered by the semantic model",
            }
        ],
    )
    _add_non_success_output_stage(builder)
    output = builder.finalize_failure(
        status="generation_failed",
        failure={"reason": "coverage_failure", "message": "增长 is not covered by the semantic model"},
    )

    payload = build_testing_agent_payload(
        qa_id="qa-failed",
        question="2024 年收入增长情况",
        generation_run_id="run-failed",
        output=output,
    )

    assert isinstance(payload, CgaGenerationNonSuccessReport)
    assert payload.generation_status == "generation_failed"
    assert payload.failure_reason == "coverage_failure"
    assert payload.clarification is None
    assert payload.parsed_cypher is None
    snapshot = json.loads(payload.input_prompt_snapshot)
    assert snapshot["stages"][0]["stage"] == "semantic_validator"
    assert snapshot["stages"][0]["errors"][0]["code"] == "coverage_failure"
    assert snapshot["final_outputs"]["failure"]["reason"] == "coverage_failure"


def test_service_failed_provider_exception_submission_uses_engineering_failure_status() -> None:
    builder = _trace_builder("qa-provider-down", "run-provider-down", "Gold 服务使用了哪些隧道")
    builder.add_stage(
        stage="grounded_understanding",
        status="failed",
        duration_ms=12,
        input_ref=inline_ref({"provider": "fake-llm"}),
        output_ref=inline_ref({"status": "service_failed"}),
        errors=[
            {
                "code": "model_invocation_failed",
                "provider": "fake-llm",
                "message": "provider timeout",
            }
        ],
    )
    _add_non_success_output_stage(builder)
    output = builder.finalize_failure(
        status="service_failed",
        failure={"reason": "model_invocation_failed", "message": "provider timeout"},
    )

    payload = build_testing_agent_payload(
        qa_id="qa-provider-down",
        question="Gold 服务使用了哪些隧道",
        generation_run_id="run-provider-down",
        output=output,
    )

    assert isinstance(payload, CgaGenerationNonSuccessReport)
    assert payload.generation_status == "service_failed"
    assert payload.failure_reason == "model_invocation_failed"
    snapshot = json.loads(payload.input_prompt_snapshot)
    assert snapshot["final_status"] == "service_failed"
    assert snapshot["stages"][0]["stage"] == "grounded_understanding"
    assert snapshot["stages"][0]["errors"][0]["provider"] == "fake-llm"


@pytest.mark.asyncio
async def test_service_submits_generated_and_non_success_trace_contracts_to_testing_agent() -> None:
    testing_client = _CaptureTestingClient()
    service = CypherGeneratorAgentService(testing_client=testing_client)

    generated_builder = _trace_builder("qa-generated", "run-generated", "Gold 服务使用了哪些隧道")
    _add_generated_trace_stages(generated_builder)
    generated = generated_builder.finalize_generated(
        dsl={"schema_version": "restricted_query_dsl_v1", "query_shape": "single_hop_traversal"},
        cypher="MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun:Tunnel) RETURN tun.id AS tunnel_id",
    )
    failed_builder = _trace_builder("qa-failed", "run-failed", "2024 年收入增长情况")
    _add_non_success_output_stage(failed_builder)
    failed = failed_builder.finalize_failure(
        status="generation_failed",
        failure={"reason": "coverage_failure", "message": "增长 is not covered by the semantic model"},
    )

    await service.submit_generation_output(
        qa_id="qa-generated",
        question="Gold 服务使用了哪些隧道",
        generation_run_id="run-generated",
        output=generated,
    )
    await service.submit_generation_output(
        qa_id="qa-failed",
        question="2024 年收入增长情况",
        generation_run_id="run-failed",
        output=failed,
    )

    assert testing_client.generated is not None
    assert testing_client.generated.generation_status == "generated"
    assert json.loads(testing_client.generated.input_prompt_snapshot)["trace_schema_version"] == "cga_graph_trace_v1"
    assert testing_client.non_success is not None
    assert testing_client.non_success.generation_status == "generation_failed"
    assert json.loads(testing_client.non_success.input_prompt_snapshot)["trace_schema_version"] == "cga_graph_trace_v1"


@pytest.mark.asyncio
async def test_testing_agent_client_retries_and_raises_final_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class AlwaysFailingAsyncClient:
        attempts = 0

        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "AlwaysFailingAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        async def post(self, url: str, *, json: dict[str, Any]) -> Any:
            type(self).attempts += 1
            raise RuntimeError(f"attempt-{type(self).attempts}-failed")

    monkeypatch.setattr(httpx, "AsyncClient", AlwaysFailingAsyncClient)

    client = TestingAgentClient(
        base_url="http://127.0.0.1:8003",
        timeout_seconds=3.0,
        max_submit_attempts=2,
    )
    payload = GeneratedCypherSubmissionRequest(
        id="qa-retry",
        question="查询协议版本",
        generation_run_id="run-retry",
        generated_cypher="MATCH (p:Protocol) RETURN p.version AS version",
        input_prompt_snapshot='{"trace_schema_version":"cga_graph_trace_v1"}',
    )

    with pytest.raises(RuntimeError, match="attempt-2-failed"):
        await client.submit(payload)
    assert AlwaysFailingAsyncClient.attempts == 2


def _trace_builder(qa_id: str, run_id: str, question: str) -> GraphTraceBuilder:
    return GraphTraceBuilder(
        trace_id=run_id,
        question_id=qa_id,
        generation_run_id=run_id,
        source_question=question,
        semantic_model={"name": "network_topology", "schema_version": "graph_semantic_model_v1"},
        started_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
    )


def _add_generated_trace_stages(builder: GraphTraceBuilder) -> None:
    for stage in [
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
    ]:
        builder.add_stage(stage=stage, status="success", duration_ms=1)


def _add_non_success_output_stage(builder: GraphTraceBuilder) -> None:
    builder.add_stage(stage="output", status="failed", duration_ms=0)


class _CaptureTestingClient:
    def __init__(self) -> None:
        self.generated: GeneratedCypherSubmissionRequest | None = None
        self.non_success: CgaGenerationNonSuccessReport | None = None

    async def submit(self, payload: GeneratedCypherSubmissionRequest) -> dict[str, bool]:
        self.generated = payload
        return {"accepted": True}

    async def submit_generation_failure(self, payload: CgaGenerationNonSuccessReport) -> dict[str, bool]:
        self.non_success = payload
        return {"accepted": True}
