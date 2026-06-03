from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.binding import (
    BindingValidationError,
    SemanticBinder,
)
from services.cypher_generator_agent.app.literals.models import (
    LiteralAlternative,
    LiteralEvidence,
    LiteralResolverResult,
)
from services.cypher_generator_agent.app.retrieval.models import (
    CandidateEvidence,
    SemanticCandidate,
)
from services.cypher_generator_agent.app.semantic_model.loader import load_graph_semantic_model


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "network_topology_graph_model.yaml"
)


@pytest.fixture
def binder() -> SemanticBinder:
    return SemanticBinder(load_graph_semantic_model(FIXTURE_PATH).registry)


def test_gold_service_tunnel_question_binds_stable_plan(binder: SemanticBinder) -> None:
    literal_result = LiteralResolverResult(
        raw_literal="Gold",
        resolved=True,
        resolved_value="GOLD",
        normalized_value="GOLD",
        match_type="value_synonym",
        confidence=0.98,
        expected_vertex="Service",
        expected_property="quality_of_service",
        evidence=[
            LiteralEvidence(
                source="property.value_synonyms",
                matched="Gold",
                target="GOLD",
            )
        ],
    )

    plan = binder.bind(
        {
            "query_shape": "single_hop",
            "selected_vertices": ["Service", "Tunnel"],
            "selected_edges": ["SERVICE_USES_TUNNEL"],
            "selected_properties": [
                {"owner": "Service", "name": "quality_of_service"},
            ],
            "selected_literals": [literal_result.model_dump()],
            "filters": [
                {
                    "owner": "Service",
                    "property": "quality_of_service",
                    "operator": "=",
                    "raw_literal": "Gold",
                }
            ],
            "projection": [{"semantic_type": "vertex", "name": "Tunnel"}],
            "limit": 50,
        },
        candidates=_gold_candidates(),
    )

    assert plan.query_shape == "single_hop_traversal"
    assert [binding.name for binding in plan.vertex_bindings] == ["Service", "Tunnel"]
    assert [binding.name for binding in plan.edge_bindings] == ["SERVICE_USES_TUNNEL"]
    assert [(binding.owner, binding.name) for binding in plan.property_bindings] == [
        ("Service", "quality_of_service")
    ]
    assert plan.literal_bindings[0].raw_literal == "Gold"
    assert plan.literal_bindings[0].value == "GOLD"
    assert plan.filters[0].owner == "Service"
    assert plan.filters[0].property == "quality_of_service"
    assert plan.filters[0].operator == "eq"
    assert plan.filters[0].value == "GOLD"
    assert plan.filters[0].literal.raw_literal == "Gold"
    assert plan.projection == [{"semantic_type": "vertex", "name": "Tunnel"}]
    assert plan.limit == 50


def test_filter_property_shorthand_is_grounded_by_selected_literal_and_property(
    binder: SemanticBinder,
) -> None:
    literal_result = LiteralResolverResult(
        raw_literal="Gold",
        resolved=True,
        resolved_value="GOLD",
        normalized_value="GOLD",
        match_type="value_synonym",
        confidence=0.98,
        expected_vertex="Service",
        expected_property="quality_of_service",
        evidence=[
            LiteralEvidence(
                source="property.value_synonyms",
                matched="Gold",
                target="GOLD",
            )
        ],
    )

    plan = binder.bind(
        {
            "query_shape": "single_hop",
            "selected_vertices": ["Service", "Tunnel"],
            "selected_edges": ["SERVICE_USES_TUNNEL"],
            "selected_properties": ["Service.quality_of_service"],
            "selected_literals": [literal_result.model_dump()],
            "filters": [
                {
                    "property": "quality_of_service",
                    "operator": "=",
                    "value": "Gold",
                }
            ],
            "projection": [{"semantic_type": "vertex", "name": "Tunnel"}],
        },
        candidates=_gold_candidates(),
    )

    assert plan.filters[0].owner == "Service"
    assert plan.filters[0].property == "quality_of_service"
    assert plan.filters[0].raw_literal == "Gold"
    assert plan.filters[0].value == "GOLD"


