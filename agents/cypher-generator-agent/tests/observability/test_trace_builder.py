from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from services.cypher_generator_agent.app.observability.stages import StageName
from services.cypher_generator_agent.app.observability.trace import (
    GraphTraceBuilder,
    GraphTraceFinalOutputs,
    GraphTraceRecord,
    TraceStage,
    inline_ref,
    redacted_ref,
)


def test_generated_trace_includes_dsl_cypher_stages_and_generated_status() -> None:
    builder = GraphTraceBuilder(
        trace_id="trace-001",
        question_id="qa-001",
        generation_run_id="run-001",
        source_question="全网有多少台防火墙？",
        semantic_model={"model_name": "network_topology", "spec_version": "graph_semantic_model_v1"},
        started_at=_dt(0),
    )
    builder.add_stage(
        stage="question_decomposer",
        status="success",
        started_at=_dt(1),
        duration_ms=12,
        input_ref=inline_ref({"question": "全网有多少台防火墙？"}),
        output_ref=inline_ref({"substantive_terms": ["防火墙"]}),
        metrics={"llm_call_count": 0},
    )
    builder.add_stage(
        stage=StageName.CYPHER_COMPILER,
        status="success",
        started_at=_dt(2),
        duration_ms=7,
        output_ref=inline_ref({"target_dialect": "neo4j"}),
    )

    output = builder.finalize_generated(
        dsl={"schema_version": "restricted_query_dsl_v1", "query": {"shape": "aggregation"}},
        cypher="MATCH (ne:NetworkElement) RETURN count(ne) AS count",
        user_visible_notices=["按 NetworkElement 统计。"],
        expected_api_status="generated",
        finished_at=_dt(3),
    )

    assert output.status == "generated"
    assert output.trace["trace_schema_version"] == "cga_graph_trace_v1"
    assert output.trace["final_status"] == "generated"
    assert output.trace["started_at"] == "2026-05-27T00:00:00+00:00"
    assert output.trace["finished_at"] == "2026-05-27T00:00:03+00:00"
    assert output.trace["semantic_model"]["model_name"] == "network_topology"
    assert [stage["stage"] for stage in output.trace["stages"]] == ["question_decomposer", "cypher_compiler"]
    assert output.trace["final_outputs"] == {
        "dsl": {"schema_version": "restricted_query_dsl_v1", "query": {"shape": "aggregation"}},
        "cypher": "MATCH (ne:NetworkElement) RETURN count(ne) AS count",
        "clarification": None,
        "user_visible_notices": ["按 NetworkElement 统计。"],
        "failure": None,
    }


def test_failure_trace_includes_failure_and_generation_failed_status() -> None:
    builder = GraphTraceBuilder(
        trace_id="trace-failed",
        question_id="qa-failed",
        generation_run_id="run-failed",
        source_question="查未知字段",
        started_at=_dt(0),
    )
    builder.add_stage(
        stage="semantic_validator",
        status="failed",
        started_at=_dt(1),
        duration_ms=3,
        errors=[{"code": "coverage_failure", "message": "uncovered term"}],
    )

    output = builder.finalize_failure(
        status="generation_failed",
        failure={"reason": "coverage_failure", "message": "uncovered term", "suggested_rewrites": []},
        expected_api_status="generation_failed",
        finished_at=_dt(2),
    )

    assert output.status == "generation_failed"
    assert output.failure is not None
    assert output.trace["final_status"] == "generation_failed"
    assert output.trace["final_outputs"]["failure"]["reason"] == "coverage_failure"
    assert output.trace["stages"][0]["status"] == "failed"


def test_clarification_trace_includes_clarification_and_required_status() -> None:
    builder = GraphTraceBuilder(
        trace_id="trace-clarify",
        question_id="qa-clarify",
        generation_run_id="run-clarify",
        source_question="查它的路径",
        started_at=_dt(0),
    )
    builder.add_stage(
        stage="input_clarification_gate",
        status="warning",
        started_at=_dt(1),
        duration_ms=4,
        output_ref=redacted_ref(reason="contains unresolved pronoun"),
        warnings=[{"code": "missing_referent", "message": "pronoun has no antecedent"}],
    )

    output = builder.finalize_clarification(
        clarification={"question": "你想查询哪个服务或隧道？"},
        expected_api_status="clarification_required",
        finished_at=_dt(2),
    )

    assert output.status == "clarification_required"
    assert output.clarification is not None
    assert output.trace["final_status"] == "clarification_required"
    assert output.trace["final_outputs"]["clarification"] == {"question": "你想查询哪个服务或隧道？"}


def test_invalid_stage_string_fails_validation() -> None:
    with pytest.raises(ValidationError):
        TraceStage(stage="free_form_stage", status="success", started_at=_dt(0), duration_ms=1)


def test_final_status_mismatch_with_expected_api_status_fails() -> None:
    builder = GraphTraceBuilder(
        trace_id="trace-mismatch",
        question_id="qa-mismatch",
        generation_run_id="run-mismatch",
        source_question="查服务",
    )

    with pytest.raises(ValueError, match="final_status generated does not match expected API status generation_failed"):
        builder.finalize_generated(
            dsl={"schema_version": "restricted_query_dsl_v1", "query": {}},
            cypher="MATCH (svc:Service) RETURN svc.id AS id",
            expected_api_status="generation_failed",
        )


@pytest.mark.parametrize("forbidden_field", ["db_connection", "execution_result"])
def test_trace_rejects_database_connection_and_execution_result_fields(forbidden_field: str) -> None:
    payload = {
        "trace_schema_version": "cga_graph_trace_v1",
        "trace_id": "trace-forbidden",
        "question_id": "qa-forbidden",
        "generation_run_id": "run-forbidden",
        "source_question": "查服务",
        "started_at": _dt(0),
        "finished_at": _dt(1),
        "final_status": "generated",
        "semantic_model": {},
        "stages": [],
        "final_outputs": {
            "dsl": {"schema_version": "restricted_query_dsl_v1", "query": {}},
            "cypher": "MATCH (svc:Service) RETURN svc.id AS id",
            "user_visible_notices": [],
        },
        forbidden_field: {},
    }

    with pytest.raises(ValidationError):
        GraphTraceRecord.model_validate(payload)


@pytest.mark.parametrize("forbidden_field", ["db_connection", "execution_result"])
def test_final_outputs_reject_forbidden_nested_fields(forbidden_field: str) -> None:
    with pytest.raises(ValidationError):
        GraphTraceFinalOutputs(
            failure={
                "reason": "coverage_failure",
                "message": "bad payload",
                forbidden_field: {"url": "bolt://secret"},
            }
        )


def _dt(seconds: int) -> datetime:
    return datetime(2026, 5, 27, 0, 0, seconds, tzinfo=timezone.utc)
