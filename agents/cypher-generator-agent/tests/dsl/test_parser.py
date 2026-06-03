from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.cypher_generator_agent.app.dsl.parser import (
    RestrictedDslValidationError,
    parse_restricted_query_dsl,
)
from services.cypher_generator_agent.app.semantic_model.loader import load_graph_semantic_model
from services.cypher_generator_agent.app.semantic_model.registry import GraphSemanticRegistry


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "network_topology_graph_model.yaml"
)


@pytest.fixture(scope="module")
def registry() -> GraphSemanticRegistry:
    return load_graph_semantic_model(FIXTURE_PATH).registry


def test_single_hop_dsl_parses_to_normalized_ast(registry: GraphSemanticRegistry) -> None:
    ast = parse_restricted_query_dsl(_single_hop_dsl(), registry)

    assert ast.schema_version == "restricted_query_dsl_v1"
    assert ast.query_shape.value == "single_hop_traversal"
    assert ast.operations[0].op.value == "traverse_edge"
    assert ast.operations[0].from_role.alias == "start"
    assert ast.operations[0].from_role.vertex_name == "Service"
    assert ast.operations[0].edge_role.edge_name == "SERVICE_USES_TUNNEL"
    assert ast.operations[0].to_role.alias == "end"
    assert ast.operations[0].to_role.vertex_name == "Tunnel"
    assert ast.projection.items[0].alias == "tunnel_id"
    assert ast.projection.items[0].target.alias == "end"
    assert ast.projection.items[0].property.owner == "Tunnel"
    assert ast.projection.items[0].property.name == "id"
    assert ast.filters[0].target.alias == "start"
    assert ast.filters[0].value.normalized == "GOLD"


def test_named_path_pattern_unknown_reference_fails(registry: GraphSemanticRegistry) -> None:
    dsl = _named_path_pattern_dsl()
    dsl["operations"][0]["path_pattern_name"] = "missing_path"

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert "unknown_path_pattern" in _error_codes(error.value)
    assert error.value.errors[0].location == "operations[0].path_pattern_name"


def test_named_path_pattern_outputs_typed_ast_node(registry: GraphSemanticRegistry) -> None:
    ast = parse_restricted_query_dsl(_named_path_pattern_dsl(), registry)

    operation = ast.operations[0]
    assert operation.op.value == "use_path_pattern"
    assert operation.path_pattern_name == "tunnel_full_path"
    assert operation.bind_as == "path"
    assert operation.parameters["tunnel_id"].normalized == "tun-mpls-001"
    assert ast.projection.items[0].source.namespace == "path"
    assert ast.projection.items[0].source.name == "device"


def test_path_pattern_required_parameter_must_have_value(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _named_path_pattern_dsl()
    dsl["operations"][0]["parameters"]["tunnel_id"] = {
        "raw": None,
        "normalized": None,
        "resolver_match_type": "value_index_exact",
    }

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"missing_path_pattern_parameter_value"}


def test_path_pattern_extra_parameter_fails(registry: GraphSemanticRegistry) -> None:
    dsl = _named_path_pattern_dsl()
    dsl["operations"][0]["parameters"]["extra"] = {
        "raw": "x",
        "normalized": "x",
        "resolver_match_type": "exact",
    }

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"unknown_path_pattern_parameter"}


def test_dimension_string_shorthand_fails(registry: GraphSemanticRegistry) -> None:
    dsl = _metric_aggregate_dsl()
    dsl["operations"][0]["group_by"] = [
        {
            "alias": "elem_type",
            "dimension": "ne.elem_type",
        }
    ]

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert "model_parse_error" in _error_codes(error.value)
    assert "dimension" in str(error.value)


@pytest.mark.parametrize("raw_key", ["raw_cypher", "cypher_fragment", "where_text"])
def test_rejects_raw_cypher_escape_hatch_anywhere(
    raw_key: str,
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _single_hop_dsl()
    dsl["operations"][0][raw_key] = "MATCH (n) RETURN n"

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"raw_cypher_attribute"}
    assert error.value.errors[0].location == f"operations[0].{raw_key}"


def test_path_pattern_required_parameters_must_be_covered(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _named_path_pattern_dsl()
    dsl["operations"][0]["parameters"] = {}

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"missing_path_pattern_parameter"}
    assert error.value.errors[0].location == "operations[0].parameters.tunnel_id"


def test_metric_group_by_must_be_valid_dimension(registry: GraphSemanticRegistry) -> None:
    dsl = _metric_aggregate_dsl()
    dsl["operations"][0]["group_by"][0]["property"]["name"] = "id"

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"invalid_metric_dimension"}


def test_metric_filter_requires_target(registry: GraphSemanticRegistry) -> None:
    dsl = _metric_aggregate_dsl()
    dsl["operations"][0]["filters"] = [
        {
            "property": {"owner": "NetworkElement", "name": "elem_type"},
            "operator": "eq",
            "value": "firewall",
        }
    ]

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"missing_filter_target"}


def test_metric_filter_must_be_valid_dimension(registry: GraphSemanticRegistry) -> None:
    dsl = _metric_aggregate_dsl()
    dsl["operations"][0]["filters"] = [
        {
            "target": "svc",
            "property": {"owner": "Service", "name": "service_type"},
            "operator": "eq",
            "value": "VPN",
        }
    ]

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"unknown_metric_alias"}


def test_aggregate_target_must_reference_binding(registry: GraphSemanticRegistry) -> None:
    dsl = _top_n_dsl_with_bindings()
    dsl["operations"][0]["measures"][0]["target"] = "missing"

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"unknown_aggregate_target"}