def test_projection_vertex_shorthand_is_normalized_to_semantic_reference(
    binder: SemanticBinder,
) -> None:
    plan = binder.bind(
        {
            "query_shape": "single_hop",
            "selected_vertices": ["Service", "Tunnel"],
            "selected_edges": ["SERVICE_USES_TUNNEL"],
            "projection": [{"vertex": "Tunnel"}],
        },
        candidates=_gold_candidates(),
    )

    assert plan.projection == [{"semantic_type": "vertex", "name": "Tunnel"}]


def test_selected_projection_vertex_binding_hydrates_to_vertex_full(
    binder: SemanticBinder,
) -> None:
    plan = binder.bind(
        {
            "query_shape": "variable_path_traversal",
            "selected_bindings": [
                {"role": "source", "semantic_type": "vertex", "semantic_id": "Service"},
                {
                    "role": "relation",
                    "semantic_type": "edge",
                    "semantic_id": "SERVICE_USES_TUNNEL",
                    "direction": "forward",
                },
                {"role": "target", "semantic_type": "vertex", "semantic_id": "Tunnel"},
                {
                    "role": "relation",
                    "semantic_type": "edge",
                    "semantic_id": "PATH_THROUGH",
                    "direction": "forward",
                },
                {"role": "projection", "semantic_type": "vertex", "semantic_id": "NetworkElement"},
            ],
        },
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("vertex", "Tunnel"),
            _candidate("vertex", "NetworkElement"),
            _candidate("edge", "SERVICE_USES_TUNNEL"),
            _candidate("edge", "PATH_THROUGH"),
        ],
    )

    assert plan.projection == [
        {"semantic_type": "vertex_full", "name": "NetworkElement", "alias": "network_element"}
    ]


def test_lookup_with_two_vertices_infers_unambiguous_connecting_edge(
    binder: SemanticBinder,
) -> None:
    plan = binder.bind(
        {
            "query_shape": "lookup",
            "selected_vertices": ["Service", "Tunnel"],
            "selected_properties": ["Service.quality_of_service"],
            "projection": [{"vertex": "Tunnel"}],
        },
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("vertex", "Tunnel"),
            _candidate(
                "property",
                "Service.quality_of_service",
                owner="Service",
                semantic_name="quality_of_service",
            ),
        ],
    )

    assert plan.query_shape == "single_hop_traversal"
    assert [binding.name for binding in plan.edge_bindings] == ["SERVICE_USES_TUNNEL"]
    assert plan.edge_bindings[0].candidate.match_type == "semantic_inference"
    assert plan.assumptions[-1] == {
        "type": "inferred_edge_binding",
        "edge": "SERVICE_USES_TUNNEL",
        "from_vertex": "Service",
        "to_vertex": "Tunnel",
        "direction": "forward",
    }


def test_edge_direction_mapping_is_preserved_for_validator_and_dsl_builder(
    binder: SemanticBinder,
) -> None:
    plan = binder.bind(
        {
            "query_shape": "single_hop",
            "selected_vertices": ["Tunnel", "Service"],
            "selected_edges": [{"name": "SERVICE_USES_TUNNEL", "direction": "backward"}],
        },
        candidates=[
            _candidate("vertex", "Tunnel"),
            _candidate("vertex", "Service"),
            _candidate("edge", "SERVICE_USES_TUNNEL"),
        ],
    )

    assert plan.edge_bindings[0].name == "SERVICE_USES_TUNNEL"
    assert plan.edge_bindings[0].direction == "backward"


