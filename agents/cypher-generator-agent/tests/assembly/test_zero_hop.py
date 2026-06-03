from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.cypher_generator_agent.app.assembly.zero_hop import ZeroHopAssembler
from services.cypher_generator_agent.app.binding.models import CandidateBinding
from services.cypher_generator_agent.app.core.pipeline import (
    _projection_items_from_substantive_terms,
    _with_literal_requests_from_candidates,
    _zero_hop_assembler_requirements,
    _zero_hop_candidates_for_assembler,
)
from services.cypher_generator_agent.app.compiler import compile_restricted_query_ast
from services.cypher_generator_agent.app.dsl.parser import parse_restricted_query_dsl
from services.cypher_generator_agent.app.assembly.taxonomy import QueryShape, ShapeStatus, classify_query_shape
from services.cypher_generator_agent.app.literals.models import LiteralResolverResult
from services.cypher_generator_agent.app.retrieval.models import CandidateRetrievalResult, SemanticCandidate
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry, load_graph_semantic_model
from services.cypher_generator_agent.app.validation.structural_requirements import StructuralRequirements


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "network_topology_graph_model.yaml"


@pytest.fixture(scope="module")
def registry() -> GraphSemanticRegistry:
    return load_graph_semantic_model(FIXTURE_PATH).registry


def test_f1_unique_vertex_and_projection_properties_builds_parseable_vertex_lookup(
    registry: GraphSemanticRegistry,
) -> None:
    result = ZeroHopAssembler(registry).assemble(
        "F1",
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("property", "Service.quality_of_service", owner="Service", semantic_name="quality_of_service"),
            _candidate("property", "Service.service_type", owner="Service", semantic_name="service_type"),
        ],
        structural_requirements={
            "projection": [
                {"property": "quality_of_service", "alias": "qos"},
                {"property": "service_type", "alias": "service_type"},
            ]
        },
    )

    assert result.success is True
    assert result.dsl is not None
    ast = parse_restricted_query_dsl(result.dsl, registry)
    assert ast.query_shape.value == "vertex_lookup"
    assert result.dsl["bindings"]["target"]["vertex_name"] == "Service"
    assert ast.projection.items[0].target.vertex_name == "Service"
    assert [(item.alias, item.property.owner, item.property.name) for item in ast.projection.items] == [
        ("qos", "Service", "quality_of_service"),
        ("service_type", "Service", "service_type"),
    ]


def test_f1_accepts_taxonomy_shape_value_and_mir006_projection_terms(
    registry: GraphSemanticRegistry,
) -> None:
    result = ZeroHopAssembler(registry).assemble(
        "F1 vertex_projection_0hop",
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("property", "Service.id", owner="Service", semantic_name="id"),
        ],
        structural_requirements=StructuralRequirements(projection_terms=["id"]).model_dump(mode="json"),
    )

    assert result.success is True
    assert result.dsl is not None
    ast = parse_restricted_query_dsl(result.dsl, registry)
    assert ast.projection.items[0].property.owner == "Service"
    assert ast.projection.items[0].property.name == "id"


def test_f1_object_info_projection_returns_full_vertex(
    registry: GraphSemanticRegistry,
) -> None:
    decomposition = {
        "original_question": "查询所有的服务信息。",
        "intent_type": "list",
        "output_shape": "rows",
        "substantive_terms": [{"text": "服务", "slot": "projection"}],
    }

    shape = classify_query_shape(
        {"requires_aggregate": False, "projection_terms": [], "path_terms": [], "min_path_hops": 0},
        decomposition,
    )

    assert shape.status == ShapeStatus.RESOLVED
    assert shape.shape == QueryShape.F1_VERTEX_PROJECTION_0HOP

    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "Service"),
            _semantic_candidate("property", "Service.id", owner="Service", semantic_name="id"),
        ],
    )
    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F1_VERTEX_PROJECTION_0HOP,
        decomposition=decomposition,
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["projection"] == [
        {"semantic_type": "vertex_full", "name": "Service", "alias": "service", "projection_terms": ["服务"]},
    ]

    result = ZeroHopAssembler(registry).assemble(
        "F1 vertex_projection_0hop",
        candidates=_zero_hop_candidates_for_assembler(retrieval.candidates),
        structural_requirements=requirements,
    )

    assert result.success is True
    assert result.dsl is not None
    ast = parse_restricted_query_dsl(result.dsl, registry)
    cypher = compile_restricted_query_ast(ast, registry).cypher
    assert cypher == "MATCH (svc:Service)\nRETURN svc AS service"


