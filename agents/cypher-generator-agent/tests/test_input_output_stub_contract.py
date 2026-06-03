from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.cypher_generator_agent.app.api.main import parse_semantics, recognize_intent
from services.cypher_generator_agent.app.api.models import (
    IntentRecognitionRequest,
    QAQuestionRequest,
    SemanticParseRequest,
)
from services.cypher_generator_agent.app.api.service import CypherGeneratorAgentService


SERVICE_ROOT = Path(__file__).resolve().parents[1]
GENERATED_NAMES = {"__pycache__", ".DS_Store"}


class _CaptureTestingClient:
    def __init__(self) -> None:
        self.question_received = None
        self.submission = None
        self.failure = None

    async def submit_question_received(self, payload):
        self.question_received = payload
        return {"accepted": True}

    async def submit(self, payload):
        self.submission = payload
        return {"ok": True}

    async def submit_generation_failure(self, payload):
        self.failure = payload
        return {"ok": True}


@pytest.mark.asyncio
async def test_ingest_question_submits_pipeline_generation_trace_contract() -> None:
    testing_client = _CaptureTestingClient()
    service = CypherGeneratorAgentService(testing_client=testing_client)

    result = await service.generate_and_submit_question(
        QAQuestionRequest(id="qa-osi-1", question="Gold 服务使用了哪些隧道ID"),
        generation_run_id="run-osi-1",
    )

    assert result.submission_status == "submitted_to_testing"
    assert result.generation_status == "generated"
    assert result.generation_run_id == "run-osi-1"
    assert testing_client.question_received is None
    assert testing_client.failure is None
    assert testing_client.submission is not None
    assert testing_client.submission.id == "qa-osi-1"
    assert testing_client.submission.question == "Gold 服务使用了哪些隧道ID"
    assert testing_client.submission.generation_run_id == result.generation_run_id
    assert "SERVICE_USES_TUNNEL" in testing_client.submission.generated_cypher

    snapshot = json.loads(testing_client.submission.input_prompt_snapshot)
    assert snapshot["trace_schema_version"] == "cga_graph_trace_v1"
    assert snapshot["trace_id"] == result.generation_run_id
    assert snapshot["question_id"] == "qa-osi-1"
    assert snapshot["source_question"] == "Gold 服务使用了哪些隧道ID"
    assert snapshot["final_status"] == "generated"
    assert snapshot["final_outputs"]["cypher"] == testing_client.submission.generated_cypher
    assert snapshot["final_outputs"]["dsl"]["query_shape"] == "single_hop_traversal"


@pytest.mark.asyncio
async def test_accept_question_reports_pending_without_running_pipeline() -> None:
    testing_client = _CaptureTestingClient()
    service = CypherGeneratorAgentService(testing_client=testing_client)

    result = await service.accept_question(QAQuestionRequest(id="qa-pending", question="查询服务使用的隧道"))

    assert result.submission_status == "submitted_to_testing"
    assert result.generation_status is None
    assert result.generation_run_id
    assert testing_client.question_received is not None
    assert testing_client.question_received.id == "qa-pending"
    assert testing_client.question_received.question == "查询服务使用的隧道"
    assert testing_client.question_received.generation_run_id == result.generation_run_id
    assert testing_client.question_received.generation_status == "generation_pending"
    assert testing_client.submission is None
    assert testing_client.failure is None