def test_variable_path_with_multiple_edges_is_normalized_to_traversal_chain(
    binder: SemanticBinder,
) -> None:
    plan = binder.bind(
        {
            "query_shape": "variable_path_traversal",
            "selected_vertices": ["Service", "Tunnel", "NetworkElement"],
            "selected_edges": ["SERVICE_USES_TUNNEL", "PATH_THROUGH"],
            "projection": [
                {
                    "semantic_type": "property",
                    "owner": "NetworkElement",
                    "name": "name",
                }
            ],
        },
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("vertex", "Tunnel"),
            _candidate("vertex", "NetworkElement"),
            _candidate("edge", "SERVICE_USES_TUNNEL"),
            _candidate("edge", "PATH_THROUGH"),
            _candidate(
                "property",
                "NetworkElement.name",
                owner="NetworkElement",
                semantic_name="name",
            ),
        ],
    )

    assert plan.query_shape == "single_hop_traversal"
    assert plan.assumptions[-1] == {
        "type": "query_shape_normalized",
        "from": "variable_path_traversal",
        "to": "single_hop_traversal",
        "reason": "multiple edge bindings form a traversal chain",
        "edge_count": 2,
        "vertex_count": 3,
    }


def test_variable_path_with_missing_intermediate_vertex_is_inferred_from_edge_chain(
    binder: SemanticBinder,
) -> None:
    plan = binder.bind(
        {
            "query_shape": "variable_path_traversal",
            "selected_vertices": ["Service", "NetworkElement", "Port"],
            "selected_edges": ["SERVICE_USES_TUNNEL", "PATH_THROUGH", "HAS_PORT"],
            "projection": [
                {
                    "semantic_type": "property",
                    "owner": "Port",
                    "name": "id",
                }
            ],
        },
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("vertex", "NetworkElement"),
            _candidate("vertex", "Port"),
            _candidate("edge", "SERVICE_USES_TUNNEL"),
            _candidate("edge", "PATH_THROUGH"),
            _candidate("edge", "HAS_PORT"),
            _candidate("property", "Port.id", owner="Port", semantic_name="id"),
        ],
    )

    assert plan.query_shape == "single_hop_traversal"
    assert [binding.name for binding in plan.vertex_bindings] == [
        "Service",
        "Tunnel",
        "NetworkElement",
        "Port",
    ]
    assert plan.assumptions[-2]["type"] == "inferred_vertex_chain_from_edges"


def test_metric_group_by_dimensions_are_preserved_for_validation(
    binder: SemanticBinder,
) -> None:
    plan = binder.bind(
        {
            "query_shape": "metric_aggregate",
            "selected_metrics": ["device_count"],
            "group_by": [
                {
                    "alias": "elem_type",
                    "target": "ne",
                    "property": {"owner": "NetworkElement", "name": "elem_type"},
                }
            ],
        },
        candidates=[
            _candidate("metric", "device_count"),
            _candidate(
                "property",
                "NetworkElement.elem_type",
                owner="NetworkElement",
                semantic_name="elem_type",
            ),
        ],
    )

    assert plan.group_by == [
        {
            "alias": "elem_type",
            "target": "ne",
            "property": {"owner": "NetworkElement", "name": "elem_type"},
        }
    ]


def test_ad_hoc_measures_are_preserved_for_dsl_builder(
    binder: SemanticBinder,
) -> None:
    plan = binder.bind(
        {
            "query_shape": "ad_hoc_aggregate",
            "selected_vertices": ["Port"],
            "selected_properties": [
                {"owner": "Port", "name": "status"},
                {"owner": "Port", "name": "id"},
            ],
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
        },
        candidates=[
            _candidate("vertex", "Port"),
            _candidate("property", "Port.status", owner="Port", semantic_name="status"),
            _candidate("property", "Port.id", owner="Port", semantic_name="id"),
        ],
    )

    assert plan.measures == [
        {
            "alias": "port_count",
            "function": "count",
            "target": "port",
            "property": {"owner": "Port", "name": "id"},
        }
    ]