def test_f1_ambiguous_vertex_or_projection_owner_falls_back(registry: GraphSemanticRegistry) -> None:
    vertex_result = ZeroHopAssembler(registry).assemble(
        "F1",
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("vertex", "Tunnel"),
            _candidate("property", "Service.id", owner="Service", semantic_name="id"),
        ],
        structural_requirements={"projection": [{"property": "id"}]},
    )
    assert vertex_result.success is False
    assert vertex_result.dsl is None
    assert vertex_result.fallback_reason == "ambiguous_vertex_candidate"

    property_result = ZeroHopAssembler(registry).assemble(
        "F1",
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("property", "Service.id", owner="Service", semantic_name="id"),
            _candidate("property", "Tunnel.id", owner="Tunnel", semantic_name="id"),
        ],
        structural_requirements={"projection": [{"property": "id"}]},
    )
    assert property_result.success is False
    assert property_result.dsl is None
    assert property_result.fallback_reason == "ambiguous_projection_property"


def test_f2_unique_filter_property_and_literal_builds_parseable_filter(
    registry: GraphSemanticRegistry,
) -> None:
    result = ZeroHopAssembler(registry).assemble(
        "F2",
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("property", "Service.quality_of_service", owner="Service", semantic_name="quality_of_service"),
            _candidate("property", "Service.id", owner="Service", semantic_name="id"),
        ],
        structural_requirements={
            "filters": [{"property": "quality_of_service", "operator": "eq"}],
            "projection": [{"property": "id", "alias": "service_id"}],
        },
        literals=[
            {
                "property": "quality_of_service",
                "owner": "Service",
                "raw": "Gold",
                "normalized": "GOLD",
                "resolver_match_type": "value_synonym",
            }
        ],
    )

    assert result.success is True
    assert result.dsl is not None
    ast = parse_restricted_query_dsl(result.dsl, registry)
    assert ast.filters[0].target.alias == "target"
    assert ast.filters[0].property.owner == "Service"
    assert ast.filters[0].property.name == "quality_of_service"
    assert ast.filters[0].value.normalized == "GOLD"
    assert ast.projection.items[0].property.name == "id"


