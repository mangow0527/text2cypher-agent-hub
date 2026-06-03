from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.cypher_generator_agent.app.api.main import parse_semantics
from services.cypher_generator_agent.app.api.models import SemanticParseRequest
from services.cypher_generator_agent.app.core import pipeline as pipeline_module
from services.cypher_generator_agent.app.core.pipeline import run_pipeline
from services.cypher_generator_agent.app.core.result import ClarificationRequest
from services.cypher_generator_agent.app.decomposition.models import (
    QuestionDecompositionClarification,
    QuestionDecompositionFailure,
)
from services.cypher_generator_agent.app.infrastructure.config import get_settings
from services.cypher_generator_agent.app.retrieval.models import (
    CandidateEvidence,
    CandidateRetrievalResult,
    SemanticCandidate,
)
from services.cypher_generator_agent.app.understanding.models import (
    GroundedUnderstanding,
    GroundedUnderstandingFailure,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"

EXPECTED_STAGES = [
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

EXPECTED_DETERMINISTIC_STAGES = [
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


def _force_grounded_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pipeline_module,
        "_deterministic_assembler_payload",
        lambda **kwargs: {
            "schema_version": "deterministic_assembler_result_v1",
            "shape_status": "resolved",
            "shape": "test_forces_grounded_path",
            "shape_candidates": ["test_forces_grounded_path"],
            "success": False,
            "fallback_reason": "test_forces_grounded_path",
        },
    )


def test_gold_service_question_generates_single_hop_cypher() -> None:
    output = run_pipeline(
        question="Gold 服务使用了哪些隧道ID",
        qa_id="gq-001",
        generation_run_id="run-gq-001",
    )

    assert output.status == "generated"
    assert output.cypher is not None
    assert "MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun:Tunnel)" in output.cypher
    assert "svc.quality_of_service = 'Gold'" in output.cypher
    assert "$quality_of_service" not in output.cypher
    assert "RETURN tun.id AS tunnel_id" in output.cypher
    assert output.trace["semantic_model"]["name"] == "network_schema_v10"
    assert _compiler_parameters(output.trace)["quality_of_service"] == "Gold"
    assert "svc.quality_of_service = $quality_of_service" in _compiler_template(output.trace)
    assert _compiler_executable(output.trace) == output.cypher
    assert _stage_names(output.trace) == EXPECTED_DETERMINISTIC_STAGES
    assert "db_connection" not in _all_keys(output.trace)
    assert "execution_result" not in _all_keys(output.trace)


def test_multi_property_service_projection_uses_each_requested_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decompose(question: str) -> dict[str, Any]:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "original_question": question,
            "intent_type": "list",
            "output_shape": "rows",
            "literal_candidates": [],
            "literal_requests": [],
            "substantive_terms": [
                _decomp_term("服务", "projection"),
                _decomp_term("ID", "projection", attached_to="服务"),
                _decomp_term("名称", "projection", attached_to="服务"),
                _decomp_term("元素类型", "projection", attached_to="服务"),
                _decomp_term("服务质量等级", "projection", attached_to="服务"),
                _decomp_term("带宽", "projection", attached_to="服务"),
                _decomp_term("时延", "projection", attached_to="服务"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "coverage": {
                "substantive_terms": {"total": 7, "covered": 7, "uncovered": []},
                "stopword_terms": {"ignored": ["查询", "所有", "的", "和"]},
                "modality_terms": {"warning_only": []},
                "time_terms": {"covered": [], "unresolved": []},
                "unparsed_terms": {"unresolved": []},
                "projection_terms": {
                    "required": ["ID", "名称", "元素类型", "服务质量等级", "带宽", "时延"],
                    "covered": [],
                    "uncovered": ["ID", "名称", "元素类型", "服务质量等级", "带宽", "时延"],
                },
            },
        }

    monkeypatch.setattr(pipeline_module, "_mock_decompose", fake_decompose)

    output = run_pipeline(
        question="查询所有服务的 ID、名称、元素类型、服务质量等级、带宽和时延",
        qa_id="qa_9cfa692813d5",
        generation_run_id="run-qa_9cfa692813d5",
    )

    assert output.status == "generated"
    assert output.dsl is not None
    assert [item["property"]["name"] for item in output.dsl["projection"]["items"]] == [
        "id",
        "name",
        "elem_type",
        "quality_of_service",
        "bandwidth",
        "latency",
    ]
    assert "RETURN svc.id AS service_id" in output.cypher
    assert "svc.name AS service_name" in output.cypher
    assert "svc.elem_type AS service_elem_type" in output.cypher
    assert "svc.quality_of_service AS service_quality_of_service" in output.cypher
    assert "svc.bandwidth AS service_bandwidth" in output.cypher
    assert "svc.latency AS service_latency" in output.cypher


def test_input_clarification_gate_short_circuits_deictic_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(question: str) -> dict[str, object]:
        raise AssertionError(f"decomposer should not receive ambiguous input: {question}")

    monkeypatch.setattr(pipeline_module, "_mock_decompose", fail_if_called)

    output = run_pipeline(
        question="它最近 down 了吗",
        qa_id="input-gate",
        generation_run_id="run-input-gate",
    )

    assert output.status == "clarification_required"
    assert output.clarification is not None
    assert "它" in output.clarification.question
    assert output.cypher is None
    assert output.dsl is None
    assert _stage_names(output.trace) == ["graph_model_loader", "input_clarification_gate", "output"]


def test_input_clarification_gate_allows_demonstrative_with_explicit_referent() -> None:
    assert pipeline_module._input_clarification_gate("统计这些隧道关联的服务数量") == {"status": "pass"}
    assert pipeline_module._input_clarification_gate("查询所有服务与它们使用的隧道，返回服务ID和隧道ID。") == {
        "status": "pass"
    }


def test_naked_object_projection_requires_output_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decompose(question: str) -> dict[str, Any]:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "intent_type": "list",
            "original_question": question,
            "literal_candidates": [],
            "literal_requests": [],
            "substantive_terms": [_decomp_term("服务", "projection")],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "output_shape": "rows",
        }

    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_DISABLE_ENV_FILE", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(pipeline_module, "_mock_decompose", fake_decompose)

    try:
        output = run_pipeline(
            question="查询所有服务。",
            qa_id="naked-service-output-clarification",
            generation_run_id="run-naked-service-output-clarification",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "clarification_required"
    assert output.cypher is None
    assert output.dsl is None
    assert output.clarification is not None
    assert "返回" in output.clarification.question
    assert "字段" in output.clarification.question


def test_explicit_id_projection_still_returns_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decompose(question: str) -> dict[str, Any]:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "intent_type": "list",
            "original_question": question,
            "literal_candidates": [],
            "literal_requests": [],
            "substantive_terms": [
                _decomp_term("服务", "projection"),
                _decomp_term("ID", "projection", attached_to="服务"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "output_shape": "rows",
        }

    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_DISABLE_ENV_FILE", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(pipeline_module, "_mock_decompose", fake_decompose)

    try:
        output = run_pipeline(
            question="查询所有服务ID。",
            qa_id="explicit-service-id",
            generation_run_id="run-explicit-service-id",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generated"
    assert output.cypher == "MATCH (svc:Service)\nRETURN svc.id AS service_id"


def test_path_context_naked_projection_returns_vertex_without_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decompose(question: str) -> dict[str, Any]:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "intent_type": "list",
            "original_question": question,
            "literal_candidates": [],
            "literal_requests": [],
            "substantive_terms": [
                _decomp_term("服务", "path"),
                _decomp_term("使用", "path"),
                _decomp_term("隧道", "path"),
                _decomp_term("经过", "path"),
                _decomp_term("网元", "projection"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "output_shape": "rows",
        }

    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_DISABLE_ENV_FILE", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(pipeline_module, "_mock_decompose", fake_decompose)

    try:
        output = run_pipeline(
            question="查询所有服务使用的隧道所经过的网元。",
            qa_id="path-context-network-element-output",
            generation_run_id="run-path-context-network-element-output",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generated"
    assert output.clarification is None
    assert output.dsl is not None
    assert output.dsl["projection"]["items"] == [
        {"target": "end", "alias": "network_element", "vertex_full": True}
    ]
    assert "RETURN ne AS network_element" in output.cypher


def test_path_context_object_generic_suffix_returns_vertex_without_id_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decompose(question: str) -> dict[str, Any]:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "intent_type": "list",
            "original_question": question,
            "literal_candidates": [],
            "literal_requests": [],
            "substantive_terms": [
                _decomp_term("业务", "path"),
                _decomp_term("使用", "path"),
                _decomp_term("隧道", "projection"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "output_shape": "rows",
        }

    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_DISABLE_ENV_FILE", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(pipeline_module, "_mock_decompose", fake_decompose)

    try:
        output = run_pipeline(
            question="查询所有被业务使用的隧道节点。",
            qa_id="path-context-tunnel-node-output",
            generation_run_id="run-path-context-tunnel-node-output",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generated"
    assert output.clarification is None
    assert output.dsl is not None
    assert output.dsl["projection"]["items"] == [{"target": "end", "alias": "tunnel", "vertex_full": True}]
    assert "RETURN tun AS tunnel" in output.cypher


def test_grounded_path_without_projection_is_enriched_from_projection_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decompose(question: str) -> dict[str, Any]:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "intent_type": "list",
            "original_question": question,
            "literal_candidates": [],
            "literal_requests": [],
            "substantive_terms": [
                _decomp_term("业务", "path"),
                _decomp_term("使用", "path"),
                _decomp_term("隧道", "projection"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "output_shape": "rows",
        }

    def fake_understand(decomposition: dict[str, Any], literal_results: list[Any]) -> dict[str, Any]:
        return {
            "query_shape": "single_hop",
            "selected_vertices": [{"name": "Service"}, {"name": "Tunnel"}],
            "selected_edges": [{"name": "SERVICE_USES_TUNNEL", "direction": "forward"}],
            "selected_literals": [],
            "filters": [],
            "projection": [],
        }

    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_DISABLE_ENV_FILE", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(pipeline_module, "_mock_decompose", fake_decompose)
    monkeypatch.setattr(pipeline_module, "_mock_understand", fake_understand)
    _force_grounded_path(monkeypatch)

    try:
        output = run_pipeline(
            question="查询所有被业务使用的隧道节点。",
            qa_id="grounded-path-projection-enrichment",
            generation_run_id="run-grounded-path-projection-enrichment",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generated"
    assert output.clarification is None
    assert output.dsl is not None
    assert output.dsl["projection"]["items"] == [{"target": "end", "alias": "tunnel", "vertex_full": True}]
    assert "RETURN tun AS tunnel" in output.cypher


def test_literal_request_uses_filter_property_term_near_literal() -> None:
    decomposition = {
        "original_question": "查询延迟为20的服务的名称和延迟值。",
        "literal_candidates": [{"text": "20", "kind_hint": "number", "attached_to": "服务"}],
        "substantive_terms": [
            _decomp_term("延迟", "filter", attached_to="服务"),
            _decomp_term("20", "filter", attached_to="延迟"),
            _decomp_term("服务", "projection"),
            _decomp_term("名称", "projection", attached_to="服务"),
            _decomp_term("延迟值", "projection", attached_to="服务"),
        ],
    }

    enriched = pipeline_module._with_literal_requests_from_candidates(
        decomposition,
        CandidateRetrievalResult(
            candidates=[
                _candidate("vertex", "Service", semantic_name="服务"),
                _candidate(
                    "property",
                    "Service.name",
                    semantic_name="name",
                    owner="Service",
                    evidence_terms=["名称"],
                ),
                _candidate(
                    "property",
                    "Service.latency",
                    semantic_name="latency",
                    owner="Service",
                    evidence_terms=["延迟", "时延"],
                ),
            ]
        ),
    )

    assert enriched["literal_requests"] == [
        {
            "raw_literal": "20",
            "expected_vertex": "Service",
            "expected_property": "latency",
            "literal_kind_hint": "numeric",
        }
    ]


def test_literal_request_uses_name_filter_for_service_id_like_literal() -> None:
    decomposition = {
        "original_question": "查询名称为Service_003的服务的ID。",
        "literal_candidates": [{"text": "Service_003", "kind_hint": "enum_or_name", "attached_to": "服务"}],
        "substantive_terms": [
            _decomp_term("名称", "filter", attached_to="服务"),
            _decomp_term("Service_003", "filter", attached_to="名称"),
            _decomp_term("服务", "projection"),
            _decomp_term("ID", "projection", attached_to="服务"),
        ],
    }

    enriched = pipeline_module._with_literal_requests_from_candidates(
        decomposition,
        CandidateRetrievalResult(
            candidates=[
                _candidate("vertex", "Service", semantic_name="服务"),
                _candidate(
                    "property",
                    "Service.name",
                    semantic_name="name",
                    owner="Service",
                    evidence_terms=["名称"],
                ),
                _candidate(
                    "property",
                    "Service.quality_of_service",
                    semantic_name="quality_of_service",
                    owner="Service",
                    evidence_terms=["服务质量等级"],
                    valid_values=["Gold", "Silver"],
                ),
            ]
        ),
    )

    assert enriched["literal_requests"][0]["expected_vertex"] == "Service"
    assert enriched["literal_requests"][0]["expected_property"] == "name"


def test_literal_request_uses_type_filter_for_hyphenated_service_type() -> None:
    decomposition = {
        "original_question": "查询类型为MPLS-VPN的服务的ID、名称和带宽。",
        "literal_candidates": [{"text": "MPLS-VPN", "kind_hint": "enum_or_name", "attached_to": "服务"}],
        "substantive_terms": [
            _decomp_term("类型", "filter", attached_to="服务"),
            _decomp_term("MPLS-VPN", "filter", attached_to="服务"),
            _decomp_term("服务", "path"),
            _decomp_term("ID", "projection", attached_to="服务"),
            _decomp_term("名称", "projection", attached_to="服务"),
            _decomp_term("带宽", "projection", attached_to="服务"),
        ],
    }

    enriched = pipeline_module._with_literal_requests_from_candidates(
        decomposition,
        CandidateRetrievalResult(
            candidates=[
                _candidate("vertex", "Service", semantic_name="服务"),
                _candidate(
                    "property",
                    "Service.id",
                    semantic_name="id",
                    owner="Service",
                    evidence_terms=["ID", "标识"],
                ),
                _candidate(
                    "property",
                    "Service.elem_type",
                    semantic_name="elem_type",
                    owner="Service",
                    evidence_terms=["类型", "服务类型"],
                    valid_values=["MPLS-VPN", "QoS"],
                ),
            ]
        ),
    )

    assert enriched["literal_requests"] == [
        {
            "raw_literal": "MPLS-VPN",
            "expected_vertex": "Service",
            "expected_property": "elem_type",
            "literal_kind_hint": "enum_or_name",
        }
    ]


def test_literal_request_uses_previous_filter_term_when_literal_has_no_attachment() -> None:
    decomposition = {
        "original_question": "查询延迟等于23的服务名称、带宽和延迟。",
        "literal_candidates": [{"text": "23", "kind_hint": "number", "attached_to": ""}],
        "substantive_terms": [
            _decomp_term("延迟", "filter"),
            _decomp_term("等于", "filter"),
            _decomp_term("23", "filter"),
            _decomp_term("服务", "path"),
            _decomp_term("名称", "projection", attached_to="服务"),
            _decomp_term("带宽", "projection", attached_to="服务"),
            _decomp_term("延迟", "projection", attached_to="服务"),
        ],
    }

    enriched = pipeline_module._with_literal_requests_from_candidates(
        decomposition,
        CandidateRetrievalResult(
            candidates=[
                _candidate("vertex", "Service", semantic_name="服务"),
                _candidate("vertex", "Tunnel", semantic_name="隧道"),
                _candidate(
                    "property",
                    "Service.name",
                    semantic_name="name",
                    owner="Service",
                    evidence_terms=["名称"],
                ),
                _candidate(
                    "property",
                    "Service.latency",
                    semantic_name="latency",
                    owner="Service",
                    evidence_terms=["延迟", "时延"],
                ),
                _candidate(
                    "property",
                    "Tunnel.latency",
                    semantic_name="latency",
                    owner="Tunnel",
                    evidence_terms=["延迟", "时延"],
                ),
            ]
        ),
    )

    assert enriched["literal_requests"] == [
        {
            "raw_literal": "23",
            "expected_vertex": "Service",
            "expected_property": "latency",
            "literal_kind_hint": "numeric",
        }
    ]


def test_grounded_projection_bindings_hydrate_projection_when_projection_array_is_empty() -> None:
    grounded = GroundedUnderstanding.model_validate(
        {
            "schema_version": "grounded_understanding_v1",
            "status": "grounded",
            "query_shape": "single_hop",
            "selected_bindings": [
                _grounded_binding("source", "vertex", "Service"),
                _grounded_binding("target", "vertex", "Tunnel"),
                _grounded_binding("projection", "property", "Tunnel.name", semantic_name="name", owner="Tunnel"),
                _grounded_binding(
                    "projection",
                    "property",
                    "Tunnel.ietf_standard",
                    semantic_name="ietf_standard",
                    owner="Tunnel",
                ),
            ],
            "selected_literals": [],
            "filters": [],
            "projection": [],
            "group_by": [],
            "measures": [],
            "sort": [],
            "assumptions": [],
            "ambiguities": [],
            "coverage": _coverage_payload(["服务", "隧道", "名称", "IETF标准"]),
            "unsupported": None,
            "confidence": 0.9,
        }
    )

    payload = grounded.to_binder_payload()

    assert payload["projection"] == [
        {"semantic_type": "property", "owner": "Tunnel", "name": "name"},
        {"semantic_type": "property", "owner": "Tunnel", "name": "ietf_standard"},
    ]


def test_pipeline_semantic_artifacts_can_be_overridden_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CYPHER_GENERATOR_AGENT_GRAPH_MODEL_PATH",
        str(FIXTURE_DIR / "network_topology_graph_model.yaml"),
    )
    monkeypatch.setenv(
        "CYPHER_GENERATOR_AGENT_VALUE_INDEX_PATH",
        str(FIXTURE_DIR / "value_index.json"),
    )
    get_settings.cache_clear()

    try:
        output = run_pipeline(
            question="Gold 服务使用了哪些隧道ID",
            qa_id="settings-override",
            generation_run_id="run-settings-override",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generated"
    assert output.trace["semantic_model"]["name"] == "network_topology"
    assert _compiler_parameters(output.trace)["quality_of_service"] == "GOLD"


def test_pipeline_can_use_real_llm_mode_with_openai_compatible_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStructuredClient:
        provider = "openai_compatible"

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self.responses = [
                {
                    "schema_version": "question_decomposition_v1",
                    "result_type": "decomposition",
                    "intent_type": "list",
                    "original_question": "Gold 服务使用了哪些隧道ID",
                    "literal_candidates": [
                        {"text": "Gold", "kind_hint": "enum_or_name", "attached_to": "服务"}
                    ],
                    "substantive_terms": [
                        _decomp_term("Gold", "filter", attached_to="服务"),
                        _decomp_term("服务", "path"),
                        _decomp_term("使用", "path"),
                        _decomp_term("隧道", "projection"),
                        _decomp_term("ID", "projection", attached_to="隧道"),
                    ],
                    "modality_terms": [],
                    "time_terms": [],
                    "unparsed_terms": [],
                    "output_shape": "rows",
                },
                {
                    "schema_version": "grounded_understanding_v1",
                    "status": "grounded",
                    "query_shape": "single_hop",
                    "selected_bindings": [
                        _grounded_binding("source", "vertex", "Service"),
                        _grounded_binding("target", "vertex", "Tunnel"),
                        _grounded_binding("relation", "edge", "SERVICE_USES_TUNNEL", direction="forward"),
                        _grounded_binding(
                            "filter_property",
                            "property",
                            "Service.quality_of_service",
                            semantic_name="quality_of_service",
                            owner="Service",
                        ),
                    ],
                    "selected_literals": [
                        {
                            "schema_version": "literal_resolver_result_v1",
                            "raw_literal": "Gold",
                            "resolved": True,
                            "resolved_value": "Gold",
                            "normalized_value": "Gold",
                            "match_type": "exact",
                            "confidence": 1.0,
                            "expected_vertex": "Service",
                            "expected_edge": None,
                            "expected_property": "quality_of_service",
                            "evidence": [
                                {"source": "property.valid_values", "matched": "Gold", "target": "Gold"}
                            ],
                            "alternatives": [],
                            "requires_user_choice": False,
                            "value_index_miss": False,
                            "error_code": None,
                        }
                    ],
                    "filters": [
                        {
                            "owner": "Service",
                            "property": "quality_of_service",
                            "operator": "=",
                            "raw_literal": "Gold",
                        }
                    ],
                    "projection": [
                        {
                            "semantic_type": "property",
                            "owner": "Tunnel",
                            "name": "id",
                            "alias": "tunnel_id",
                        }
                    ],
                    "coverage": {
                        "substantive_terms": {
                            "total": 4,
                            "covered": 4,
                            "uncovered": [],
                        },
                        "stopword_terms": {"ignored": []},
                        "modality_terms": {"warning_only": []},
                        "time_terms": {"covered": [], "unresolved": []},
                        "unparsed_terms": {"unresolved": []},
                    },
                    "unsupported": None,
                    "confidence": 0.93,
                },
            ]

        def generate_structured(
            self,
            *,
            prompt: str,
            schema_name: str,
            schema: dict[str, Any],
            attempt: int,
        ) -> dict[str, Any]:
            self.calls.append(
                {
                    "prompt": prompt,
                    "schema_name": schema_name,
                    "schema": schema,
                    "attempt": attempt,
                }
            )
            return self.responses.pop(0)

    fake_client = FakeStructuredClient()
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_API_KEY", "test-key")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_MODEL", "qwen3-32b")
    get_settings.cache_clear()
    monkeypatch.setattr(
        pipeline_module,
        "_structured_llm_client_from_settings",
        lambda settings: fake_client,
    )

    try:
        output = run_pipeline(
            question="Gold 服务使用了哪些隧道ID",
            qa_id="real-llm-mode",
            generation_run_id="run-real-llm-mode",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generated"
    assert len(fake_client.calls) == 2
    assert [call["schema_name"] for call in fake_client.calls] == [
        "question_decomposition_v1",
        "grounded_understanding_v1",
    ]
    assert output.trace["semantic_model"]["name"] == "network_schema_v10"
    assert _compiler_parameters(output.trace)["quality_of_service"] == "Gold"
    decomposer_stage = next(
        stage for stage in output.trace["stages"] if stage["stage"] == "question_decomposer"
    )
    llm_calls = decomposer_stage["output_ref"]["value"]["llm_calls"]
    assert llm_calls[0]["stage"] == "question_decomposer"
    assert llm_calls[0]["schema_name"] == "question_decomposition_v1"
    assert "返回且只返回一个 JSON 对象" in llm_calls[0]["prompt"]
    assert '图原生 Cypher 生成流水线中的"问题结构化拆解器"' in llm_calls[0]["prompt"]
    assert "两条" + "正交的分类轴" not in llm_calls[0]["prompt"]
    assert "substantive_terms 的 slot 取值" in llm_calls[0]["prompt"]
    assert "示例 3:含时间、近似、聚合" in llm_calls[0]["prompt"]
    assert "Return exactly one JSON object" not in llm_calls[0]["prompt"]
    assert "JSON Schema:" in llm_calls[0]["prompt"]
    assert '"intent_type": "list"' in llm_calls[0]["raw_output"]


def test_llm_literal_kind_hint_outside_contract_is_schema_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStructuredClient:
        provider = "openai_compatible"

        def generate_structured(
            self,
            *,
            prompt: str,
            schema_name: str,
            schema: dict[str, Any],
            attempt: int,
        ) -> dict[str, Any]:
            return {
                "schema_version": "question_decomposition_v1",
                "result_type": "decomposition",
                "intent_type": "list",
                "original_question": "Gold 服务使用了哪些隧道",
                "literal_candidates": [
                    {"text": "Gold", "kind_hint": "service", "attached_to": "服务"}
                ],
                "substantive_terms": [
                    _decomp_term("Gold", "filter", attached_to="服务"),
                    _decomp_term("服务", "path"),
                    _decomp_term("使用", "path"),
                    _decomp_term("隧道", "projection"),
                ],
                "modality_terms": [],
                "time_terms": [],
                "unparsed_terms": [],
                "output_shape": "rows",
            }

    fake_client = FakeStructuredClient()
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(
        pipeline_module,
        "_structured_llm_client_from_settings",
        lambda settings: fake_client,
    )
    try:
        output = run_pipeline(
            question="Gold 服务使用了哪些隧道",
            qa_id="llm-kind-hint-schema-invalid",
            generation_run_id="run-llm-kind-hint-schema-invalid",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generation_failed"
    assert output.failure is not None
    assert output.failure.reason == "question_decomposer_schema_invalid"


def test_llm_enum_literal_with_qualifier_prefers_enum_property_over_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStructuredClient:
        provider = "openai_compatible"

        def generate_structured(
            self,
            *,
            prompt: str,
            schema_name: str,
            schema: dict[str, Any],
            attempt: int,
        ) -> dict[str, Any]:
            return {
                "schema_version": "question_decomposition_v1",
                "result_type": "decomposition",
                "intent_type": "list",
                    "original_question": "Gold级别的服务都使用了哪些隧道ID",
                "literal_candidates": [
                    {"text": "Gold级别", "kind_hint": "enum_or_name", "attached_to": "服务"}
                ],
                "substantive_terms": [
                    _decomp_term("Gold级别", "filter", attached_to="服务"),
                    _decomp_term("服务", "path"),
                    _decomp_term("使用", "path"),
                    _decomp_term("隧道", "projection"),
                    _decomp_term("ID", "projection", attached_to="隧道"),
                ],
                "modality_terms": [],
                "time_terms": [],
                "unparsed_terms": [],
                "output_shape": "rows",
            }

    def fake_grounded_stage(
        trace: object,
        *,
        decomposition: dict[str, Any],
        retrieval_result: object,
        literal_results: list[object],
        settings: object,
        llm_client: object | None,
        attempt_no: int,
        registry: object | None = None,
        repair_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        literal = literal_results[0].model_dump(mode="json")
        assert literal["raw_literal"] == "Gold级别"
        assert literal["resolved_value"] == "Gold"
        assert literal["expected_property"] == "quality_of_service"
        return {
            "schema_version": "grounded_understanding_v1",
            "status": "grounded",
            "query_shape": "single_hop",
            "selected_bindings": [
                _grounded_binding("source", "vertex", "Service"),
                _grounded_binding("target", "vertex", "Tunnel"),
                _grounded_binding("relation", "edge", "SERVICE_USES_TUNNEL", direction="forward"),
                _grounded_binding(
                    "filter_property",
                    "property",
                    "Service.quality_of_service",
                    semantic_name="quality_of_service",
                    owner="Service",
                ),
            ],
            "selected_literals": [literal],
            "filters": [
                {
                    "owner": "Service",
                    "property": "quality_of_service",
                    "operator": "=",
                    "raw_literal": "Gold级别",
                }
            ],
            "projection": [
                {"semantic_type": "property", "owner": "Tunnel", "name": "id", "alias": "tunnel_id"}
            ],
            "coverage": {
                "substantive_terms": {"total": 4, "covered": 4, "uncovered": []},
                "stopword_terms": {"ignored": ["都", "哪些"]},
                "modality_terms": {"warning_only": []},
                "time_terms": {"covered": [], "unresolved": []},
                "unparsed_terms": {"unresolved": []},
            },
            "unsupported": None,
            "confidence": 0.93,
        }

    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(
        pipeline_module,
        "_structured_llm_client_from_settings",
        lambda settings: FakeStructuredClient(),
    )
    monkeypatch.setattr(pipeline_module, "_run_grounded_understanding_stage", fake_grounded_stage)

    try:
        output = run_pipeline(
            question="Gold级别的服务都使用了哪些隧道ID",
            qa_id="llm-gold-qualifier",
            generation_run_id="run-llm-gold-qualifier",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generated"
    assert _compiler_parameters(output.trace)["quality_of_service"] == "Gold"


def test_decomposition_slot_normalization_uses_attachment_and_classifier_without_prompt_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStructuredClient:
        provider = "openai_compatible"

        def generate_structured(
            self,
            *,
            prompt: str,
            schema_name: str,
            schema: dict[str, Any],
            attempt: int,
        ) -> dict[str, Any]:
            return {
                "schema_version": "question_decomposition_v1",
                "result_type": "decomposition",
                "intent_type": "count",
                "original_question": "有多少台防火墙",
                "literal_candidates": [],
                "substantive_terms": [
                    _decomp_term("多少", "projection"),
                    _decomp_term("台", "projection"),
                    _decomp_term("防火墙", "projection"),
                ],
                "modality_terms": [],
                "time_terms": [],
                "unparsed_terms": [],
                "output_shape": "scalar",
            }

    def fake_grounded_stage(
        trace: object,
        *,
        decomposition: dict[str, Any],
        retrieval_result: object,
        literal_results: list[object],
        settings: object,
        llm_client: object | None,
        attempt_no: int,
        registry: object | None = None,
        repair_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidate_ids = {f"{item.semantic_type}:{item.semantic_id}" for item in retrieval_result.candidates}
        assert "vertex:NetworkElement" in candidate_ids
        literal = literal_results[0].model_dump(mode="json")
        assert literal["resolved_value"] == "firewall"
        assert literal["expected_vertex"] == "NetworkElement"
        return {
            "schema_version": "grounded_understanding_v1",
            "status": "grounded",
            "query_shape": "metric_aggregate",
            "selected_bindings": [
                _grounded_binding("target_vertex", "vertex", "NetworkElement"),
                _grounded_binding(
                    "filter_property",
                    "property",
                    "NetworkElement.elem_type",
                    semantic_name="elem_type",
                    owner="NetworkElement",
                ),
            ],
            "selected_literals": [literal],
            "filters": [{"property": "NetworkElement.elem_type", "value": "firewall"}],
            "projection": [],
            "group_by": [],
            "measures": [{"function": "count", "vertex": "NetworkElement"}],
            "coverage": {
                "substantive_terms": {"total": 3, "covered": 3, "uncovered": []},
                "stopword_terms": {"ignored": []},
                "modality_terms": {"warning_only": []},
                "time_terms": {"covered": [], "unresolved": []},
                "unparsed_terms": {"unresolved": []},
            },
            "unsupported": None,
            "confidence": 0.93,
        }

    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(
        pipeline_module,
        "_structured_llm_client_from_settings",
        lambda settings: FakeStructuredClient(),
    )
    monkeypatch.setattr(pipeline_module, "_run_grounded_understanding_stage", fake_grounded_stage)

    try:
        output = run_pipeline(
            question="有多少台防火墙",
            qa_id="slot-normalization",
            generation_run_id="run-slot-normalization",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generated"
    assert "count(ne.id)" in output.cypher
    assert _compiler_parameters(output.trace)["elem_type"] == "firewall"


def test_value_synonym_candidate_becomes_literal_request_when_llm_omits_literal_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStructuredClient:
        provider = "openai_compatible"

        def generate_structured(
            self,
            *,
            prompt: str,
            schema_name: str,
            schema: dict[str, Any],
            attempt: int,
        ) -> dict[str, Any]:
            return {
                "schema_version": "question_decomposition_v1",
                "result_type": "decomposition",
                "intent_type": "count",
                "original_question": "有多少台防火墙",
                "literal_candidates": [],
                "substantive_terms": [
                    _decomp_term("多少", "projection"),
                    _decomp_term("台", "projection"),
                    _decomp_term("防火墙", "projection"),
                ],
                "modality_terms": [],
                "time_terms": [],
                "unparsed_terms": [],
                "output_shape": "scalar",
            }

    fake_client = FakeStructuredClient()
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(
        pipeline_module,
        "_structured_llm_client_from_settings",
        lambda settings: fake_client,
    )

    try:
        output = run_pipeline(
            question="有多少台防火墙",
            qa_id="value-candidate-literal",
            generation_run_id="run-value-candidate-literal",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generated"
    assert "WHERE ne.elem_type = 'firewall'" in output.cypher
    assert "$elem_type" not in output.cypher
    assert "WHERE ne.elem_type = $elem_type" in _compiler_template(output.trace)
    assert _compiler_parameters(output.trace)["elem_type"] == "firewall"


def test_llm_vertex_lookup_without_filter_or_projection_requires_output_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStructuredClient:
        provider = "openai_compatible"

        def __init__(self) -> None:
            self.responses = [
                {
                    "schema_version": "question_decomposition_v1",
                    "result_type": "decomposition",
                    "intent_type": "list",
                    "original_question": "当前 down 的端口有哪些",
                    "literal_candidates": [
                        {"text": "down", "kind_hint": "enum_or_name", "attached_to": "端口"}
                    ],
                    "substantive_terms": [
                        _decomp_term("down", "filter", attached_to="端口"),
                        _decomp_term("端口", "projection"),
                    ],
                    "modality_terms": [],
                    "time_terms": ["当前"],
                    "unparsed_terms": [],
                    "output_shape": "rows",
                },
                {
                    "schema_version": "grounded_understanding_v1",
                    "status": "grounded",
                    "query_shape": "vertex_lookup",
                    "selected_bindings": [
                        _grounded_binding("target_vertex", "vertex", "Port"),
                        _grounded_binding(
                            "filter_property",
                            "property",
                            "Port.status",
                            semantic_name="status",
                            owner="Port",
                        ),
                    ],
                    "selected_literals": [],
                    "filters": [],
                    "projection": [],
                    "coverage": {
                        "substantive_terms": {"total": 2, "covered": 2, "uncovered": []},
                        "stopword_terms": {"ignored": ["有哪些"]},
                        "modality_terms": {"warning_only": []},
                        "time_terms": {"covered": [], "unresolved": []},
                        "unparsed_terms": {"unresolved": []},
                    },
                    "unsupported": None,
                    "confidence": 0.92,
                },
            ]

        def generate_structured(
            self,
            *,
            prompt: str,
            schema_name: str,
            schema: dict[str, Any],
            attempt: int,
        ) -> dict[str, Any]:
            if schema_name == "grounded_understanding_v1":
                self.responses[0]["selected_literals"] = [
                    result
                    for result in _latest_literal_resolver_results
                ]
            return self.responses.pop(0)

    _latest_literal_resolver_results: list[dict[str, Any]] = []
    original_run_grounded = pipeline_module._run_grounded_understanding_stage

    def capture_literals(*args: Any, **kwargs: Any) -> Any:
        _latest_literal_resolver_results[:] = [
            result.model_dump(mode="json")
            for result in kwargs["literal_results"]
        ]
        return original_run_grounded(*args, **kwargs)

    fake_client = FakeStructuredClient()
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(
        pipeline_module,
        "_structured_llm_client_from_settings",
        lambda settings: fake_client,
    )
    monkeypatch.setattr(pipeline_module, "_run_grounded_understanding_stage", capture_literals)

    try:
        output = run_pipeline(
            question="当前 down 的端口有哪些",
            qa_id="llm-vertex-lookup-defaults",
            generation_run_id="run-llm-vertex-lookup-defaults",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "clarification_required"
    assert output.cypher is None
    assert output.dsl is None
    assert output.clarification is not None
    assert "返回" in output.clarification.question
    assert "字段" in output.clarification.question


def test_llm_single_shot_fallback_does_not_reground_after_repairable_validator_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStructuredClient:
        provider = "openai_compatible"

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self.responses = [
                {
                    "schema_version": "question_decomposition_v1",
                    "result_type": "decomposition",
                    "intent_type": "list",
                    "original_question": "Gold 服务使用了哪些隧道ID",
                    "literal_candidates": [
                        {"text": "Gold", "kind_hint": "enum_or_name", "attached_to": "服务"}
                    ],
                    "substantive_terms": [
                        _decomp_term("Gold", "filter", attached_to="服务"),
                        _decomp_term("服务", "path"),
                        _decomp_term("使用", "path"),
                        _decomp_term("隧道", "projection"),
                        _decomp_term("ID", "projection", attached_to="隧道"),
                    ],
                    "modality_terms": [],
                    "time_terms": [],
                    "unparsed_terms": [],
                    "output_shape": "rows",
                },
            ]

        def generate_structured(
            self,
            *,
            prompt: str,
            schema_name: str,
            schema: dict[str, Any],
            attempt: int,
        ) -> dict[str, Any]:
            self.calls.append(
                {
                    "prompt": prompt,
                    "schema_name": schema_name,
                    "schema": schema,
                    "attempt": attempt,
                }
            )
            return self.responses.pop(0)

    fake_client = FakeStructuredClient()

    class FakeGroundedSelector:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def select(self, **_kwargs: Any) -> dict[str, object]:
            fake_client.calls.append(
                {
                    "prompt": "",
                    "schema_name": "grounded_understanding_v1",
                    "schema": {},
                    "attempt": 1,
                }
            )
            return _grounded_service_tunnel_payload(direction="backward")

    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(
        pipeline_module,
        "_structured_llm_client_from_settings",
        lambda settings: fake_client,
    )
    monkeypatch.setattr(pipeline_module, "GroundedUnderstandingSelector", FakeGroundedSelector)
    monkeypatch.setattr(
        pipeline_module,
        "_deterministic_grounding_from_slots",
        lambda **kwargs: None,
    )

    try:
        output = run_pipeline(
            question="Gold 服务使用了哪些隧道ID",
            qa_id="llm-repair-loop",
            generation_run_id="run-llm-repair-loop",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generation_failed"
    assert [call["schema_name"] for call in fake_client.calls] == [
        "question_decomposition_v1",
        "grounded_understanding_v1",
    ]
    assert _stage_names(output.trace).count("grounded_understanding") == 1
    assert _stage_names(output.trace).count("repair_controller") == 1


def test_tunnel_path_question_generates_named_path_pattern_cypher() -> None:
    output = run_pipeline(
        question="隧道 tun-mpls-001 经过哪些设备信息",
        qa_id="gq-003",
        generation_run_id="run-gq-003",
    )

    assert output.status == "generated"
    assert output.cypher is not None
    assert output.cypher == (
        "MATCH (t:Tunnel {id: 'tun-mpls-001'})-[p:PATH_THROUGH]->(ne:NetworkElement)\n"
        "RETURN ne AS device, p.hop_order AS hop\n"
        "ORDER BY p.hop_order ASC"
    )
    assert _compiler_parameters(output.trace) == {"tunnel_id": "tun-mpls-001"}
    assert "MATCH (t:Tunnel {id: $tunnel_id})" in _compiler_template(output.trace)
    assert _compiler_executable(output.trace) == output.cypher
    assert output.dsl is not None
    assert output.dsl["query_shape"] == "named_path_pattern"
    assert output.dsl["operations"][0]["path_pattern_name"] == "tunnel_full_path"
    assert _stage_names(output.trace) == EXPECTED_STAGES


def test_coverage_failure_does_not_emit_cypher_or_dsl() -> None:
    output = run_pipeline(
        question="2024 年收入增长情况",
        qa_id="coverage-failure",
        generation_run_id="run-coverage-failure",
    )

    assert output.status == "clarification_required"
    assert output.cypher is None
    assert output.dsl is None
    assert output.failure is None
    assert output.clarification is not None
    assert "收入" in output.clarification.question
    assert output.trace["final_outputs"]["clarification"]["question"] == output.clarification.question
    assert output.trace["final_outputs"]["cypher"] is None
    assert output.trace["final_outputs"]["dsl"] is None
    assert _stage_names(output.trace)[-3:] == ["semantic_validator", "repair_controller", "output"]


def test_generated_output_includes_user_visible_assumption_notices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_decompose = pipeline_module._mock_decompose

    def modality_decompose(question: str) -> dict[str, object]:
        payload = original_decompose("全网有多少台防火墙")
        payload["original_question"] = question
        payload["literal_candidates"] = [
            {"text": "防火墙", "kind_hint": "enum_or_name", "attached_to": "设备"}
        ]
        payload["substantive_terms"] = [
            _decomp_term("多少", "projection"),
            _decomp_term("防火墙", "projection"),
        ]
        payload["coverage"] = {
            "substantive_terms": {"total": 2, "covered": 2, "uncovered": []},
            "stopword_terms": {"ignored": []},
            "modality_terms": {"warning_only": ["大概"]},
            "time_terms": {"covered": [], "unresolved": []},
            "unparsed_terms": {"unresolved": []},
        }
        return payload

    monkeypatch.setattr(pipeline_module, "_mock_decompose", modality_decompose)

    output = run_pipeline(
        question="大概有多少防火墙",
        qa_id="assumption-notice",
        generation_run_id="run-assumption-notice",
    )

    assert output.status == "generated"
    assert output.user_visible_notices == ["问题中的“大概”没有被解释为查询约束。"]
    assert output.trace["final_outputs"]["user_visible_notices"] == output.user_visible_notices


def test_unsupported_query_shape_from_validator_returns_unsupported_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unsupported_understanding(
        decomposition: dict[str, object],
        literal_results: list[object],
    ) -> dict[str, object]:
        return {
            "query_shape": "shortest_path",
            "selected_vertices": ["Service"],
            "projection": [{"semantic_type": "vertex", "name": "Service"}],
        }

    monkeypatch.setattr(pipeline_module, "_mock_understand", unsupported_understanding)
    _force_grounded_path(monkeypatch)

    output = run_pipeline(
        question="Gold 服务使用了哪些隧道ID",
        qa_id="unsupported-shape",
        generation_run_id="run-unsupported-shape",
    )

    assert output.status == "unsupported_query_shape"
    assert output.cypher is None
    assert output.dsl is None
    assert output.failure is not None
    assert output.failure.reason == "unsupported_query_shape"
    assert _stage_names(output.trace)[-3:] == ["semantic_validator", "repair_controller", "output"]


def test_unresolved_literal_stops_before_dsl_or_cypher_generation() -> None:
    output = run_pipeline(
        question="Platinum 服务使用了哪些隧道",
        qa_id="literal-unresolved",
        generation_run_id="run-literal-unresolved",
    )

    assert output.status == "clarification_required"
    assert output.cypher is None
    assert output.dsl is None
    assert output.failure is None
    assert output.clarification is not None
    assert "Platinum" in output.clarification.question
    assert _stage_names(output.trace) == [
        "graph_model_loader",
        "input_clarification_gate",
        "question_decomposer",
        "candidate_retrieval",
        "candidate_reranker",
        "literal_resolver",
        "repair_controller",
        "output",
    ]


def test_qa_c3_limit_number_skips_literal_resolution_and_deterministic_f6_generates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "统计服务使用的隧道源节点所在位置的网元数量，按数量降序排列，返回前3名。"

    def fake_decompose(raw_question: str) -> dict[str, Any]:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "original_question": raw_question,
            "intent_type": "top_n",
            "output_shape": "grouped_rows",
            "literal_candidates": [
                {"text": "3", "kind_hint": "number", "attached_to": "数量"}
            ],
            "literal_requests": [],
            "substantive_terms": [
                _decomp_term("服务", "path"),
                _decomp_term("使用", "path"),
                _decomp_term("隧道", "path"),
                _decomp_term("源节点", "path"),
                _decomp_term("位置", "group_by", attached_to="网元"),
                _decomp_term("网元", "projection"),
                _decomp_term("数量", "projection"),
                _decomp_term("降序", "order_by"),
                _decomp_term("前", "limit"),
                _decomp_term("3", "limit"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "coverage": _coverage_payload(
                ["服务", "使用", "隧道", "源节点", "位置", "网元", "数量", "降序", "前", "3"]
            ),
        }

    def fail_if_grounding_runs(*args: Any, **kwargs: Any) -> GroundedUnderstandingFailure:
        raise AssertionError("deterministic F6 should generate before grounded understanding")

    monkeypatch.setattr(pipeline_module, "_mock_decompose", fake_decompose)
    monkeypatch.setattr(
        pipeline_module,
        "_run_grounded_understanding_stage",
        fail_if_grounding_runs,
    )
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "false")
    get_settings.cache_clear()

    try:
        output = run_pipeline(
            question=question,
            qa_id="qa_c3e83dd7ad32",
            generation_run_id="run-qa_c3e83dd7ad32",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generated"
    assert output.dsl is not None
    assert output.dsl["bindings"]["edge_1"] == {"edge_name": "TUNNEL_SRC"}
    aggregate = output.dsl["operations"][2]
    assert aggregate["group_by"][0]["property"] == {"owner": "NetworkElement", "name": "location"}
    assert aggregate["measures"][0]["property"] == {"owner": "NetworkElement", "name": "id"}
    assert output.dsl["operations"][3] == {
        "op": "sort",
        "by": [{"source": "measure.network_element_count", "direction": "desc"}],
    }
    assert output.dsl["operations"][4] == {"op": "limit", "value": 3}
    literal_stage = _stage(output.trace, "literal_resolver")
    literal_input = literal_stage["input_ref"]["value"]
    assert literal_input["literal_requests"] == []
    assert literal_input["skipped_literal_candidates"] == [
        {"raw": "3", "slot": "limit", "reason": "slot=limit"}
    ]
    assert literal_stage["metrics"]["skipped_literal_candidate_count"] == 1
    deterministic_stage = _stage(output.trace, "deterministic_assembler")
    assert deterministic_stage["metrics"]["deterministic_hit"] is True


def test_structural_coverage_gate_stops_qa_c3_single_hop_before_compiler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "统计服务使用的隧道源节点所在位置的网元数量，按数量降序排列，返回前3名。"

    def fake_decompose(raw_question: str) -> dict[str, Any]:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "original_question": raw_question,
            "intent_type": "top_n",
            "output_shape": "grouped_rows",
            "literal_candidates": [],
            "literal_requests": [],
            "substantive_terms": [
                _decomp_term("服务", "path"),
                _decomp_term("使用", "path"),
                _decomp_term("隧道", "path"),
                _decomp_term("源节点", "path"),
                _decomp_term("位置", "group_by", attached_to="网元"),
                _decomp_term("网元", "projection"),
                _decomp_term("数量", "projection"),
                _decomp_term("降序", "order_by"),
                _decomp_term("前", "limit"),
                _decomp_term("3", "limit"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "coverage": _coverage_payload(
                ["服务", "使用", "隧道", "源节点", "位置", "网元", "数量", "降序", "前", "3"]
            ),
        }

    def bad_grounding(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "query_shape": "single_hop",
            "selected_vertices": ["Tunnel", "NetworkElement"],
            "selected_edges": ["TUNNEL_SRC"],
            "selected_properties": [{"owner": "NetworkElement", "name": "id"}],
            "projection": [
                {
                    "semantic_type": "property",
                    "owner": "NetworkElement",
                    "name": "id",
                    "alias": "network_element_id",
                    "projection_terms": ["网元", "数量"],
                }
            ],
        }

    monkeypatch.setattr(pipeline_module, "_mock_decompose", fake_decompose)
    monkeypatch.setattr(pipeline_module, "_run_grounded_understanding_stage", bad_grounding)
    monkeypatch.setattr(
        pipeline_module,
        "_deterministic_assembler_payload",
        lambda **kwargs: {
            "schema_version": "deterministic_assembler_result_v1",
            "shape_status": "resolved",
            "shape": "F6 path_group_topn",
            "shape_candidates": ["F6 path_group_topn"],
            "success": False,
            "fallback_reason": "test_forces_grounded_path",
        },
    )
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "false")
    get_settings.cache_clear()

    try:
        output = run_pipeline(
            question=question,
            qa_id="qa_c3e83dd7ad32",
            generation_run_id="run-qa_c3e83dd7ad32-structural-gate",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generation_failed"
    assert output.failure is not None
    assert output.failure.reason == "coverage_failure"
    assert "dsl_parser" in _stage_names(output.trace)
    assert "cypher_compiler" not in _stage_names(output.trace)
    gate_stage = _stage(output.trace, "dsl_structural_coverage_gate")
    assert gate_stage["status"] == "failed"
    gate_output = gate_stage["output_ref"]["value"]
    assert gate_output["coverage_result"]["is_valid"] is False
    assert [item["code"] for item in gate_output["coverage_result"]["missing"]] == [
        "aggregate_required",
        "group_by_required",
        "order_by_required",
        "limit_required",
        "path_hops_insufficient",
    ]
    assert "repair_controller" not in _stage_names(output.trace)


def test_structural_coverage_gate_does_not_reground_repairable_missing_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "按状态统计端口数量"

    class FakeStructuredClient:
        provider = "openai_compatible"

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self.responses = [
                {
                    "schema_version": "question_decomposition_v1",
                    "result_type": "decomposition",
                    "intent_type": "aggregate",
                    "original_question": question,
                    "literal_candidates": [],
                    "substantive_terms": [
                        _decomp_term("状态", "group_by", attached_to="端口"),
                        _decomp_term("端口", "projection"),
                        _decomp_term("数量", "projection"),
                    ],
                    "modality_terms": [],
                    "time_terms": [],
                    "unparsed_terms": [],
                    "output_shape": "grouped_rows",
                },
                {
                    "schema_version": "grounded_understanding_v1",
                    "status": "grounded",
                    "query_shape": "vertex_lookup",
                    "selected_bindings": [
                        _grounded_binding("target_vertex", "vertex", "Port"),
                        _grounded_binding(
                            "projection_property",
                            "property",
                            "Port.id",
                            semantic_name="id",
                            owner="Port",
                        ),
                    ],
                    "selected_literals": [],
                    "filters": [],
                    "projection": [
                        {"semantic_type": "property", "owner": "Port", "name": "id", "alias": "port_id"}
                    ],
                    "coverage": _coverage_payload(["状态", "端口", "数量"]),
                    "unsupported": None,
                    "confidence": 0.86,
                },
                {
                    "schema_version": "grounded_understanding_v1",
                    "status": "grounded",
                    "query_shape": "ad_hoc_aggregate",
                    "selected_bindings": [
                        _grounded_binding("target_vertex", "vertex", "Port"),
                        _grounded_binding(
                            "group_property",
                            "property",
                            "Port.status",
                            semantic_name="status",
                            owner="Port",
                        ),
                        _grounded_binding(
                            "measure_property",
                            "property",
                            "Port.id",
                            semantic_name="id",
                            owner="Port",
                        ),
                    ],
                    "selected_literals": [],
                    "filters": [],
                    "group_by": [
                        {
                            "alias": "status",
                            "target": "port",
                            "property": {"owner": "Port", "name": "status"},
                        }
                    ],
                    "measures": [
                        {
                            "alias": "port_count",
                            "function": "count",
                            "target": "port",
                            "property": {"owner": "Port", "name": "id"},
                        }
                    ],
                    "projection": [
                        {"alias": "status", "source": "group.status"},
                        {"alias": "port_count", "source": "measure.port_count"},
                    ],
                    "coverage": _coverage_payload(["状态", "端口", "数量"]),
                    "unsupported": None,
                    "confidence": 0.94,
                },
            ]

        def generate_structured(
            self,
            *,
            prompt: str,
            schema_name: str,
            schema: dict[str, Any],
            attempt: int,
        ) -> dict[str, Any]:
            self.calls.append(
                {
                    "prompt": prompt,
                    "schema_name": schema_name,
                    "schema": schema,
                    "attempt": attempt,
                }
            )
            return self.responses.pop(0)

    fake_client = FakeStructuredClient()
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(
        pipeline_module,
        "_structured_llm_client_from_settings",
        lambda settings: fake_client,
    )
    monkeypatch.setattr(
        pipeline_module,
        "_deterministic_grounding_from_slots",
        lambda **kwargs: None,
    )

    try:
        output = run_pipeline(
            question=question,
            qa_id="structural-repair-loop",
            generation_run_id="run-structural-repair-loop",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generation_failed"
    assert output.failure is not None
    assert output.failure.reason == "coverage_failure"
    assert [call["schema_name"] for call in fake_client.calls] == [
        "question_decomposition_v1",
        "grounded_understanding_v1",
    ]
    assert _stage_names(output.trace).count("dsl_structural_coverage_gate") == 1
    assert _stage(output.trace, "dsl_structural_coverage_gate")["status"] == "failed"
    assert "repair_controller" not in _stage_names(output.trace)


def test_llm_fallback_selector_is_single_shot_without_repair_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeGroundedSelector:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def select(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "schema_version": "grounded_understanding_v1",
                "status": "grounded",
                "query_shape": "ad_hoc_aggregate",
                "selected_bindings": [],
                "selected_literals": [],
                "filters": [],
                "group_by": [{"property": {"owner": "NetworkElement", "name": "location"}}],
                "measures": [{"function": "count", "target": "network_element"}],
                "sort": [{"source": "cnt", "direction": "desc"}],
                "limit": 3,
                "projection": [{"alias": "location", "source": "group.location"}],
                "coverage": _coverage_payload(["服务", "隧道", "位置", "数量", "降序", "前", "3"]),
                "unsupported": None,
                "confidence": 0.9,
            }

    monkeypatch.setattr(pipeline_module, "GroundedUnderstandingSelector", FakeGroundedSelector)

    result = pipeline_module._select_grounded_understanding(
        decomposition={
            "schema_version": "question_decomposition_v1",
            "original_question": "统计服务使用的隧道源节点所在位置的网元数量，按数量降序排列，返回前3名。",
            "intent_type": "top_n",
            "substantive_terms": [],
            "structural_requirements": {
                "schema_version": "structural_requirements_v1",
                "requires_aggregate": True,
                "requires_group_by": True,
                "requires_order_by": True,
                "order_direction": "desc",
                "requires_limit": {"required": True, "value": 3},
                "path_terms": [],
                "path_order_confidence": "high",
                "min_path_hops": 2,
                "projection_terms": ["位置", "数量"],
            },
        },
        retrieval_result=CandidateRetrievalResult(candidates=[]),
        literal_results=[],
        settings=SimpleNamespace(llm_max_schema_retries=0),
        llm_client=object(),
        registry=None,
    )

    assert calls
    assert calls[0].get("repair_context") is None
    assert result["_grounding_decision"]["grounding_source"] == "llm"
    assert result["_grounding_decision"]["deterministic_decision"] == "not_applicable"
    assert result["_grounding_decision"]["fallback_mode"] == "single_shot"


def test_single_shot_fallback_failure_preserves_grounded_reason_without_binder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeGroundedSelector:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def select(self, **_kwargs: Any) -> GroundedUnderstandingFailure:
            return GroundedUnderstandingFailure(
                status="generation_failed",
                reason="grounded_understanding_schema_invalid",
                message="LLM output did not satisfy grounded_understanding_v1.",
                provider="fake-grounded-llm",
                error_type="ValidationError",
                attempts=1,
                retry_count=0,
            )

    monkeypatch.setattr(pipeline_module, "GroundedUnderstandingSelector", FakeGroundedSelector)

    result = pipeline_module._select_grounded_understanding(
        decomposition={
            "schema_version": "question_decomposition_v1",
            "original_question": "查询服务使用的隧道名称",
            "intent_type": "list",
            "substantive_terms": [],
        },
        retrieval_result=CandidateRetrievalResult(candidates=[]),
        literal_results=[],
        settings=SimpleNamespace(llm_max_schema_retries=0),
        llm_client=object(),
        registry=None,
    )

    assert isinstance(result, GroundedUnderstandingFailure)
    trace = pipeline_module.GraphTraceBuilder(
        trace_id="trace-grounded-schema-invalid",
        question_id="grounded-schema-invalid",
        generation_run_id="run-grounded-schema-invalid",
        source_question="查询服务使用的隧道名称",
    )
    output = pipeline_module._output_from_grounded_outcome(trace, result)
    assert output is not None
    assert output.status == "generation_failed"
    assert output.failure is not None
    assert output.failure.reason == "grounded_understanding_schema_invalid"


def test_structural_limit_literal_candidate_is_skipped_while_filter_literal_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decompose(question: str) -> dict[str, Any]:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "original_question": question,
            "intent_type": "list",
            "output_shape": "rows",
            "literal_candidates": [
                {"text": "Gold", "kind_hint": "enum_or_name", "attached_to": "服务"},
                {"text": "3", "kind_hint": "number", "attached_to": "数量"},
            ],
            "literal_requests": [],
            "substantive_terms": [
                _decomp_term("Gold", "filter", attached_to="服务"),
                _decomp_term("服务", "path"),
                _decomp_term("使用", "path"),
                _decomp_term("隧道", "projection"),
                _decomp_term("前", "limit"),
                _decomp_term("3", "limit"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "coverage": _coverage_payload(["Gold", "服务", "使用", "隧道", "前", "3"]),
            "mock_intent": "gold_service_tunnels",
        }

    monkeypatch.setattr(pipeline_module, "_mock_decompose", fake_decompose)
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "false")
    get_settings.cache_clear()

    try:
        output = run_pipeline(
            question="Gold 服务使用了哪些隧道，返回前3名",
            qa_id="limit-literal-skip",
            generation_run_id="run-limit-literal-skip",
        )
    finally:
        get_settings.cache_clear()

    assert output.status != "clarification_required"
    literal_stage = _stage(output.trace, "literal_resolver")
    literal_input = literal_stage["input_ref"]["value"]
    assert [request["raw_literal"] for request in literal_input["literal_requests"]] == ["Gold"]
    assert literal_input["skipped_literal_candidates"] == [
        {"raw": "3", "slot": "limit", "reason": "slot=limit"}
    ]
    literal_output = literal_stage["output_ref"]["value"]
    assert literal_output[0]["raw_literal"] == "Gold"
    assert literal_output[0]["resolved"] is True
    assert literal_stage["metrics"]["literal_count"] == 1
    assert literal_stage["metrics"]["skipped_literal_candidate_count"] == 1


def test_filter_numeric_literal_candidate_still_becomes_literal_request() -> None:
    retrieval_result = CandidateRetrievalResult(
        candidates=[
            SemanticCandidate(
                semantic_type="vertex",
                semantic_id="Tunnel",
                semantic_name="Tunnel",
                score=0.9,
                match_type="synonym",
                evidence=[CandidateEvidence(term="隧道", source="synonym", matched_text="隧道")],
            ),
            SemanticCandidate(
                semantic_type="property",
                semantic_id="Tunnel.bandwidth",
                semantic_name="bandwidth",
                owner="Tunnel",
                score=0.9,
                match_type="synonym",
                evidence=[CandidateEvidence(term="带宽", source="synonym", matched_text="带宽")],
            ),
        ]
    )
    decomposition = {
        "schema_version": "question_decomposition_v1",
        "original_question": "带宽为3的隧道",
        "literal_candidates": [
            {"text": "3", "kind_hint": "number", "attached_to": "带宽"}
        ],
        "literal_requests": [],
        "substantive_terms": [
            _decomp_term("带宽", "filter", attached_to="隧道"),
            _decomp_term("3", "filter", attached_to="带宽"),
            _decomp_term("隧道", "projection"),
        ],
    }

    enriched = pipeline_module._with_literal_requests_from_candidates(
        decomposition,
        retrieval_result,
    )

    assert enriched["literal_requests"] == [
        {
            "raw_literal": "3",
            "expected_vertex": "Tunnel",
            "expected_property": "bandwidth",
            "literal_kind_hint": "numeric",
        }
    ]
    assert enriched.get("skipped_literal_candidates") == []


def test_self_validation_failure_records_self_validation_stage_without_final_cypher() -> None:
    output = run_pipeline(
        question="隧道 tun-mpls-001 经过哪些设备信息",
        qa_id="self-validation-failure",
        generation_run_id="run-self-validation-failure",
        _path_pattern_template_overrides_for_tests={
            "tunnel_full_path": (
                "MATCH (t:Tunnel {id: $tunnel_id})-[p:PATH_THROUGH]->(ne:NetworkElement)\n"
                "SET ne.name = 'bad'\n"
                "RETURN ne AS device, p.hop_order AS hop"
            )
        },
    )

    assert output.status == "generation_failed"
    assert output.cypher is None
    assert output.dsl is None
    assert output.failure is not None
    assert output.failure.reason == "cypher_readonly_violation"
    assert _stage_names(output.trace)[-2:] == ["cypher_self_validation", "output"]
    assert "repair_controller" not in _stage_names(output.trace)
    self_validation_stage = _stage(output.trace, "cypher_self_validation")
    assert self_validation_stage["status"] == "failed"
    assert self_validation_stage["output_ref"]["value"]["valid"] is False


def test_path_pattern_shape_mismatch_is_reported_by_self_validation_stage() -> None:
    output = run_pipeline(
        question="隧道 tun-mpls-001 经过哪些设备信息",
        qa_id="shape-mismatch",
        generation_run_id="run-shape-mismatch",
        _path_pattern_template_overrides_for_tests={
            "tunnel_full_path": (
                "MATCH (t:Tunnel {id: $tunnel_id})-[p:PATH_THROUGH]->(ne:NetworkElement)\n"
                "RETURN ne AS wrong_device, p.hop_order AS hop"
            )
        },
    )

    assert output.status == "generation_failed"
    assert output.cypher is None
    assert output.dsl is None
    assert output.failure is not None
    assert output.failure.reason == "compiler_shape_mismatch"
    assert _stage_names(output.trace)[-2:] == ["cypher_self_validation", "output"]
    assert "repair_controller" not in _stage_names(output.trace)
    self_validation_stage = _stage(output.trace, "cypher_self_validation")
    assert self_validation_stage["status"] == "failed"
    assert self_validation_stage["errors"][0]["code"] == "compiler_shape_mismatch"


def test_dsl_parser_failure_is_recorded_in_dsl_parser_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_dsl(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "schema_version": "restricted_query_dsl_v1",
            "query_id": "invalid-dsl",
            "query_shape": "single_hop_traversal",
            "bindings": {},
            "operations": [],
            "projection": {"items": []},
        }

    monkeypatch.setattr(pipeline_module.RestrictedDslBuilder, "build", invalid_dsl)
    _force_grounded_path(monkeypatch)

    output = run_pipeline(
        question="Gold 服务使用了哪些隧道ID",
        qa_id="invalid-dsl",
        generation_run_id="run-invalid-dsl",
    )

    assert output.status == "generation_failed"
    assert output.failure is not None
    assert output.failure.reason == "compiler_shape_mismatch"
    assert _stage_names(output.trace)[-2:] == ["dsl_parser", "output"]
    parser_stage = output.trace["stages"][-2]
    assert parser_stage["status"] == "failed"
    assert parser_stage["errors"][0]["type"] == "RestrictedDslValidationError"


def test_model_loader_failure_returns_service_failure_envelope(tmp_path) -> None:
    output = run_pipeline(
        question="Gold 服务使用了哪些隧道ID",
        qa_id="model-loader-failure",
        generation_run_id="run-model-loader-failure",
        _model_path=tmp_path / "missing-model.yaml",
    )

    assert output.status == "service_failed"
    assert output.cypher is None
    assert output.dsl is None
    assert output.failure is not None
    assert output.failure.reason == "knowledge_context_unavailable"
    assert _stage_names(output.trace) == ["graph_model_loader", "output"]
    assert output.trace["stages"][0]["status"] == "failed"


def test_decomposer_clarification_outcome_short_circuits_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def clarification_decompose(question: str) -> QuestionDecompositionClarification:
        return QuestionDecompositionClarification(
            original_question=question,
            clarification=ClarificationRequest(question="请说明“它”指的是哪个设备或服务。"),
            missing_referents=["它"],
        )

    monkeypatch.setattr(pipeline_module, "_mock_decompose", clarification_decompose)

    output = run_pipeline(
        question="请进一步说明查询对象",
        qa_id="decomposer-clarification",
        generation_run_id="run-decomposer-clarification",
    )

    assert output.status == "clarification_required"
    assert output.cypher is None
    assert output.dsl is None
    assert output.clarification is not None
    assert output.clarification.question == "请说明“它”指的是哪个设备或服务。"
    assert _stage_names(output.trace) == ["graph_model_loader", "input_clarification_gate", "question_decomposer", "output"]


def test_decomposer_failure_outcome_short_circuits_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_decompose(question: str) -> QuestionDecompositionFailure:
        return QuestionDecompositionFailure(
            status="service_failed",
            reason="model_invocation_failed",
            message="provider timeout",
            provider="fake-llm",
            error_type="TimeoutError",
            attempts=1,
            retry_count=0,
        )

    monkeypatch.setattr(pipeline_module, "_mock_decompose", failed_decompose)

    output = run_pipeline(
        question="Gold 服务使用了哪些隧道ID",
        qa_id="decomposer-failure",
        generation_run_id="run-decomposer-failure",
    )

    assert output.status == "service_failed"
    assert output.failure is not None
    assert output.failure.reason == "model_invocation_failed"
    assert _stage_names(output.trace) == ["graph_model_loader", "input_clarification_gate", "question_decomposer", "output"]


def test_grounded_understanding_schema_output_is_converted_before_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def grounded_schema_understanding(
        decomposition: dict[str, object],
        literal_results: list[object],
    ) -> dict[str, object]:
        return {
            "schema_version": "grounded_understanding_v1",
            "status": "grounded",
            "query_shape": "single_hop",
            "selected_bindings": [
                _grounded_binding("source", "vertex", "Service"),
                _grounded_binding("target", "vertex", "Tunnel"),
                _grounded_binding("relation", "edge", "SERVICE_USES_TUNNEL", direction="forward"),
                _grounded_binding(
                    "filter_property",
                    "property",
                    "Service.quality_of_service",
                    semantic_name="quality_of_service",
                    owner="Service",
                ),
            ],
            "selected_literals": [
                result.model_dump(mode="json")
                for result in literal_results
            ],
            "filters": [
                {
                    "owner": "Service",
                    "property": "quality_of_service",
                    "operator": "=",
                    "raw_literal": "Gold",
                }
            ],
            "projection": [
                {"semantic_type": "property", "owner": "Tunnel", "name": "id", "alias": "tunnel_id"}
            ],
            "coverage": {
                "substantive_terms": {
                    "total": 4,
                    "covered": 4,
                    "uncovered": [],
                }
            },
            "unsupported": None,
            "confidence": 0.93,
        }

    monkeypatch.setattr(pipeline_module, "_mock_understand", grounded_schema_understanding)
    _force_grounded_path(monkeypatch)

    output = run_pipeline(
        question="Gold 服务使用了哪些隧道ID",
        qa_id="grounded-schema",
        generation_run_id="run-grounded-schema",
    )

    assert output.status == "generated"
    assert output.cypher is not None
    assert "SERVICE_USES_TUNNEL" in output.cypher
    assert _stage_names(output.trace) == EXPECTED_STAGES


def test_grounded_understanding_failure_outcome_stops_before_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_grounding(
        decomposition: dict[str, object],
        literal_results: list[object],
    ) -> GroundedUnderstandingFailure:
        return GroundedUnderstandingFailure(
            status="generation_failed",
            reason="semantic_match_rejected",
            message="candidate_id edge:USES_TUNNEL is not present in candidate set",
            provider="fake-grounded-llm",
            error_type="CandidateBoundaryError",
            attempts=1,
            retry_count=0,
        )

    monkeypatch.setattr(pipeline_module, "_mock_understand", failed_grounding)
    _force_grounded_path(monkeypatch)

    output = run_pipeline(
        question="Gold 服务使用了哪些隧道ID",
        qa_id="grounded-failure",
        generation_run_id="run-grounded-failure",
    )

    assert output.status == "generation_failed"
    assert output.failure is not None
    assert output.failure.reason == "semantic_match_rejected"
    assert _stage_names(output.trace)[-2:] == ["grounded_understanding", "output"]


def test_f1_vertex_projection_uses_deterministic_main_path_without_llm() -> None:
    output = run_pipeline(
        question="查询所有服务的 ID、名称、元素类型、服务质量等级、带宽和时延",
        qa_id="mir010-f1",
        generation_run_id="run-mir010-f1",
    )

    assert output.status == "generated"
    assert output.dsl is not None
    assert output.dsl["query_shape"] == "vertex_lookup"
    assert "deterministic_assembler" in _stage_names(output.trace)
    assert "grounded_understanding" not in _stage_names(output.trace)
    assert _total_llm_call_count(output.trace) == 0
    assert [item["property"]["name"] for item in output.dsl["projection"]["items"]] == [
        "id",
        "name",
        "elem_type",
        "quality_of_service",
        "bandwidth",
        "latency",
    ]


def test_f2_vertex_filter_uses_deterministic_main_path_without_llm() -> None:
    output = run_pipeline(
        question="查询服务质量等级为金牌的服务ID、名称和带宽",
        qa_id="mir010-f2",
        generation_run_id="run-mir010-f2",
    )

    assert output.status == "generated"
    assert output.dsl is not None
    assert output.dsl["query_shape"] == "vertex_lookup"
    assert "deterministic_assembler" in _stage_names(output.trace)
    assert "grounded_understanding" not in _stage_names(output.trace)
    assert _total_llm_call_count(output.trace) == 0
    assert output.dsl["filters"][0]["property"] == {"owner": "Service", "name": "quality_of_service"}
    assert output.dsl["filters"][0]["value"]["normalized"] == "Gold"


def test_f3_vertex_count_uses_deterministic_main_path_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decompose(question: str) -> dict[str, Any]:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "original_question": question,
            "intent_type": "count",
            "output_shape": "scalar",
            "literal_candidates": [],
            "literal_requests": [],
            "substantive_terms": [
                _decomp_term("统计", "projection"),
                _decomp_term("服务", "projection"),
                _decomp_term("数量", "projection"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "coverage": _coverage_payload(["统计", "服务", "数量"]),
        }

    monkeypatch.setattr(pipeline_module, "_mock_decompose", fake_decompose)

    output = run_pipeline(
        question="统计服务数量",
        qa_id="mir010-f3",
        generation_run_id="run-mir010-f3",
    )

    assert output.status == "generated"
    assert output.dsl is not None
    assert output.dsl["query_shape"] == "ad_hoc_aggregate"
    assert "deterministic_assembler" in _stage_names(output.trace)
    assert "grounded_understanding" not in _stage_names(output.trace)
    assert _total_llm_call_count(output.trace) == 0
    aggregate = output.dsl["operations"][0]
    assert aggregate["op"] == "aggregate"
    assert aggregate["measures"][0]["function"] == "count"
    assert aggregate["measures"][0]["property"] == {"owner": "Service", "name": "id"}


def test_f4_path_projection_uses_deterministic_multihop_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decompose(question: str) -> dict[str, Any]:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "intent_type": "list",
            "output_shape": "rows",
            "original_question": question,
            "literal_candidates": [],
            "literal_requests": [],
            "substantive_terms": [
                _decomp_term("服务", "path"),
                _decomp_term("使用隧道", "path"),
                _decomp_term("隧道", "path"),
                _decomp_term("ID", "projection", attached_to="隧道"),
                _decomp_term("名称", "projection", attached_to="隧道"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "coverage": _coverage_payload(["服务", "使用隧道", "隧道", "ID", "名称"]),
        }

    monkeypatch.setattr(pipeline_module, "_mock_decompose", fake_decompose)

    output = run_pipeline(
        question="查询服务使用隧道的 ID 和名称",
        qa_id="mir010-f4",
        generation_run_id="run-mir010-f4",
    )

    assert output.status == "generated"
    assert output.dsl is not None
    assert output.dsl["query_shape"] == "single_hop_traversal"
    assert "deterministic_assembler" in _stage_names(output.trace)
    assert "grounded_understanding" not in _stage_names(output.trace)
    assert _total_llm_call_count(output.trace) == 0
    assert output.dsl["bindings"] == {
        "v0": {"vertex_name": "Service"},
        "edge_0": {"edge_name": "SERVICE_USES_TUNNEL"},
        "v1": {"vertex_name": "Tunnel"},
    }
    assert [item["property"]["name"] for item in output.dsl["projection"]["items"]] == [
        "id",
        "name",
    ]


def test_f4_path_projection_does_not_default_to_id_when_explicit_terms_are_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decompose(question: str) -> dict[str, Any]:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "intent_type": "list",
            "output_shape": "rows",
            "original_question": question,
            "literal_candidates": [],
            "literal_requests": [],
            "substantive_terms": [
                _decomp_term("业务", "path"),
                _decomp_term("使用", "path"),
                _decomp_term("隧道", "path"),
                _decomp_term("ID", "projection"),
                _decomp_term("名称", "projection"),
                _decomp_term("带宽", "projection"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "coverage": _coverage_payload(["业务", "使用", "隧道", "ID", "名称", "带宽"]),
        }

    monkeypatch.setattr(pipeline_module, "_mock_decompose", fake_decompose)

    output = run_pipeline(
        question="查询所有业务使用的隧道的ID、名称和带宽。",
        qa_id="qa_65f6a2d6ec7a",
        generation_run_id="run-qa_65f6a2d6ec7a",
    )

    assembler_output = _stage(output.trace, "deterministic_assembler")["output_ref"]["value"]
    assert assembler_output["success"] is False
    assert assembler_output["fallback_reason"] == "unresolved_projection_terms"
    if output.cypher is not None:
        assert "RETURN tun.id AS tunnel_id" not in output.cypher


def test_f4_two_hop_path_projection_uses_deterministic_multihop_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decompose(question: str) -> dict[str, Any]:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "intent_type": "list",
            "output_shape": "rows",
            "original_question": question,
            "literal_candidates": [],
            "literal_requests": [],
            "substantive_terms": [
                _decomp_term("服务", "path"),
                _decomp_term("使用", "path"),
                _decomp_term("隧道", "path"),
                _decomp_term("目的", "path"),
                _decomp_term("网元", "path"),
                _decomp_term("厂商名称", "projection", attached_to="网元"),
                _decomp_term("带宽", "projection", attached_to="隧道"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "coverage": _coverage_payload(["服务", "使用", "隧道", "目的", "网元", "厂商名称", "带宽"]),
        }

    monkeypatch.setattr(pipeline_module, "_mock_decompose", fake_decompose)

    output = run_pipeline(
        question="查询服务使用的隧道及其目的网元，返回厂商名称和隧道带宽。",
        qa_id="mir010-f4-two-hop",
        generation_run_id="run-mir010-f4-two-hop",
    )

    assert output.status == "generated"
    assert output.dsl is not None
    assert output.dsl["query_shape"] == "single_hop_traversal"
    assert "grounded_understanding" not in _stage_names(output.trace)
    assert _total_llm_call_count(output.trace) == 0
    assert [operation["edge"] for operation in output.dsl["operations"]] == ["edge_0", "edge_1"]
    assert "MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun:Tunnel)-[:TUNNEL_DST]->(ne:NetworkElement)" in output.cypher
    assert "ne.vendor AS network_element_vendor" in output.cypher
    assert "tun.bandwidth AS tunnel_bandwidth" in output.cypher


def test_f6_path_group_topn_uses_deterministic_multihop_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decompose(question: str) -> dict[str, Any]:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "intent_type": "top_n",
            "output_shape": "rows",
            "original_question": question,
            "literal_candidates": [],
            "literal_requests": [],
            "substantive_terms": [
                _decomp_term("服务", "path"),
                _decomp_term("使用", "path"),
                _decomp_term("隧道", "path"),
                _decomp_term("隧道ID", "group_by", attached_to="隧道"),
                _decomp_term("服务数量", "projection", attached_to="服务"),
                _decomp_term("数量降序", "order_by"),
                _decomp_term("前3", "limit"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "coverage": _coverage_payload(["服务", "使用", "隧道", "隧道ID", "服务数量", "数量降序", "前3"]),
        }

    monkeypatch.setattr(pipeline_module, "_mock_decompose", fake_decompose)

    output = run_pipeline(
        question="按隧道ID统计使用该隧道的服务数量，按数量降序排列，返回前3名。",
        qa_id="mir010-f6",
        generation_run_id="run-mir010-f6",
    )

    assert output.status == "generated"
    assert output.dsl is not None
    assert output.dsl["query_shape"] == "top_n"
    assert "grounded_understanding" not in _stage_names(output.trace)
    assert _total_llm_call_count(output.trace) == 0
    assert "MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun:Tunnel)" in output.cypher
    assert "RETURN tun.id AS tunnel_id, count(svc.id) AS service_count" in output.cypher
    assert "ORDER BY service_count DESC" in output.cypher
    assert "LIMIT 3" in output.cypher


def test_single_shot_fallback_does_not_reground_after_structural_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStructuredClient:
        provider = "openai_compatible"

        def __init__(self) -> None:
            self.calls: list[str] = []
            self.responses = [
                {
                    "schema_version": "question_decomposition_v1",
                    "result_type": "decomposition",
                    "intent_type": "top_n",
                    "original_question": "统计服务使用的隧道源节点所在位置的网元数量，按数量降序排列，返回前3名。",
                    "literal_candidates": [],
                    "substantive_terms": [
                        _decomp_term("服务", "path"),
                        _decomp_term("使用", "path"),
                        _decomp_term("隧道", "path"),
                        _decomp_term("源节点", "path"),
                        _decomp_term("位置", "group_by", attached_to="网元"),
                        _decomp_term("网元数量", "projection"),
                        _decomp_term("数量", "order_by"),
                        _decomp_term("3", "limit"),
                    ],
                    "modality_terms": [],
                    "time_terms": [],
                    "unparsed_terms": [],
                    "output_shape": "rows",
                },
                {
                    "schema_version": "grounded_understanding_v1",
                    "status": "grounded",
                    "query_shape": "single_hop",
                    "selected_bindings": [
                        _grounded_binding("source", "vertex", "Tunnel"),
                        _grounded_binding("target", "vertex", "NetworkElement"),
                        _grounded_binding("relation", "edge", "TUNNEL_SRC", direction="forward"),
                    ],
                    "selected_literals": [],
                    "filters": [],
                    "projection": [
                        {
                            "semantic_type": "property",
                            "owner": "NetworkElement",
                            "name": "id",
                            "alias": "network_element_id",
                        }
                    ],
                    "coverage": _coverage_payload(["隧道", "源节点", "网元数量"]),
                    "unsupported": None,
                    "confidence": 0.75,
                },
            ]

        def generate_structured(
            self,
            *,
            prompt: str,
            schema_name: str,
            schema: dict[str, Any],
            attempt: int,
        ) -> dict[str, Any]:
            self.calls.append(schema_name)
            if not self.responses:
                raise AssertionError("fallback must be single-shot and must not call LLM again")
            return self.responses.pop(0)

    fake_client = FakeStructuredClient()
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_API_KEY", "test-key")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_MODEL", "qwen3-32b")
    get_settings.cache_clear()
    monkeypatch.setattr(
        pipeline_module,
        "_structured_llm_client_from_settings",
        lambda settings: fake_client,
    )

    try:
        output = run_pipeline(
            question="统计服务使用的隧道源节点所在位置的网元数量，按数量降序排列，返回前3名。",
            qa_id="mir010-fallback-single-shot",
            generation_run_id="run-mir010-fallback-single-shot",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generation_failed"
    assert output.failure is not None
    assert output.failure.reason == "coverage_failure"
    assert fake_client.calls == ["question_decomposition_v1", "grounded_understanding_v1"]
    assert _stage_names(output.trace).count("grounded_understanding") == 1
    assert _stage_names(output.trace).count("repair_controller") == 0
    grounded_stage = _stage(output.trace, "grounded_understanding")
    assert grounded_stage["metrics"]["llm_call_count"] == 1


def test_single_shot_fallback_failed_status_concedes_with_specific_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStructuredClient:
        provider = "openai_compatible"

        def __init__(self) -> None:
            self.calls: list[str] = []
            self.responses = [
                {
                    "schema_version": "question_decomposition_v1",
                    "result_type": "decomposition",
                    "intent_type": "list",
                    "original_question": "查询服务绕行策略",
                    "literal_candidates": [],
                    "substantive_terms": [
                        _decomp_term("服务", "path"),
                        _decomp_term("绕行策略", "path"),
                    ],
                    "modality_terms": [],
                    "time_terms": [],
                    "unparsed_terms": [],
                    "output_shape": "rows",
                },
                {
                    "schema_version": "grounded_understanding_v1",
                    "status": "failed",
                    "query_shape": "unsupported",
                    "selected_bindings": [],
                    "ambiguities": [
                        {
                            "role": "path",
                            "reason": "没有可用候选能表达绕行策略路径",
                            "candidate_ids": ["vertex:Service", "property:Service.id"],
                        }
                    ],
                },
            ]

        def generate_structured(
            self,
            *,
            prompt: str,
            schema_name: str,
            schema: dict[str, Any],
            attempt: int,
        ) -> dict[str, Any]:
            self.calls.append(schema_name)
            if not self.responses:
                raise AssertionError("single-shot fallback must not retry")
            return self.responses.pop(0)

    fake_client = FakeStructuredClient()
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_API_KEY", "test-key")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_MODEL", "qwen3-32b")
    get_settings.cache_clear()
    monkeypatch.setattr(
        pipeline_module,
        "_structured_llm_client_from_settings",
        lambda settings: fake_client,
    )

    try:
        output = run_pipeline(
            question="查询服务绕行策略",
            qa_id="mir010-fallback-concede",
            generation_run_id="run-mir010-fallback-concede",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "generation_failed"
    assert output.failure is not None
    assert output.failure.reason == "single_shot_fallback_failed"
    assert "没有可用候选能表达绕行策略路径" in (output.failure.message or "")
    assert fake_client.calls == ["question_decomposition_v1", "grounded_understanding_v1"]
    assert _stage_names(output.trace).count("grounded_understanding") == 1


def test_single_shot_fallback_clarification_uses_ambiguity_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStructuredClient:
        provider = "openai_compatible"

        def __init__(self) -> None:
            self.calls: list[str] = []
            self.responses = [
                {
                    "schema_version": "question_decomposition_v1",
                    "result_type": "decomposition",
                    "intent_type": "list",
                    "original_question": "查询服务关联对象",
                    "literal_candidates": [],
                    "substantive_terms": [
                        _decomp_term("服务", "path"),
                        _decomp_term("关联对象", "path"),
                    ],
                    "modality_terms": [],
                    "time_terms": [],
                    "unparsed_terms": [],
                    "output_shape": "rows",
                },
                {
                    "schema_version": "grounded_understanding_v1",
                    "status": "clarification_required",
                    "query_shape": "unsupported",
                    "selected_bindings": [],
                    "ambiguities": [
                            {
                                "role": "path",
                                "reason": "关联对象可能指隧道或端口",
                                "candidate_ids": ["vertex:Service", "property:Service.id"],
                            }
                        ],
                    },
            ]

        def generate_structured(
            self,
            *,
            prompt: str,
            schema_name: str,
            schema: dict[str, Any],
            attempt: int,
        ) -> dict[str, Any]:
            self.calls.append(schema_name)
            if not self.responses:
                raise AssertionError("single-shot fallback must not retry")
            return self.responses.pop(0)

    fake_client = FakeStructuredClient()
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_API_KEY", "test-key")
    monkeypatch.setenv("CYPHER_GENERATOR_AGENT_LLM_MODEL", "qwen3-32b")
    get_settings.cache_clear()
    monkeypatch.setattr(
        pipeline_module,
        "_structured_llm_client_from_settings",
        lambda settings: fake_client,
    )

    try:
        output = run_pipeline(
            question="查询服务关联对象",
            qa_id="mir010-fallback-clarification",
            generation_run_id="run-mir010-fallback-clarification",
        )
    finally:
        get_settings.cache_clear()

    assert output.status == "clarification_required"
    assert output.clarification is not None
    assert "关联对象可能指隧道或端口" in output.clarification.question
    assert fake_client.calls == ["question_decomposition_v1", "grounded_understanding_v1"]
    assert _stage_names(output.trace).count("grounded_understanding") == 1


def test_old_multi_round_repair_reground_loop_is_not_present() -> None:
    source = inspect.getsource(pipeline_module._run_pipeline_steps)

    assert "while True" not in source
    assert "_can_reground_with_llm" not in source
    assert "repair_context=decision.repair_prompt_delta" not in source


@pytest.mark.asyncio
async def test_semantic_parse_api_uses_pipeline_for_happy_path() -> None:
    result = await parse_semantics(
        SemanticParseRequest(
            id="gq-001",
            question="Gold 服务使用了哪些隧道ID",
            generation_run_id="run-api-gq-001",
        )
    )

    assert result["status"] == "generated"
    assert "SERVICE_USES_TUNNEL" in result["cypher"]
    assert result["trace"]["final_status"] == "generated"
    assert _stage_names(result["trace"]) == EXPECTED_DETERMINISTIC_STAGES


def _stage_names(trace: dict[str, object]) -> list[str]:
    return [stage["stage"] for stage in trace["stages"]]


def _stage(trace: dict[str, object], stage_name: str) -> dict[str, Any]:
    for stage in trace["stages"]:
        if stage["stage"] == stage_name:
            return stage
    raise AssertionError(f"missing {stage_name} stage")


def _compiler_parameters(trace: dict[str, object]) -> dict[str, object]:
    for stage in trace["stages"]:
        if stage["stage"] == "cypher_compiler":
            return stage["output_ref"]["value"]["parameters"]
    raise AssertionError("missing cypher_compiler stage")


def _compiler_template(trace: dict[str, object]) -> str:
    for stage in trace["stages"]:
        if stage["stage"] == "cypher_compiler":
            return stage["output_ref"]["value"]["cypher_template"]
    raise AssertionError("missing cypher_compiler stage")


def _compiler_executable(trace: dict[str, object]) -> str:
    for stage in trace["stages"]:
        if stage["stage"] == "cypher_compiler":
            return stage["output_ref"]["value"]["cypher_executable"]
    raise AssertionError("missing cypher_compiler stage")


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for item in value.values():
            keys.update(_all_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_all_keys(item))
        return keys
    return set()


def _total_llm_call_count(trace: dict[str, object]) -> int:
    total = 0
    for stage in trace["stages"]:
        metrics = stage.get("metrics") or {}
        if isinstance(metrics, dict):
            total += int(metrics.get("llm_call_count") or 0)
    return total


def _grounded_binding(
    role: str,
    semantic_type: str,
    semantic_id: str,
    *,
    semantic_name: str | None = None,
    owner: str | None = None,
    direction: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "role": role,
        "semantic_type": semantic_type,
        "candidate_id": f"{semantic_type}:{semantic_id}",
        "semantic_id": semantic_id,
        "semantic_name": semantic_name or semantic_id,
        "confidence": 0.95,
    }
    if owner is not None:
        payload["owner"] = owner
    if direction is not None:
        payload["direction"] = direction
    return payload


def _decomp_term(text: str, slot: str, *, attached_to: str | None = None) -> dict[str, str]:
    payload = {"text": text, "slot": slot}
    if attached_to is not None:
        payload["attached_to"] = attached_to
    return payload


def _candidate(
    semantic_type: str,
    semantic_id: str,
    *,
    semantic_name: str | None = None,
    owner: str | None = None,
    evidence_terms: list[str] | None = None,
    valid_values: list[str] | None = None,
) -> SemanticCandidate:
    evidence_terms = evidence_terms or [semantic_name or semantic_id]
    return SemanticCandidate(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
        semantic_name=semantic_name or semantic_id,
        owner=owner,
        score=1.0,
        match_type="exact",
        evidence=[
            CandidateEvidence(term=term, source="test", matched_text=term)
            for term in evidence_terms
        ],
        metadata={"valid_values": valid_values or []},
    )


def _coverage_payload(covered: list[str]) -> dict[str, object]:
    return {
        "substantive_terms": {"total": len(covered), "covered": len(covered), "uncovered": []},
        "stopword_terms": {"ignored": []},
        "modality_terms": {"warning_only": []},
        "time_terms": {"covered": [], "unresolved": []},
        "unparsed_terms": {"unresolved": []},
    }


def _grounded_service_tunnel_payload(*, direction: str) -> dict[str, object]:
    return {
        "schema_version": "grounded_understanding_v1",
        "status": "grounded",
        "query_shape": "single_hop",
        "selected_bindings": [
            _grounded_binding("source", "vertex", "Service"),
            _grounded_binding("target", "vertex", "Tunnel"),
            _grounded_binding("relation", "edge", "SERVICE_USES_TUNNEL", direction=direction),
            _grounded_binding(
                "filter_property",
                "property",
                "Service.quality_of_service",
                semantic_name="quality_of_service",
                owner="Service",
            ),
        ],
        "selected_literals": [
            {
                "schema_version": "literal_resolver_result_v1",
                "raw_literal": "Gold",
                "resolved": True,
                "resolved_value": "Gold",
                "normalized_value": "Gold",
                "match_type": "exact",
                "confidence": 1.0,
                "expected_vertex": "Service",
                "expected_edge": None,
                "expected_property": "quality_of_service",
                "evidence": [
                    {"source": "property.valid_values", "matched": "Gold", "target": "Gold"}
                ],
                "alternatives": [],
                "requires_user_choice": False,
                "value_index_miss": False,
                "error_code": None,
            }
        ],
        "filters": [
            {
                "owner": "Service",
                "property": "quality_of_service",
                "operator": "=",
                "raw_literal": "Gold",
            }
        ],
        "projection": [
            {"semantic_type": "property", "owner": "Tunnel", "name": "id", "alias": "tunnel_id"}
        ],
        "coverage": {
            "substantive_terms": {
                "total": 4,
                "covered": 4,
                "uncovered": [],
            },
            "stopword_terms": {"ignored": []},
            "modality_terms": {"warning_only": []},
            "time_terms": {"covered": [], "unresolved": []},
            "unparsed_terms": {"unresolved": []},
        },
        "unsupported": None,
        "confidence": 0.93,
    }