def test_llm_nested_filter_payload_is_normalized_to_selected_literal(
    binder: SemanticBinder,
) -> None:
    literal_result = LiteralResolverResult(
        raw_literal="Gold级别",
        resolved=True,
        resolved_value="GOLD",
        normalized_value="GOLD",
        match_type="exact",
        confidence=0.99,
        expected_vertex="Service",
        expected_property="quality_of_service",
    )

    plan = binder.bind(
        {
            "query_shape": "single_hop",
            "selected_vertices": ["Service", "Tunnel"],
            "selected_edges": ["SERVICE_USES_TUNNEL"],
            "selected_properties": ["Service.quality_of_service"],
            "selected_literals": [literal_result.model_dump()],
            "filters": [
                {
                    "role": "filter",
                    "binding": {
                        "candidate_id": "property:Service.quality_of_service",
                        "semantic_type": "property",
                        "semantic_id": "Service.quality_of_service",
                        "semantic_name": "quality_of_service",
                        "owner": "Service",
                    },
                    "literal": {"raw_literal": "Gold级别", "resolved_value": "GOLD"},
                }
            ],
            "projection": [
                {
                    "role": "vertex",
                    "binding": {
                        "candidate_id": "vertex:Tunnel",
                        "semantic_type": "vertex",
                        "semantic_id": "Tunnel",
                        "semantic_name": "Tunnel",
                        "owner": None,
                    },
                }
            ],
        },
        candidates=_gold_candidates(),
    )

    assert plan.filters[0].owner == "Service"
    assert plan.filters[0].property == "quality_of_service"
    assert plan.filters[0].raw_literal == "Gold级别"
    assert plan.filters[0].value == "GOLD"
    assert plan.projection == [{"semantic_type": "vertex", "name": "Tunnel"}]


def test_vertex_lookup_infers_unique_vertex_from_property_owners(
    binder: SemanticBinder,
) -> None:
    literal_result = LiteralResolverResult(
        raw_literal="21",
        resolved=True,
        resolved_value=21.0,
        normalized_value=21.0,
        match_type="typed_parse",
        confidence=0.97,
        expected_vertex="Service",
        expected_property="quality_of_service",
    )

    plan = binder.bind(
        {
            "query_shape": "vertex_lookup",
            "selected_properties": ["Service.quality_of_service", "Service.id"],
            "selected_literals": [literal_result.model_dump()],
            "projection": [
                {"semantic_type": "property", "owner": "Service", "name": "id"},
                {"semantic_type": "property", "owner": "Service", "name": "quality_of_service"},
            ],
        },
        candidates=_service_lookup_candidates(),
    )

    assert [binding.name for binding in plan.vertex_bindings] == ["Service"]
    assert [(item["owner"], item["name"]) for item in plan.projection] == [
        ("Service", "id"),
        ("Service", "quality_of_service"),
    ]
    assert [(item.owner, item.property) for item in plan.filters] == [("Service", "quality_of_service")]
    assert any(item["type"] == "inferred_vertex_from_property_owner" for item in plan.assumptions)


def test_vertex_lookup_does_not_infer_vertex_from_multiple_property_owners(
    binder: SemanticBinder,
) -> None:
    plan = binder.bind(
        {
            "query_shape": "vertex_lookup",
            "selected_properties": ["Service.id", "Tunnel.id"],
            "projection": [
                {"semantic_type": "property", "owner": "Service", "name": "id"},
                {"semantic_type": "property", "owner": "Tunnel", "name": "id"},
            ],
        },
        candidates=[
            *_service_lookup_candidates(),
            _candidate("vertex", "Tunnel"),
            _candidate("property", "Tunnel.id", owner="Tunnel", semantic_name="id"),
        ],
    )

    assert plan.vertex_bindings == []