@pytest.mark.asyncio
async def test_semantic_parse_returns_pipeline_clarification_without_cypher() -> None:
    result = await parse_semantics(
        SemanticParseRequest(id="qa-osi-2", question="2024 年收入增长情况", generation_run_id="run-osi-2")
    )

    assert result["status"] == "clarification_required"
    assert "cypher" not in result
    assert "dsl" not in result
    assert result["clarification"]["question"]
    assert result["user_visible_notices"] == []

    trace = result["trace"]
    assert trace["started_at"]
    assert trace["finished_at"]
    assert trace["trace_id"] == "run-osi-2"
    assert trace["question_id"] == "qa-osi-2"
    assert trace["generation_run_id"] == "run-osi-2"
    assert trace["source_question"] == "2024 年收入增长情况"
    assert trace["final_status"] == "clarification_required"
    assert trace["semantic_model"]["name"] == "network_schema_v10"
    assert [stage["stage"] for stage in trace["stages"]][-3:] == [
        "semantic_validator",
        "repair_controller",
        "output",
    ]
    assert trace["final_outputs"]["cypher"] is None
    assert trace["final_outputs"]["dsl"] is None
    assert trace["final_outputs"]["failure"] is None
    assert trace["final_outputs"]["clarification"]["question"] == result["clarification"]["question"]


@pytest.mark.asyncio
async def test_semantic_parse_without_question_id_still_has_trace_question_id() -> None:
    result = await parse_semantics(SemanticParseRequest(question="查询端口信息", generation_run_id="run-no-qa"))

    assert result["trace"]["question_id"] == "run-no-qa"


@pytest.mark.asyncio
async def test_intent_recognition_returns_empty_io_skeleton() -> None:
    result = await recognize_intent(IntentRecognitionRequest(question="查询业务状态"))

    assert result == {
        "status": "stubbed",
        "input": {"question": "查询业务状态"},
        "output": {"intent": {}},
        "internal_flow": {},
    }