def test_aggregate_target_must_match_property_owner(registry: GraphSemanticRegistry) -> None:
    dsl = _top_n_dsl_with_bindings()
    dsl["operations"][0]["measures"][0]["property"] = {"owner": "NetworkElement", "name": "id"}

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"aggregate_owner_mismatch"}


def test_filter_target_must_match_property_owner(registry: GraphSemanticRegistry) -> None:
    dsl = _single_hop_dsl()
    dsl["filters"][0]["property"] = {"owner": "Port", "name": "status"}

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"filter_owner_mismatch"}


def test_filter_operator_must_be_supported(registry: GraphSemanticRegistry) -> None:
    dsl = _single_hop_dsl()
    dsl["filters"][0]["operator"] = "definitely_not_an_operator"

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert "model_parse_error" in _error_codes(error.value)


def test_projection_target_must_reference_known_binding(registry: GraphSemanticRegistry) -> None:
    dsl = _single_hop_dsl()
    dsl["projection"]["items"][0]["target"] = "missing"

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"unknown_projection_target"}


@pytest.mark.parametrize(
    ("source", "expected_code"),
    [
        ("group.missing", "unknown_source_output"),
        ("measure.missing", "unknown_source_output"),
        ("metric.missing", "unknown_source_output"),
        ("typo.device_count", "unknown_source_namespace"),
        ("device_count", "invalid_source_reference"),
    ],
)
def test_projection_source_must_reference_known_output(
    source: str,
    expected_code: str,
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _metric_aggregate_dsl()
    dsl["projection"]["items"][0]["source"] = source

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {expected_code}


def test_path_projection_source_must_reference_template_output(registry: GraphSemanticRegistry) -> None:
    dsl = _named_path_pattern_dsl()
    dsl["projection"]["items"][0]["source"] = "path.no_such_output"

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"unknown_source_output"}


def test_top_level_order_by_source_is_validated(registry: GraphSemanticRegistry) -> None:
    dsl = _metric_aggregate_dsl()
    dsl["order_by"] = [{"source": "group.missing", "direction": "asc"}]

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"unknown_source_output"}


def _single_hop_dsl() -> dict[str, Any]:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-single-hop",
        "query_shape": "single_hop_traversal",
        "source_question": "Gold 服务使用了哪些隧道",
        "bindings": {
            "start": {"vertex_name": "Service"},
            "edge": {"edge_name": "SERVICE_USES_TUNNEL"},
            "end": {"vertex_name": "Tunnel"},
        },
        "operations": [
            {
                "op": "traverse_edge",
                "from": "start",
                "edge": "edge",
                "to": "end",
                "direction": "forward",
            }
        ],
        "filters": [
            {
                "target": "start",
                "property": {"owner": "Service", "name": "quality_of_service"},
                "operator": "eq",
                "value": {
                    "raw": "Gold",
                    "normalized": "GOLD",
                    "resolver_match_type": "synonym",
                },
            }
        ],
        "projection": {
            "items": [
                {
                    "alias": "tunnel_id",
                    "target": "end",
                    "property": {"owner": "Tunnel", "name": "id"},
                }
            ]
        },
    }


def _named_path_pattern_dsl() -> dict[str, Any]:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-path",
        "query_shape": "named_path_pattern",
        "source_question": "隧道 tun-mpls-001 经过哪些设备",
        "bindings": {"primary_vertex": {"vertex_name": "Tunnel"}},
        "operations": [
            {
                "op": "use_path_pattern",
                "path_pattern_name": "tunnel_full_path",
                "bind_as": "path",
                "parameters": {
                    "tunnel_id": {
                        "raw": "tun-mpls-001",
                        "normalized": "tun-mpls-001",
                        "resolver_match_type": "value_index_exact",
                    }
                },
            }
        ],
        "projection": {
            "items": [
                {"alias": "device", "source": "path.device"},
                {"alias": "hop", "source": "path.hop"},
            ]
        },
    }


def _metric_aggregate_dsl() -> dict[str, Any]:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-metric",
        "query_shape": "metric_aggregate",
        "source_question": "按设备类型统计设备数量",
        "bindings": {"metric": {"metric_name": "device_count"}},
        "operations": [
            {
                "op": "metric_aggregate",
                "metric_name": "device_count",
                "group_by": [
                    {
                        "alias": "elem_type",
                        "target": "ne",
                        "property": {"owner": "NetworkElement", "name": "elem_type"},
                    }
                ],
                "filters": [],
            }
        ],
        "projection": {
            "items": [
                {"alias": "elem_type", "source": "group.elem_type"},
                {"alias": "device_count", "source": "metric.device_count"},
            ]
        },
    }


def _top_n_dsl_with_bindings() -> dict[str, Any]:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-top-n",
        "query_shape": "top_n",
        "source_question": "端口最多的 5 台设备",
        "bindings": {
            "device": {"vertex_name": "NetworkElement"},
            "port": {"vertex_name": "Port"},
        },
        "operations": [
            {
                "op": "aggregate",
                "group_by": [
                    {
                        "alias": "device",
                        "target": "device",
                        "property": {"owner": "NetworkElement", "name": "id"},
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
            {"op": "sort", "by": [{"source": "measure.port_count", "direction": "desc"}]},
            {"op": "limit", "value": 5},
        ],
        "projection": {
            "items": [
                {"alias": "device", "source": "group.device"},
                {"alias": "port_count", "source": "measure.port_count"},
            ]
        },
    }


def _error_codes(error: RestrictedDslValidationError) -> set[str]:
    return {issue.code for issue in error.errors}