def test_llm_shorthand_filter_and_projection_are_normalized(
    binder: SemanticBinder,
) -> None:
    literal_result = LiteralResolverResult(
        raw_literal="down",
        resolved=True,
        resolved_value="down",
        normalized_value="down",
        match_type="exact",
        confidence=1.0,
        expected_vertex="Port",
        expected_property="status",
    )

    plan = binder.bind(
        {
            "query_shape": "vertex_lookup",
            "selected_vertices": ["Port"],
            "selected_properties": ["Port.status"],
            "selected_literals": [literal_result.model_dump()],
            "filters": [{"property:Port.status": "down"}],
            "projection": [{"vertex:Port": {}}],
        },
        candidates=[
            _candidate("vertex", "Port"),
            _candidate("property", "Port.status", owner="Port", semantic_name="status"),
        ],
    )

    assert plan.filters[0].owner == "Port"
    assert plan.filters[0].property == "status"
    assert plan.filters[0].value == "down"
    assert plan.projection == [{"semantic_type": "vertex", "name": "Port"}]


def test_metric_aggregate_without_metric_binding_falls_back_to_ad_hoc_count(
    binder: SemanticBinder,
) -> None:
    literal_result = LiteralResolverResult(
        raw_literal="防火墙",
        resolved=True,
        resolved_value="firewall",
        normalized_value="firewall",
        match_type="value_synonym",
        confidence=0.98,
        expected_vertex="NetworkElement",
        expected_property="elem_type",
    )

    plan = binder.bind(
        {
            "query_shape": "metric_aggregate",
            "selected_vertices": ["NetworkElement"],
            "selected_properties": ["NetworkElement.elem_type"],
            "selected_literals": [literal_result.model_dump()],
            "filters": [{"property": "NetworkElement.elem_type", "value": "firewall"}],
            "measures": [{"function": "count", "vertex": "NetworkElement"}],
        },
        candidates=[
            _candidate("vertex", "NetworkElement"),
            _candidate(
                "property",
                "NetworkElement.elem_type",
                owner="NetworkElement",
                semantic_name="elem_type",
            ),
            _candidate("property", "NetworkElement.id", owner="NetworkElement", semantic_name="id"),
        ],
    )

    assert plan.query_shape == "ad_hoc_aggregate"
    assert plan.filters[0].owner == "NetworkElement"
    assert plan.filters[0].value == "firewall"
    assert plan.measures == [
        {
            "function": "count",
            "vertex": "NetworkElement",
            "alias": "network_element_count",
            "target": "network_element",
            "property": {"owner": "NetworkElement", "name": "id"},
        }
    ]


def test_ad_hoc_measure_property_must_be_in_candidate_set(
    binder: SemanticBinder,
) -> None:
    with pytest.raises(BindingValidationError, match="measures"):
        binder.bind(
            {
                "query_shape": "ad_hoc_aggregate",
                "selected_vertices": ["Port"],
                "measures": [
                    {
                        "alias": "port_count",
                        "function": "count",
                        "target": "port",
                        "property": {"owner": "Port", "name": "id"},
                    }
                ],
            },
            candidates=[_candidate("vertex", "Port")],
        )


def test_rejects_llm_vertex_name_without_candidate_or_registry_match(binder: SemanticBinder) -> None:
    with pytest.raises(BindingValidationError, match="NetworkDevice"):
        binder.bind(
            {
                "query_shape": "lookup",
                "selected_vertices": ["NetworkDevice"],
            },
            candidates=[
                _candidate("vertex", "NetworkElement"),
                _candidate("vertex", "Service"),
            ],
        )


def test_rejects_registry_vertex_when_candidate_is_missing(binder: SemanticBinder) -> None:
    with pytest.raises(BindingValidationError, match="not present in candidate set"):
        binder.bind(
            {
                "query_shape": "lookup",
                "selected_vertices": ["Tunnel"],
            },
            candidates=[
                _candidate("vertex", "Service"),
            ],
        )