def test_f2_comparison_operator_from_closed_mapping_reaches_dsl(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "Tunnel"),
            _semantic_candidate("property", "Tunnel.bandwidth", owner="Tunnel", semantic_name="bandwidth"),
            _semantic_candidate("property", "Tunnel.id", owner="Tunnel", semantic_name="id"),
        ],
    )
    literal = LiteralResolverResult(
        raw_literal="100",
        resolved=True,
        resolved_value=100.0,
        normalized_value=100.0,
        match_type="literal_passthrough",
        confidence=1.0,
        expected_vertex="Tunnel",
        expected_property="bandwidth",
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F2_VERTEX_FILTER_0HOP,
        decomposition={
            "original_question": "查询带宽大于100的隧道ID。",
            "substantive_terms": [
                {"text": "带宽", "slot": "filter", "attached_to": "隧道"},
                {"text": "大于", "slot": "filter", "attached_to": "带宽"},
                {"text": "100", "slot": "filter", "attached_to": "带宽"},
                {"text": "ID", "slot": "projection", "attached_to": "隧道"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[literal],
        registry=registry,
    )

    result = ZeroHopAssembler(registry).assemble(
        "F2",
        candidates=_zero_hop_candidates_for_assembler(retrieval.candidates),
        structural_requirements=requirements,
        literals=[
            {
                "property": "bandwidth",
                "owner": "Tunnel",
                "raw": "100",
                "normalized": 100.0,
                "resolver_match_type": "literal_passthrough",
            }
        ],
    )

    assert result.success is True
    assert result.dsl is not None
    assert result.dsl["filters"][0]["operator"] == "gt"


def test_zero_hop_requirements_report_uncovered_projection_slot_before_assembly(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "Service"),
            _semantic_candidate("property", "Service.id", owner="Service", semantic_name="id"),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F1_VERTEX_PROJECTION_0HOP,
        decomposition={
            "original_question": "查询服务的ID和厂商。",
            "substantive_terms": [
                {"text": "服务", "slot": "path"},
                {"text": "ID", "slot": "projection", "attached_to": "服务"},
                {"text": "厂商", "slot": "projection", "attached_to": "服务"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["projection_uncovered_terms"] == ["服务.厂商"]


def test_f2_unknown_filter_operator_falls_back_before_dsl_boundary(
    registry: GraphSemanticRegistry,
) -> None:
    result = ZeroHopAssembler(registry).assemble(
        "F2",
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("property", "Service.quality_of_service", owner="Service", semantic_name="quality_of_service"),
            _candidate("property", "Service.id", owner="Service", semantic_name="id"),
        ],
        structural_requirements={
            "filters": [{"property": "quality_of_service", "operator": "__unsupported__"}],
            "projection": [{"property": "id", "alias": "service_id"}],
        },
        literals=[
            {
                "property": "quality_of_service",
                "owner": "Service",
                "raw": "100",
                "normalized": 100.0,
                "resolver_match_type": "literal_passthrough",
            }
        ],
    )

    assert result.success is False
    assert result.dsl is None
    assert result.fallback_reason == "unsupported_filter_operator"


def test_f2_covers_service_quality_projection_when_filter_uses_same_property(
    registry: GraphSemanticRegistry,
) -> None:
    result = ZeroHopAssembler(registry).assemble(
        "F2",
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("property", "Service.id", owner="Service", semantic_name="id"),
            _candidate("property", "Service.quality_of_service", owner="Service", semantic_name="quality_of_service"),
        ],
        structural_requirements={
            "filters": [{"property": "quality_of_service", "operator": "eq"}],
            "projection": [
                {
                    "semantic_type": "property",
                    "owner": "Service",
                    "name": "id",
                    "projection_terms": ["编号"],
                },
                {
                    "semantic_type": "property",
                    "owner": "Service",
                    "name": "quality_of_service",
                    "projection_terms": ["服务质量等级"],
                },
            ],
        },
        literals=[
            {
                "property": "quality_of_service",
                "owner": "Service",
                "raw": "Gold",
                "normalized": "Gold",
                "resolver_match_type": "exact",
            }
        ],
    )

    assert result.success is True
    assert result.dsl is not None
    assert [item["property"]["name"] for item in result.dsl["projection"]["items"]] == [
        "id",
        "quality_of_service",
    ]


def test_f2_preserves_vertex_full_detail_projection(
    registry: GraphSemanticRegistry,
) -> None:
    result = ZeroHopAssembler(registry).assemble(
        "F2",
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("property", "Service.quality_of_service", owner="Service", semantic_name="quality_of_service"),
        ],
        structural_requirements={
            "filters": [{"property": "quality_of_service", "operator": "eq"}],
            "projection": [
                {
                    "semantic_type": "vertex_full",
                    "name": "Service",
                    "alias": "service",
                    "projection_terms": ["详细信息"],
                }
            ],
        },
        literals=[
            {
                "property": "quality_of_service",
                "owner": "Service",
                "raw": "Gold",
                "normalized": "Gold",
                "resolver_match_type": "exact",
            }
        ],
    )

    assert result.success is True
    assert result.dsl is not None
    ast = parse_restricted_query_dsl(result.dsl, registry)
    assert ast.projection.items[0].vertex_full is True
    assert ast.projection.items[0].target is not None
    assert ast.projection.items[0].target.vertex_name == "Service"


def test_projection_terms_map_detail_info_to_vertex_full(
    registry: GraphSemanticRegistry,
) -> None:
    projection = _projection_items_from_substantive_terms(
        decomposition={
            "substantive_terms": [
                {"text": "服务", "slot": "projection"},
                {"text": "详细信息", "slot": "projection", "attached_to": "服务"},
            ]
        },
        candidates=[
            _semantic_candidate("vertex", "Service"),
            _semantic_candidate("property", "Service.id", owner="Service", semantic_name="id"),
        ],
        registry=registry,
        selected_vertices=["Service"],
    )

    assert projection == [
        {
            "semantic_type": "vertex_full",
            "name": "Service",
            "alias": "service",
            "projection_terms": ["详细信息"],
        }
    ]


def test_projection_terms_map_node_object_to_vertex_full_when_owner_is_unique(
    registry: GraphSemanticRegistry,
) -> None:
    projection = _projection_items_from_substantive_terms(
        decomposition={
            "substantive_terms": [
                {"text": "服务", "slot": "path"},
                {"text": "节点", "slot": "projection"},
            ]
        },
        candidates=[
            _semantic_candidate("vertex", "Service"),
            _semantic_candidate("property", "Service.id", owner="Service", semantic_name="id"),
        ],
        registry=registry,
        selected_vertices=["Service"],
    )

    assert projection == [
        {
            "semantic_type": "vertex_full",
            "name": "Service",
            "alias": "service",
            "projection_terms": ["节点"],
        }
    ]


def test_zero_hop_candidates_can_be_scoped_by_unique_literal_owner() -> None:
    candidates = [
        _semantic_candidate("vertex", "Service"),
        _semantic_candidate("vertex", "NetworkElement"),
        _semantic_candidate("property", "Service.elem_type", owner="Service", semantic_name="elem_type"),
        _semantic_candidate("property", "NetworkElement.elem_type", owner="NetworkElement", semantic_name="elem_type"),
    ]

    filtered = _zero_hop_candidates_for_assembler(candidates, preferred_vertex="Service")

    assert [(item.semantic_type, item.semantic_id) for item in filtered] == [
        ("vertex", "Service"),
        ("property", "Service.elem_type"),
    ]


def test_literal_request_infers_service_owner_from_projection_surface_without_vertex_candidate(
    registry: GraphSemanticRegistry,
) -> None:
    enriched = _with_literal_requests_from_candidates(
        {
            "original_question": "查询时延等于22的服务ID、名称和时延。",
            "literal_candidates": [{"text": "22", "kind_hint": "number"}],
            "substantive_terms": [
                {"text": "时延", "slot": "filter"},
                {"text": "等于", "slot": "filter"},
                {"text": "22", "slot": "filter"},
                {"text": "服务ID", "slot": "projection"},
                {"text": "名称", "slot": "projection"},
                {"text": "时延", "slot": "projection"},
            ],
        },
        CandidateRetrievalResult(
            candidates=[
                _semantic_candidate("property", "Service.id", owner="Service", semantic_name="id"),
                _semantic_candidate("property", "Service.latency", owner="Service", semantic_name="latency"),
                _semantic_candidate("property", "Tunnel.latency", owner="Tunnel", semantic_name="latency"),
            ]
        ),
        registry=registry,
    )

    assert enriched["literal_requests"] == [
        {
            "raw_literal": "22",
            "expected_vertex": "Service",
            "expected_property": "latency",
            "literal_kind_hint": "numeric",
        }
    ]


def test_f3_unique_vertex_count_builds_parseable_aggregate(registry: GraphSemanticRegistry) -> None:
    result = ZeroHopAssembler(registry).assemble(
        "F3",
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("property", "Service.id", owner="Service", semantic_name="id"),
        ],
        structural_requirements={"aggregate": {"function": "count", "alias": "service_count"}},
    )

    assert result.success is True
    assert result.dsl is not None
    ast = parse_restricted_query_dsl(result.dsl, registry)
    assert ast.query_shape.value == "ad_hoc_aggregate"
    assert ast.operations[0].group_by == []
    assert ast.operations[0].measures[0].function == "count"
    assert ast.operations[0].measures[0].target.alias == "target"
    assert ast.operations[0].measures[0].property.name == "id"
    assert ast.projection.items[0].source.namespace == "measure"
    assert ast.projection.items[0].source.name == "service_count"


def test_f3_property_value_count_uses_unique_measure_property(
    registry: GraphSemanticRegistry,
) -> None:
    result = ZeroHopAssembler(registry).assemble(
        "F3",
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("property", "Service.id", owner="Service", semantic_name="id"),
            _candidate(
                "property",
                "Service.quality_of_service",
                owner="Service",
                semantic_name="quality_of_service",
            ),
        ],
        structural_requirements={
            "aggregate": {
                "function": "count",
                "owner": "Service",
                "property": "quality_of_service",
                "alias": "service_quality_of_service_count",
                "projection_terms": ["服务质量", "属性值", "总数量"],
            }
        },
    )

    assert result.success is True
    assert result.dsl is not None
    aggregate = result.dsl["operations"][0]
    assert aggregate["measures"] == [
            {
                "alias": "service_quality_of_service_count",
                "function": "count",
                "target": "target",
                "property": {"owner": "Service", "name": "quality_of_service"},
            }
    ]
    assert result.dsl["projection"]["items"] == [
        {
            "alias": "service_quality_of_service_count",
            "source": "measure.service_quality_of_service_count",
            "projection_terms": ["服务质量", "属性值", "总数量"],
        }
    ]


def test_zero_hop_requirements_count_filter_slot_property_modifier_as_measure_property(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "Service"),
            _semantic_candidate("property", "Service.id", owner="Service", semantic_name="id"),
            _semantic_candidate(
                "property",
                "Service.quality_of_service",
                owner="Service",
                semantic_name="quality_of_service",
            ),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计所有服务节点中服务质量属性的数量。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "服务", "slot": "path"},
                {"text": "节点", "slot": "path"},
                {"text": "服务质量", "slot": "filter", "attached_to": "服务"},
                {"text": "属性", "slot": "filter", "attached_to": "服务质量"},
                {"text": "数量", "slot": "projection"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["aggregate"] == {
        "function": "count",
        "owner": "Service",
        "property": "quality_of_service",
        "alias": "service_quality_of_service_count",
        "projection_terms": ["服务质量", "属性", "数量"],
    }


def test_zero_hop_requirements_counts_id_property_values_when_value_is_quantity_term(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "Service"),
            _semantic_candidate("property", "Service.id", owner="Service", semantic_name="id"),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计服务节点的ID属性共有多少个值。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "服务", "slot": "path"},
                {"text": "节点", "slot": "path"},
                {"text": "ID", "slot": "filter", "attached_to": "服务"},
                {"text": "属性", "slot": "filter", "attached_to": "服务"},
                {"text": "值", "slot": "projection"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["aggregate"] == {
        "function": "count",
        "owner": "Service",
        "property": "id",
        "alias": "service_id_count",
        "projection_terms": ["ID", "属性", "值"],
    }


def test_zero_hop_requirements_count_field_total_as_property_count(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "Service"),
            _semantic_candidate("property", "Service.id", owner="Service", semantic_name="id"),
            _semantic_candidate("property", "Service.quality_of_service", owner="Service", semantic_name="quality_of_service"),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计服务质量等级字段的总数量。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "服务质量等级", "slot": "filter", "attached_to": "服务"},
                {"text": "字段", "slot": "filter", "attached_to": "服务质量等级"},
                {"text": "总数量", "slot": "projection"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["aggregate"] == {
        "function": "count",
        "owner": "Service",
        "property": "quality_of_service",
        "alias": "service_quality_of_service_count",
        "projection_terms": ["服务质量等级", "字段", "总数量"],
    }


def test_zero_hop_requirements_count_name_total_as_property_count_without_modifier(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "NetworkElement"),
            _semantic_candidate("property", "NetworkElement.id", owner="NetworkElement", semantic_name="id"),
            _semantic_candidate("property", "NetworkElement.name", owner="NetworkElement", semantic_name="name"),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计所有网元名称的总数量。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "网元", "slot": "projection"},
                {"text": "名称", "slot": "projection"},
                {"text": "总数量", "slot": "projection"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["aggregate"] == {
        "function": "count",
            "owner": "NetworkElement",
            "property": "name",
            "alias": "network_element_name_count",
        "projection_terms": ["名称", "总数量"],
    }


def test_zero_hop_requirements_does_not_count_property_without_closed_modifier(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "Service"),
            _semantic_candidate("property", "Service.id", owner="Service", semantic_name="id"),
            _semantic_candidate(
                "property",
                "Service.quality_of_service",
                owner="Service",
                semantic_name="quality_of_service",
            ),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计服务质量不为空的服务数量。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "服务质量", "slot": "filter", "attached_to": "服务"},
                {"text": "不为空", "slot": "filter", "attached_to": "服务质量"},
                {"text": "服务", "slot": "projection"},
                {"text": "数量", "slot": "projection", "attached_to": "服务"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["aggregate"] == {
        "function": "count",
        "alias": "service_count",
    }


def test_f3_multi_measure_keeps_entity_count_and_property_count(
    registry: GraphSemanticRegistry,
) -> None:
    result = ZeroHopAssembler(registry).assemble(
        "F3",
        candidates=[
            _candidate("vertex", "Tunnel"),
            _candidate("property", "Tunnel.id", owner="Tunnel", semantic_name="id"),
            _candidate("property", "Tunnel.bandwidth", owner="Tunnel", semantic_name="bandwidth"),
        ],
        structural_requirements={
            "aggregate": {
                "measures": [
                    {"function": "count", "alias": "tunnel_count"},
                    {
                        "function": "count",
                        "owner": "Tunnel",
                        "property": "bandwidth",
                        "alias": "tunnel_bandwidth_count",
                    },
                ]
            }
        },
    )

    assert result.success is True
    assert result.dsl is not None
    aggregate = result.dsl["operations"][0]
    assert aggregate["measures"] == [
        {
            "alias": "tunnel_count",
            "function": "count",
            "target": "target",
            "property": {"owner": "Tunnel", "name": "id"},
        },
        {
            "alias": "tunnel_bandwidth_count",
            "function": "count",
            "target": "target",
            "property": {"owner": "Tunnel", "name": "bandwidth"},
        },
    ]
    assert result.dsl["projection"]["items"] == [
        {"alias": "tunnel_count", "source": "measure.tunnel_count"},
        {"alias": "tunnel_bandwidth_count", "source": "measure.tunnel_bandwidth_count"},
    ]


def test_zero_hop_requirements_multi_measure_from_total_and_property_count_terms(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "Tunnel"),
            _semantic_candidate("property", "Tunnel.id", owner="Tunnel", semantic_name="id"),
            _semantic_candidate("property", "Tunnel.bandwidth", owner="Tunnel", semantic_name="bandwidth"),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "请统计隧道节点的总数以及拥有带宽字段的隧道数量。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "隧道", "slot": "path"},
                {"text": "节点", "slot": "path"},
                {"text": "总数", "slot": "projection", "attached_to": "隧道"},
                {"text": "带宽", "slot": "filter", "attached_to": "隧道"},
                {"text": "字段", "slot": "filter", "attached_to": "带宽"},
                {"text": "数量", "slot": "projection", "attached_to": "带宽"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["aggregate"] == {
        "measures": [
            {
                "function": "count",
                "alias": "tunnel_count",
                "projection_terms": ["总数"],
            },
            {
                "function": "count",
                "owner": "Tunnel",
                "property": "bandwidth",
                "alias": "tunnel_bandwidth_count",
                "projection_terms": ["带宽", "字段", "数量"],
            },
        ]
    }


def test_zero_hop_requirements_multi_measure_when_quantity_terms_are_unattached(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "Tunnel"),
            _semantic_candidate("property", "Tunnel.id", owner="Tunnel", semantic_name="id"),
            _semantic_candidate("property", "Tunnel.bandwidth", owner="Tunnel", semantic_name="bandwidth"),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "请统计隧道节点的总数以及拥有带宽属性的隧道数量。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "隧道", "slot": "projection"},
                {"text": "节点", "slot": "projection"},
                {"text": "总数", "slot": "projection"},
                {"text": "带宽", "slot": "filter", "attached_to": "隧道"},
                {"text": "属性", "slot": "filter", "attached_to": "隧道"},
                {"text": "数量", "slot": "projection"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["aggregate"] == {
        "measures": [
            {"function": "count", "alias": "tunnel_count", "projection_terms": ["总数"]},
            {
                "function": "count",
                "owner": "Tunnel",
                "property": "bandwidth",
                "alias": "tunnel_bandwidth_count",
                "projection_terms": ["带宽", "属性", "数量"],
            },
        ]
    }


def test_f3_non_null_filter_counts_requested_property_values(
    registry: GraphSemanticRegistry,
) -> None:
    result = ZeroHopAssembler(registry).assemble(
        "F3",
        candidates=[
            _candidate("vertex", "Tunnel"),
            _candidate("property", "Tunnel.id", owner="Tunnel", semantic_name="id"),
            _candidate("property", "Tunnel.bandwidth", owner="Tunnel", semantic_name="bandwidth"),
        ],
        structural_requirements={
            "filters": [{"property": "bandwidth", "operator": "is_not_null"}],
            "aggregate": {
                "function": "count",
                "owner": "Tunnel",
                "property": "bandwidth",
                "alias": "tunnel_bandwidth_count",
            },
        },
    )

    assert result.success is True
    assert result.dsl is not None
    assert result.dsl["filters"] == [
        {
            "target": "target",
            "property": {"owner": "Tunnel", "name": "bandwidth"},
            "operator": "is_not_null",
            "value": {"raw": None, "normalized": None, "resolver_match_type": "deterministic_non_null"},
        }
    ]
    ast = parse_restricted_query_dsl(result.dsl, registry)
    cypher = compile_restricted_query_ast(ast, registry).cypher
    assert "bandwidth IS NOT NULL" in cypher


def test_zero_hop_requirements_non_null_filter_reaches_f3_assembly(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "Tunnel"),
            _semantic_candidate("property", "Tunnel.id", owner="Tunnel", semantic_name="id"),
            _semantic_candidate("property", "Tunnel.bandwidth", owner="Tunnel", semantic_name="bandwidth"),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计隧道带宽字段不为空的数量。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "隧道", "slot": "path"},
                {"text": "带宽", "slot": "filter", "attached_to": "隧道"},
                {"text": "字段", "slot": "filter", "attached_to": "带宽"},
                {"text": "不为空", "slot": "filter", "attached_to": "带宽"},
                {"text": "数量", "slot": "projection", "attached_to": "带宽"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["filters"] == [{"property": "bandwidth", "operator": "is_not_null"}]
    assert requirements["aggregate"] == {
        "function": "count",
        "owner": "Tunnel",
        "property": "bandwidth",
        "alias": "tunnel_bandwidth_count",
        "projection_terms": ["带宽", "字段", "数量"],
    }


def test_zero_hop_requirements_non_null_filter_uses_previous_property_when_unattached(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "Tunnel"),
            _semantic_candidate("property", "Tunnel.id", owner="Tunnel", semantic_name="id"),
            _semantic_candidate("property", "Tunnel.bandwidth", owner="Tunnel", semantic_name="bandwidth"),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计有多少个隧道的带宽属性不为空。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "隧道", "slot": "projection"},
                {"text": "带宽", "slot": "filter", "attached_to": "隧道"},
                {"text": "属性", "slot": "filter", "attached_to": "隧道"},
                {"text": "不为空", "slot": "filter"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["filters"] == [{"property": "bandwidth", "operator": "is_not_null"}]
    assert requirements["aggregate"] == {
        "function": "count",
        "owner": "Tunnel",
        "property": "bandwidth",
        "alias": "tunnel_bandwidth_count",
        "projection_terms": ["带宽", "属性", "数量"],
    }


def test_zero_hop_requirements_count_owned_name_as_property_count(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "NetworkElement"),
            _semantic_candidate("property", "NetworkElement.id", owner="NetworkElement", semantic_name="id"),
            _semantic_candidate("property", "NetworkElement.name", owner="NetworkElement", semantic_name="name"),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计拥有名称的网元数量。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "网元", "slot": "path"},
                {"text": "名称", "slot": "filter", "attached_to": "网元"},
                {"text": "数量", "slot": "projection", "attached_to": "网元"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["aggregate"] == {
        "function": "count",
        "owner": "NetworkElement",
        "property": "name",
        "alias": "network_element_name_count",
        "projection_terms": ["名称", "数量"],
    }


def test_zero_hop_requirements_count_quality_attribute_records_as_property_count(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "Service"),
            _semantic_candidate("property", "Service.id", owner="Service", semantic_name="id"),
            _semantic_candidate(
                "property",
                "Service.quality_of_service",
                owner="Service",
                semantic_name="quality_of_service",
            ),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计服务质量属性记录的总数量。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "服务", "slot": "path"},
                {"text": "服务质量", "slot": "filter", "attached_to": "服务"},
                {"text": "属性记录", "slot": "filter", "attached_to": "服务质量"},
                {"text": "总数量", "slot": "projection"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["aggregate"] == {
        "function": "count",
        "owner": "Service",
        "property": "quality_of_service",
        "alias": "service_quality_of_service_count",
        "projection_terms": ["服务质量", "属性记录", "总数量"],
    }


def test_zero_hop_requirements_count_slot_with_only_property_projection_counts_property(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "Service"),
            _semantic_candidate("property", "Service.id", owner="Service", semantic_name="id"),
            _semantic_candidate(
                "property",
                "Service.quality_of_service",
                owner="Service",
                semantic_name="quality_of_service",
            ),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计服务质量等级字段的总数量。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "统计", "slot": "projection"},
                {"text": "服务", "slot": "path"},
                {"text": "质量等级", "slot": "projection"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["aggregate"] == {
        "function": "count",
        "owner": "Service",
        "property": "quality_of_service",
        "alias": "service_quality_of_service_count",
        "projection_terms": ["质量等级", "数量"],
    }


def test_zero_hop_requirements_split_total_with_attribute_record_counts_property(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "NetworkElement"),
            _semantic_candidate("property", "NetworkElement.id", owner="NetworkElement", semantic_name="id"),
            _semantic_candidate("property", "NetworkElement.name", owner="NetworkElement", semantic_name="name"),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计所有网元中名称属性记录的总数量。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "统计", "slot": "projection"},
                {"text": "网元", "slot": "path"},
                {"text": "名称", "slot": "filter", "attached_to": "网元"},
                {"text": "属性", "slot": "filter", "attached_to": "网元"},
                {"text": "记录", "slot": "filter", "attached_to": "网元"},
                {"text": "总", "slot": "projection"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["aggregate"] == {
        "function": "count",
        "owner": "NetworkElement",
        "property": "name",
        "alias": "network_element_name_count",
        "projection_terms": ["名称", "属性", "记录", "总"],
    }


def test_zero_hop_requirements_having_property_filters_and_counts_requested_id(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "NetworkElement"),
            _semantic_candidate("property", "NetworkElement.id", owner="NetworkElement", semantic_name="id"),
            _semantic_candidate("property", "NetworkElement.elem_type", owner="NetworkElement", semantic_name="elem_type"),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计具有元素类型属性的网元的ID总数量。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "网元", "slot": "path"},
                {"text": "元素类型", "slot": "filter", "attached_to": "网元"},
                {"text": "属性", "slot": "filter", "attached_to": "元素类型"},
                {"text": "ID", "slot": "projection", "attached_to": "网元"},
                {"text": "总数量", "slot": "projection", "attached_to": "ID"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["filters"] == [{"property": "elem_type", "operator": "is_not_null"}]
    assert requirements["aggregate"] == {
        "function": "count",
        "owner": "NetworkElement",
        "property": "id",
        "alias": "network_element_id_count",
        "projection_terms": ["ID", "总数量"],
    }


def test_zero_hop_requirements_owned_parameter_counts_property(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "Service"),
            _semantic_candidate("property", "Service.id", owner="Service", semantic_name="id"),
            _semantic_candidate(
                "property",
                "Service.quality_of_service",
                owner="Service",
                semantic_name="quality_of_service",
            ),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计拥有服务质量参数的服务数量。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "服务", "slot": "path"},
                {"text": "服务质量", "slot": "filter", "attached_to": "服务"},
                {"text": "参数", "slot": "filter", "attached_to": "服务质量"},
                {"text": "数量", "slot": "projection", "attached_to": "服务"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["aggregate"] == {
        "function": "count",
        "owner": "Service",
        "property": "quality_of_service",
        "alias": "service_quality_of_service_count",
        "projection_terms": ["服务质量", "参数", "数量"],
    }


def test_zero_hop_requirements_non_null_filter_counts_separate_requested_property(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "NetworkElement"),
            _semantic_candidate("property", "NetworkElement.id", owner="NetworkElement", semantic_name="id"),
            _semantic_candidate("property", "NetworkElement.name", owner="NetworkElement", semantic_name="name"),
            _semantic_candidate("property", "NetworkElement.elem_type", owner="NetworkElement", semantic_name="elem_type"),
        ],
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计所有元素类型不为空的网元的名称数量。",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "网元", "slot": "path"},
                {"text": "元素类型", "slot": "filter", "attached_to": "网元"},
                {"text": "不为空", "slot": "filter", "attached_to": "元素类型"},
                {"text": "名称", "slot": "projection", "attached_to": "网元"},
                {"text": "数量", "slot": "projection", "attached_to": "名称"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[],
        registry=registry,
    )

    assert requirements["filters"] == [{"property": "elem_type", "operator": "is_not_null"}]
    assert requirements["aggregate"] == {
        "function": "count",
        "owner": "NetworkElement",
        "property": "name",
        "alias": "network_element_name_count",
        "projection_terms": ["名称", "数量"],
    }


def test_zero_hop_property_count_terms_are_not_reused_as_literal_filters(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "NetworkElement"),
            _semantic_candidate("property", "NetworkElement.id", owner="NetworkElement", semantic_name="id"),
            _semantic_candidate("property", "NetworkElement.elem_type", owner="NetworkElement", semantic_name="elem_type"),
        ],
    )
    literal = LiteralResolverResult(
        raw_literal="类型",
        resolved=True,
        resolved_value="router",
        normalized_value="router",
        match_type="value_synonym",
        confidence=0.98,
        expected_vertex="NetworkElement",
        expected_property="elem_type",
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计所有网元节点的类型属性记录共有多少条？",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "网元节点", "slot": "path"},
                {"text": "类型", "slot": "filter", "attached_to": "网元节点"},
                {"text": "属性记录", "slot": "filter", "attached_to": "网元节点"},
                {"text": "多少条", "slot": "projection"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[literal],
        registry=registry,
    )

    assert requirements["filters"] == []
    assert requirements["aggregate"] == {
        "function": "count",
        "owner": "NetworkElement",
        "property": "elem_type",
        "alias": "network_element_elem_type_count",
        "projection_terms": ["类型", "属性记录", "多少条"],
    }


def test_f3_numeric_comparison_operator_from_closed_mapping_reaches_dsl(
    registry: GraphSemanticRegistry,
) -> None:
    retrieval = CandidateRetrievalResult(
        candidates=[
            _semantic_candidate("vertex", "Tunnel"),
            _semantic_candidate("property", "Tunnel.id", owner="Tunnel", semantic_name="id"),
            _semantic_candidate("property", "Tunnel.bandwidth", owner="Tunnel", semantic_name="bandwidth"),
        ],
    )
    literal = LiteralResolverResult(
        raw_literal="100",
        resolved=True,
        resolved_value=100.0,
        normalized_value=100.0,
        match_type="literal_passthrough",
        confidence=1.0,
        expected_vertex="Tunnel",
        expected_property="bandwidth",
    )

    requirements = _zero_hop_assembler_requirements(
        shape=QueryShape.F3_VERTEX_AGGREGATE_0HOP,
        decomposition={
            "original_question": "统计带宽大于100的隧道有多少个？",
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "带宽", "slot": "filter", "attached_to": "隧道"},
                {"text": "大于", "slot": "filter", "attached_to": "带宽"},
                {"text": "100", "slot": "filter", "attached_to": "带宽"},
                {"text": "隧道", "slot": "projection"},
                {"text": "多少个", "slot": "projection", "attached_to": "隧道"},
            ],
        },
        retrieval_result=retrieval,
        literal_results=[literal],
        registry=registry,
    )

    result = ZeroHopAssembler(registry).assemble(
        "F3",
        candidates=_zero_hop_candidates_for_assembler(retrieval.candidates),
        structural_requirements=requirements,
        literals=[
            {
                "property": "bandwidth",
                "owner": "Tunnel",
                "raw": "100",
                "normalized": 100.0,
                "resolver_match_type": "literal_passthrough",
            }
        ],
    )

    assert result.success is True
    assert result.dsl is not None
    assert result.dsl["filters"][0]["operator"] == "gt"
    assert result.dsl["operations"][0]["measures"][0]["property"] == {"owner": "Tunnel", "name": "id"}