def test_cypher_generator_agent_contains_only_io_stub_files() -> None:
    allowed_top_level = {"__init__.py", "app", "tests"}
    assert _source_names(SERVICE_ROOT) <= allowed_top_level

    allowed_app_children = {
        "__init__.py",
        "api",
        "assembly",
        "binding",
        "compiler",
        "core",
        "cypher_validation",
        "decomposition",
        "dsl",
        "infrastructure",
        "literals",
        "observability",
        "repair",
        "retrieval",
        "semantic_model",
        "understanding",
        "validation",
    }
    assert _source_names(SERVICE_ROOT / "app") <= allowed_app_children

    allowed_core_files = {"__init__.py", "errors.py", "pipeline.py", "result.py"}
    assert _source_names(SERVICE_ROOT / "app" / "core") <= allowed_core_files

    allowed_infrastructure_files = {"__init__.py", "clients.py", "config.py", "llm_client.py"}
    assert _source_names(SERVICE_ROOT / "app" / "infrastructure") <= allowed_infrastructure_files

    allowed_semantic_model_files = {
        "__init__.py",
        "artifacts",
        "loader.py",
        "model.py",
        "registry.py",
        "tugraph_schema.py",
        "validator.py",
    }
    assert _source_names(SERVICE_ROOT / "app" / "semantic_model") <= allowed_semantic_model_files

    allowed_semantic_model_artifacts = {
        "tugraph_network_semantic_model.yaml",
        "tugraph_value_index.json",
    }
    assert _source_names(SERVICE_ROOT / "app" / "semantic_model" / "artifacts") <= allowed_semantic_model_artifacts

    allowed_cypher_validation_files = {
        "__init__.py",
        "dialect.py",
        "models.py",
        "parser.py",
        "readonly.py",
        "schema_reference.py",
        "shape.py",
        "validator.py",
    }
    assert _source_names(SERVICE_ROOT / "app" / "cypher_validation") <= allowed_cypher_validation_files

    allowed_decomposition_files = {
        "__init__.py",
        "coverage_terms.py",
        "decomposer.py",
        "models.py",
        "prompt.py",
    }
    assert _source_names(SERVICE_ROOT / "app" / "decomposition") <= allowed_decomposition_files

    allowed_dsl_files = {"__init__.py", "ast.py", "builder.py", "models.py", "parser.py"}
    assert _source_names(SERVICE_ROOT / "app" / "dsl") <= allowed_dsl_files

    allowed_compiler_files = {"__init__.py", "compiler.py", "literals.py", "projection.py", "templates.py"}
    assert _source_names(SERVICE_ROOT / "app" / "compiler") <= allowed_compiler_files

    allowed_literals_files = {
        "__init__.py",
        "models.py",
        "resolver.py",
        "typed_parser.py",
        "value_index.py",
    }
    assert _source_names(SERVICE_ROOT / "app" / "literals") <= allowed_literals_files

    allowed_retrieval_files = {
        "__init__.py",
        "index.py",
        "models.py",
        "retriever.py",
        "scoring.py",
        "structural_reranker.py",
    }
    assert _source_names(SERVICE_ROOT / "app" / "retrieval") <= allowed_retrieval_files

    allowed_assembly_files = {
        "__init__.py",
        "direction.py",
        "multihop.py",
        "taxonomy.py",
        "zero_hop.py",
    }
    assert _source_names(SERVICE_ROOT / "app" / "assembly") <= allowed_assembly_files

    allowed_binding_files = {"__init__.py", "binder.py", "models.py"}
    assert _source_names(SERVICE_ROOT / "app" / "binding") <= allowed_binding_files

    allowed_understanding_files = {
        "__init__.py",
        "grounded_understanding.py",
        "llm_client.py",
        "models.py",
        "prompt.py",
    }
    assert _source_names(SERVICE_ROOT / "app" / "understanding") <= allowed_understanding_files

    allowed_repair_files = {"__init__.py", "controller.py", "fingerprint.py", "models.py", "notices.py"}
    assert _source_names(SERVICE_ROOT / "app" / "repair") <= allowed_repair_files

    allowed_observability_files = {"__init__.py", "baseline.py", "metrics.py", "stages.py", "trace.py"}
    assert _source_names(SERVICE_ROOT / "app" / "observability") <= allowed_observability_files

    allowed_validation_files = {
        "__init__.py",
        "coverage.py",
        "models.py",
        "semantic_validator.py",
        "structural_requirements.py",
    }
    assert _source_names(SERVICE_ROOT / "app" / "validation") <= allowed_validation_files

    allowed_tests = {
        "__init__.py",
        "assembly",
        "binding",
        "compiler",
        "cypher_validation",
        "decomposition",
        "dsl",
        "fixtures",
        "infrastructure",
        "integration",
        "literals",
        "observability",
        "repair",
        "retrieval",
        "semantic_model",
        "understanding",
        "validation",
        "conftest.py",
        "test_input_output_stub_contract.py",
    }
    assert _source_names(SERVICE_ROOT / "tests") <= allowed_tests

    allowed_integration_files = {
        "__init__.py",
        "test_aggregate.py",
        "test_api_contract.py",
        "test_golden_questions.py",
        "test_pipeline_mvp.py",
        "test_testing_agent_submission.py",
        "test_two_step_aggregate.py",
        "test_variable_path.py",
    }
    assert _source_names(SERVICE_ROOT / "tests" / "integration") <= allowed_integration_files

    allowed_infrastructure_tests = {
        "__init__.py",
        "test_config.py",
        "test_llm_client.py",
    }
    assert _source_names(SERVICE_ROOT / "tests" / "infrastructure") <= allowed_infrastructure_tests

    allowed_semantic_model_tests = {
        "__init__.py",
        "test_loader.py",
        "test_registry.py",
        "test_tugraph_schema_converter.py",
    }
    assert _source_names(SERVICE_ROOT / "tests" / "semantic_model") <= allowed_semantic_model_tests

    allowed_cypher_validation_tests = {
        "__init__.py",
        "test_readonly.py",
        "test_dialect.py",
        "test_model_artifact_validation.py",
        "test_schema_reference_mvp.py",
        "test_shape.py",
        "test_variable_path_bounds.py",
        "test_validator_entrypoints.py",
    }
    assert _source_names(SERVICE_ROOT / "tests" / "cypher_validation") <= allowed_cypher_validation_tests

    allowed_decomposition_tests = {
        "__init__.py",
        "test_schema_retry.py",
        "test_term_classification.py",
    }
    assert _source_names(SERVICE_ROOT / "tests" / "decomposition") <= allowed_decomposition_tests

    allowed_dsl_tests = {
        "__init__.py",
        "test_builder_aggregate.py",
        "test_builder_named_path_pattern.py",
        "test_builder_single_hop.py",
        "test_builder_top_n_two_step.py",
        "test_builder_variable_path.py",
        "test_operation_sequences.py",
        "test_parser.py",
    }
    assert _source_names(SERVICE_ROOT / "tests" / "dsl") <= allowed_dsl_tests

    allowed_compiler_tests = {
            "__init__.py",
            "conftest.py",
            "test_aggregate.py",
            "test_literal_inliner.py",
            "test_named_path_pattern.py",
            "test_readonly_output.py",
        "test_single_hop.py",
        "test_top_n_two_step_aggregate.py",
        "test_variable_path.py",
        "test_vertex_lookup.py",
    }
    assert _source_names(SERVICE_ROOT / "tests" / "compiler") <= allowed_compiler_tests

    allowed_literals_tests = {
        "__init__.py",
        "test_enum_resolution.py",
        "test_id_resolution.py",
        "test_time_numeric_parse.py",
    }
    assert _source_names(SERVICE_ROOT / "tests" / "literals") <= allowed_literals_tests

    allowed_fixture_files = {
        "__init__.py",
        "expected_cypher",
        "expected_dsl",
        "golden_questions.yaml",
        "network_topology_graph_model.yaml",
        "performance_baseline_cases.yaml",
        "questions.yaml",
        "test_fixture_consistency.py",
        "test_tugraph_semantic_corpus.py",
        "tugraph_network_graph_model.yaml",
        "tugraph_value_index.json",
        "value_index.json",
    }
    assert _source_names(SERVICE_ROOT / "tests" / "fixtures") <= allowed_fixture_files

    allowed_observability_tests = {
        "__init__.py",
        "test_performance_baseline.py",
        "test_stage_contract.py",
        "test_trace_builder.py",
    }
    assert _source_names(SERVICE_ROOT / "tests" / "observability") <= allowed_observability_tests

    allowed_retrieval_tests = {"__init__.py", "test_candidate_retriever.py", "test_structural_reranker.py"}
    assert _source_names(SERVICE_ROOT / "tests" / "retrieval") <= allowed_retrieval_tests

    allowed_assembly_tests = {
        "test_direction.py",
        "test_multihop.py",
        "test_taxonomy.py",
        "test_zero_hop.py",
    }
    assert _source_names(SERVICE_ROOT / "tests" / "assembly") <= allowed_assembly_tests

    allowed_binding_tests = {"__init__.py", "test_binder.py"}
    assert _source_names(SERVICE_ROOT / "tests" / "binding") <= allowed_binding_tests

    allowed_understanding_tests = {
        "__init__.py",
        "test_candidate_boundaries.py",
        "test_grounded_schema.py",
        "test_prompt.py",
    }
    assert _source_names(SERVICE_ROOT / "tests" / "understanding") <= allowed_understanding_tests

    allowed_repair_tests = {
        "__init__.py",
        "test_assumption_notices.py",
        "test_decision_matrix.py",
        "test_fingerprint.py",
    }
    assert _source_names(SERVICE_ROOT / "tests" / "repair") <= allowed_repair_tests

    allowed_validation_tests = {
        "__init__.py",
        "test_aggregate.py",
        "test_coverage.py",
        "test_dsl_support.py",
        "test_edge_endpoint.py",
        "test_structural_requirements.py",
    }
    assert _source_names(SERVICE_ROOT / "tests" / "validation") <= allowed_validation_tests


def _source_names(path: Path) -> set[str]:
    return {child.name for child in path.iterdir() if child.name not in GENERATED_NAMES}