def test_rejects_candidate_property_when_registry_property_is_missing(binder: SemanticBinder) -> None:
    with pytest.raises(BindingValidationError, match="not found in semantic registry"):
        binder.bind(
            {
                "query_shape": "lookup",
                "selected_properties": ["Service.missing_property"],
            },
            candidates=[
                _candidate(
                    "property",
                    "Service.missing_property",
                    owner="Service",
                    semantic_name="missing_property",
                ),
            ],
        )


def test_rejects_hallucinated_projection_reference(binder: SemanticBinder) -> None:
    with pytest.raises(BindingValidationError, match="projection"):
        binder.bind(
            {
                "query_shape": "lookup",
                "selected_vertices": ["Service"],
                "projection": [{"semantic_type": "vertex", "name": "NetworkDevice"}],
            },
            candidates=[
                _candidate("vertex", "Service"),
            ],
        )


def test_rejects_projection_name_without_semantic_type(binder: SemanticBinder) -> None:
    with pytest.raises(BindingValidationError, match="projection"):
        binder.bind(
            {
                "query_shape": "lookup",
                "selected_vertices": ["Service"],
                "projection": [{"name": "NetworkDevice"}],
            },
            candidates=[
                _candidate("vertex", "Service"),
            ],
        )


def test_rejects_projection_source_without_namespace(binder: SemanticBinder) -> None:
    with pytest.raises(BindingValidationError, match="projection"):
        binder.bind(
            {
                "query_shape": "lookup",
                "selected_vertices": ["Service"],
                "projection": [{"source": "NetworkDevice"}],
            },
            candidates=[
                _candidate("vertex", "Service"),
            ],
        )


def test_allows_namespaced_projection_source_reference(binder: SemanticBinder) -> None:
    plan = binder.bind(
        {
            "query_shape": "lookup",
            "selected_vertices": ["Service"],
            "projection": [{"source": "service_counts.total"}],
        },
        candidates=[
            _candidate("vertex", "Service"),
        ],
    )

    assert plan.projection == [{"source": "service_counts.total"}]


def test_rejects_source_reference_with_extra_reference_like_fields(binder: SemanticBinder) -> None:
    with pytest.raises(BindingValidationError, match="source"):
        binder.bind(
            {
                "query_shape": "lookup",
                "selected_vertices": ["Service"],
                "projection": [{"source": "service_counts.total", "name": "NetworkDevice"}],
            },
            candidates=[
                _candidate("vertex", "Service"),
            ],
        )


def test_dsl_shaped_projection_property_is_validated_against_candidates(
    binder: SemanticBinder,
) -> None:
    plan = binder.bind(
        {
            "query_shape": "single_hop",
            "selected_vertices": ["Service", "Tunnel"],
            "projection": [
                {
                    "target": "tunnel",
                    "property": {"owner": "Tunnel", "name": "id"},
                }
            ],
        },
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("vertex", "Tunnel"),
            _candidate("property", "Tunnel.id", owner="Tunnel", semantic_name="id"),
        ],
    )

    assert plan.projection == [{"target": "tunnel", "property": {"owner": "Tunnel", "name": "id"}}]


def test_rejects_hallucinated_sort_reference(binder: SemanticBinder) -> None:
    with pytest.raises(BindingValidationError, match="sort"):
        binder.bind(
            {
                "query_shape": "lookup",
                "selected_vertices": ["Service"],
                "sort": [{"semantic_type": "property", "owner": "Service", "name": "missing_property"}],
            },
            candidates=[
                _candidate("vertex", "Service"),
            ],
        )


def test_rejects_sort_name_without_semantic_type(binder: SemanticBinder) -> None:
    with pytest.raises(BindingValidationError, match="sort"):
        binder.bind(
            {
                "query_shape": "lookup",
                "selected_vertices": ["Service"],
                "sort": [{"name": "NetworkDevice"}],
            },
            candidates=[
                _candidate("vertex", "Service"),
            ],
        )