def test_f3_count_with_literal_filter_resolves_to_aggregate_shape() -> None:
    result = classify_query_shape(
        {
            "requires_aggregate": True,
            "projection_terms": ["服务", "多少"],
            "path_terms": [],
        },
        {
            "intent_type": "count",
            "output_shape": "scalar",
            "literal_candidates": [{"text": "100", "kind_hint": "number"}],
            "substantive_terms": [
                {"text": "带宽", "slot": "filter", "attached_to": "服务"},
                {"text": "大于", "slot": "filter"},
                {"text": "100", "slot": "filter"},
                {"text": "服务", "slot": "projection"},
                {"text": "多少", "slot": "projection"},
            ],
        },
    )

    assert result.status == ShapeStatus.RESOLVED
    assert result.shape == QueryShape.F3_VERTEX_AGGREGATE_0HOP


def test_f3_with_group_order_or_limit_falls_back(registry: GraphSemanticRegistry) -> None:
    for structural_requirements in (
        {"aggregate": {"function": "count"}, "group_by": [{"property": "quality_of_service"}]},
        {"aggregate": {"function": "count"}, "order_by": [{"source": "measure.service_count"}]},
        {"aggregate": {"function": "count"}, "limit": 5},
    ):
        result = ZeroHopAssembler(registry).assemble(
            "F3",
            candidates=[
                _candidate("vertex", "Service"),
                _candidate("property", "Service.id", owner="Service", semantic_name="id"),
            ],
            structural_requirements=structural_requirements,
        )

        assert result.success is False
        assert result.dsl is None
        assert result.fallback_reason == "unsupported_f3_modifier"


def _candidate(
    semantic_type: str,
    semantic_id: str,
    *,
    owner: str | None = None,
    semantic_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> CandidateBinding:
    return CandidateBinding(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
        semantic_name=semantic_name or semantic_id,
        owner=owner,
        score=1.0,
        match_type="exact",
        metadata=metadata or {},
    )


def _semantic_candidate(
    semantic_type: str,
    semantic_id: str,
    *,
    owner: str | None = None,
    semantic_name: str | None = None,
) -> SemanticCandidate:
    return SemanticCandidate(
        semantic_type=semantic_type,  # type: ignore[arg-type]
        semantic_id=semantic_id,
        semantic_name=semantic_name or semantic_id,
        owner=owner,
        score=1.0,
        match_type="exact",
        evidence=[],
    )