def test_rejects_sort_source_without_namespace(binder: SemanticBinder) -> None:
    with pytest.raises(BindingValidationError, match="sort"):
        binder.bind(
            {
                "query_shape": "lookup",
                "selected_vertices": ["Service"],
                "sort": [{"source": "name", "direction": "asc"}],
            },
            candidates=[
                _candidate("vertex", "Service"),
            ],
        )


def test_rejects_filter_value_without_literal_resolver_result(binder: SemanticBinder) -> None:
    with pytest.raises(BindingValidationError, match="literal"):
        binder.bind(
            {
                "query_shape": "lookup",
                "selected_vertices": ["Service"],
                "selected_properties": ["Service.quality_of_service"],
                "filters": [
                    {
                        "owner": "Service",
                        "property": "quality_of_service",
                        "operator": "=",
                        "raw_literal": "Gold",
                        "value": "GOLD",
                    }
                ],
            },
            candidates=[
                _candidate("vertex", "Service"),
                _candidate(
                    "property",
                    "Service.quality_of_service",
                    owner="Service",
                    semantic_name="quality_of_service",
                ),
            ],
        )


def test_rejects_filter_literal_result_for_wrong_property(binder: SemanticBinder) -> None:
    wrong_literal = LiteralResolverResult(
        raw_literal="Gold",
        resolved=True,
        resolved_value="GOLD",
        normalized_value="GOLD",
        match_type="value_synonym",
        confidence=0.98,
        expected_vertex="Service",
        expected_property="service_type",
    )

    with pytest.raises(BindingValidationError, match="does not match filter"):
        binder.bind(
            {
                "query_shape": "lookup",
                "selected_vertices": ["Service"],
                "selected_properties": ["Service.quality_of_service"],
                "filters": [
                    {
                        "owner": "Service",
                        "property": "quality_of_service",
                        "operator": "=",
                        "raw_literal": "Gold",
                        "literal_result": wrong_literal.model_dump(),
                    }
                ],
            },
            candidates=[
                _candidate("vertex", "Service"),
                _candidate(
                    "property",
                    "Service.quality_of_service",
                    owner="Service",
                    semantic_name="quality_of_service",
                ),
            ],
        )


def test_preserves_unknown_query_shape_for_semantic_validator(binder: SemanticBinder) -> None:
    plan = binder.bind(
        {
            "query_shape": "shortest_path",
            "selected_vertices": ["Service"],
        },
        candidates=[
            _candidate("vertex", "Service"),
        ],
    )

    assert plan.query_shape == "shortest_path"


def test_rejects_unsupported_filter_operator(binder: SemanticBinder) -> None:
    literal_result = LiteralResolverResult(
        raw_literal="Gold",
        resolved=True,
        resolved_value="GOLD",
        normalized_value="GOLD",
        match_type="value_synonym",
        confidence=0.98,
        expected_vertex="Service",
        expected_property="quality_of_service",
    )

    with pytest.raises(BindingValidationError, match="operator"):
        binder.bind(
            {
                "query_shape": "lookup",
                "selected_vertices": ["Service"],
                "selected_properties": ["Service.quality_of_service"],
                "selected_literals": [literal_result.model_dump()],
                "filters": [
                    {
                        "owner": "Service",
                        "property": "quality_of_service",
                        "operator": "roughly_equals",
                        "raw_literal": "Gold",
                    }
                ],
            },
            candidates=[
                _candidate("vertex", "Service"),
                _candidate(
                    "property",
                    "Service.quality_of_service",
                    owner="Service",
                    semantic_name="quality_of_service",
                ),
            ],
        )


def test_high_confidence_fuzzy_literal_result_becomes_assumption(binder: SemanticBinder) -> None:
    fuzzy_result = LiteralResolverResult(
        raw_literal="voic",
        resolved=True,
        resolved_value="VOICE",
        normalized_value="VOICE",
        match_type="fuzzy_text",
        confidence=0.97,
        expected_vertex="Service",
        expected_property="service_type",
        evidence=[
            LiteralEvidence(
                source="property.valid_values",
                matched="voic",
                target="VOICE",
            )
        ],
    )

    plan = binder.bind(
        {
            "query_shape": "lookup",
            "selected_vertices": ["Service"],
            "selected_properties": ["Service.service_type"],
            "selected_literals": [fuzzy_result.model_dump()],
            "filters": [
                {
                    "owner": "Service",
                    "property": "service_type",
                    "operator": "=",
                    "raw_literal": "voic",
                }
            ],
        },
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("property", "Service.service_type", owner="Service", semantic_name="service_type"),
        ],
    )

    assert plan.filters[0].value == "VOICE"
    assert plan.assumptions == [
        {
            "type": "literal_fuzzy_match",
            "raw_literal": "voic",
            "owner": "Service",
            "property": "service_type",
            "value": "VOICE",
            "confidence": 0.97,
        }
    ]


def test_unresolved_literal_and_alternatives_are_preserved_in_filter_value(
    binder: SemanticBinder,
) -> None:
    unresolved_result = LiteralResolverResult(
        raw_literal="gol",
        resolved=False,
        resolved_value=None,
        normalized_value=None,
        match_type="unresolved",
        confidence=0.0,
        expected_vertex="Service",
        expected_property="quality_of_service",
        alternatives=[
            LiteralAlternative(
                value="GOLD",
                display="Gold",
                confidence=0.83,
                source="property.valid_values",
                why="closest local literal candidate",
            ),
            LiteralAlternative(
                value="SILVER",
                display="Silver",
                confidence=0.52,
                source="property.valid_values",
                why="closest local literal candidate",
            ),
        ],
        requires_user_choice=True,
        error_code="literal_ambiguous",
    )

    plan = binder.bind(
        {
            "query_shape": "lookup",
            "selected_vertices": ["Service"],
            "selected_properties": ["Service.quality_of_service"],
            "literal_resolver_results": [unresolved_result.model_dump()],
            "filters": [
                {
                    "owner": "Service",
                    "property": "quality_of_service",
                    "operator": "=",
                    "raw_literal": "gol",
                }
            ],
        },
        candidates=[
            _candidate("vertex", "Service"),
            _candidate(
                "property",
                "Service.quality_of_service",
                owner="Service",
                semantic_name="quality_of_service",
            ),
        ],
    )

    literal = plan.filters[0].literal
    assert plan.filters[0].value is None
    assert literal.resolved is False
    assert literal.raw_literal == "gol"
    assert literal.requires_user_choice is True
    assert [alternative.value for alternative in literal.alternatives] == ["GOLD", "SILVER"]
    assert plan.literal_bindings[0] == literal


def _gold_candidates() -> list[SemanticCandidate]:
    return [
        _candidate("vertex", "Service"),
        _candidate("vertex", "Tunnel"),
        _candidate("edge", "SERVICE_USES_TUNNEL"),
        _candidate(
            "property",
            "Service.quality_of_service",
            owner="Service",
            semantic_name="quality_of_service",
        ),
    ]


def _service_lookup_candidates() -> list[SemanticCandidate]:
    return [
        _candidate("vertex", "Service"),
        _candidate("property", "Service.id", owner="Service", semantic_name="id"),
        _candidate(
            "property",
            "Service.quality_of_service",
            owner="Service",
            semantic_name="quality_of_service",
        ),
        _candidate("property", "Service.latency", owner="Service", semantic_name="latency"),
        _candidate("property", "Service.elem_type", owner="Service", semantic_name="elem_type"),
    ]


def _candidate(
    semantic_type: str,
    semantic_id: str,
    *,
    owner: str | None = None,
    semantic_name: str | None = None,
) -> SemanticCandidate:
    return SemanticCandidate(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
        semantic_name=semantic_name or semantic_id,
        owner=owner,
        score=1.0,
        match_type="exact",
        evidence=[
            CandidateEvidence(
                term=semantic_id,
                source="test",
                matched_text=semantic_id,
            )
        ],
    )
