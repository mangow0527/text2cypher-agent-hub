from __future__ import annotations

from copy import deepcopy
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


def test_top_n_requires_limit_operation(registry: GraphSemanticRegistry) -> None:
    dsl = _top_n_dsl()
    dsl["operations"] = [op for op in dsl["operations"] if op["op"] != "limit"]

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"invalid_operation_sequence"}
    assert "top_n" in error.value.errors[0].message
    assert "limit" in error.value.errors[0].message


def test_two_step_aggregate_nested_subquery_fails(registry: GraphSemanticRegistry) -> None:
    dsl = _two_step_aggregate_dsl()
    dsl["operations"][0]["operations"] = [
        {
            "op": "subquery",
            "bind_as": "nested",
            "query_shape": "ad_hoc_aggregate",
            "group_by": [],
            "measures": [
                {
                    "alias": "nested_count",
                    "function": "count",
                    "target": "port",
                    "property": {"owner": "Port", "name": "id"},
                }
            ],
        }
    ]

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"nested_subquery_not_allowed"}
    assert error.value.errors[0].location == "operations[0].operations[0]"


def test_two_step_aggregate_subquery_shape_must_be_ad_hoc_aggregate(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _two_step_aggregate_dsl()
    dsl["operations"][0]["query_shape"] = "metric_aggregate"

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"invalid_subquery_shape"}


def test_two_step_aggregate_subquery_requires_measures(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _two_step_aggregate_dsl()
    dsl["operations"][0]["measures"] = []

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert "missing_subquery_measures" in _error_codes(error.value)


def test_filter_subquery_source_must_reference_subquery(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _two_step_aggregate_dsl()
    dsl["operations"][1]["source"] = "missing_source"

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"unknown_subquery_source"}


def test_filter_subquery_predicate_property_must_reference_subquery_output(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _two_step_aggregate_dsl()
    dsl["operations"][1]["predicate"]["property"] = "missing_output"

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"unknown_subquery_output"}


def test_variable_path_through_filter_target_must_match_owner(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _variable_path_dsl()
    dsl["operations"][0]["through"]["filters"][0]["target"] = "through"
    dsl["operations"][0]["through"]["filters"][0]["property"] = {"owner": "Port", "name": "status"}

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"filter_owner_mismatch"}


def test_variable_path_through_filter_unknown_target_fails(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _variable_path_dsl()
    dsl["operations"][0]["through"]["filters"][0]["target"] = "missing"

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"unknown_filter_target"}


def test_variable_path_min_hops_cannot_exceed_max_hops(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _variable_path_dsl()
    dsl["operations"][0]["min_hops"] = 9
    dsl["operations"][0]["max_hops"] = 1

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert "model_parse_error" in _error_codes(error.value)


def test_filter_subquery_operator_must_be_supported(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _two_step_aggregate_dsl()
    dsl["operations"][1]["predicate"]["operator"] = "definitely_not_an_operator"

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert "model_parse_error" in _error_codes(error.value)


def test_two_step_aggregate_subquery_must_use_single_vertex_role(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _two_step_aggregate_dsl()
    dsl["bindings"]["device"] = {"vertex_name": "NetworkElement"}
    dsl["operations"][0]["group_by"][0] = {
        "alias": "device",
        "target": "device",
        "property": {"owner": "NetworkElement", "name": "id"},
    }
    dsl["projection"]["items"][0] = {"alias": "device", "source": "port_status_counts.device"}

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert _error_codes(error.value) == {"unsupported_subquery_vertex_roles"}


@pytest.mark.parametrize(
    ("query_shape", "ops"),
    [
        ("vertex_lookup", ["aggregate"]),
        ("single_hop_traversal", ["traverse_edge", "aggregate"]),
        ("variable_path_traversal", ["variable_path", "sort", "sort"]),
        ("named_path_pattern", ["use_path_pattern", "limit", "sort"]),
        ("metric_aggregate", ["metric_aggregate", "limit", "sort"]),
        ("ad_hoc_aggregate", ["aggregate", "subquery"]),
        ("two_step_aggregate", ["subquery", "limit", "filter_subquery"]),
    ],
)
def test_invalid_operation_sequences_fail(
    query_shape: str,
    ops: list[str],
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _shape_dsl(query_shape)
    operation_templates = {operation["op"]: operation for operation in _all_operations()}
    dsl["operations"] = [deepcopy(operation_templates[op]) for op in ops]

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert "invalid_operation_sequence" in _error_codes(error.value)


def _top_n_dsl() -> dict[str, Any]:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-top-n",
        "query_shape": "top_n",
        "source_question": "端口最多的 5 台设备",
        "bindings": {},
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


def _two_step_aggregate_dsl() -> dict[str, Any]:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-two-step",
        "query_shape": "two_step_aggregate",
        "source_question": "先按状态统计端口，再取最多的 5 个状态",
        "bindings": {
            "port": {"vertex_name": "Port"},
        },
        "operations": [
            {
                "op": "subquery",
                "bind_as": "port_status_counts",
                "query_shape": "ad_hoc_aggregate",
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
            {
                "op": "filter_subquery",
                "source": "port_status_counts",
                "predicate": {"property": "port_count", "operator": "gt", "value": 10},
            },
            {"op": "sort", "by": [{"source": "port_status_counts.port_count", "direction": "desc"}]},
            {"op": "limit", "value": 5},
        ],
        "projection": {
            "items": [
                {"alias": "status", "source": "port_status_counts.status"},
                {"alias": "port_count", "source": "port_status_counts.port_count"},
            ]
        },
    }


def _variable_path_dsl() -> dict[str, Any]:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-variable-path",
        "query_shape": "variable_path_traversal",
        "source_question": "找出所有经过设备 ne-0001 的隧道",
        "bindings": {
            "start": {"vertex_name": "Tunnel"},
            "through": {"vertex_name": "NetworkElement"},
        },
        "operations": [
            {
                "op": "variable_path",
                "bind_as": "path",
                "start": "start",
                "through": {
                    "vertex_ref": "through",
                    "filters": [
                        {
                            "property": {"owner": "NetworkElement", "name": "id"},
                            "operator": "eq",
                            "value": {
                                "raw": "ne-0001",
                                "normalized": "ne-0001",
                                "resolver_match_type": "value_index_exact",
                            },
                        }
                    ],
                },
                "allowed_edges": ["PATH_THROUGH"],
                "min_hops": 1,
                "max_hops": 8,
            }
        ],
        "projection": {
            "items": [
                {
                    "alias": "tunnel_id",
                    "target": "start",
                    "property": {"owner": "Tunnel", "name": "id"},
                }
            ]
        },
    }


def _shape_dsl(query_shape: str) -> dict[str, Any]:
    dsl = _top_n_dsl()
    dsl["query_id"] = f"q-{query_shape}"
    dsl["query_shape"] = query_shape
    if query_shape == "vertex_lookup":
        dsl["bindings"] = {"device": {"vertex_name": "NetworkElement"}}
    if query_shape == "two_step_aggregate":
        dsl = _two_step_aggregate_dsl()
    return dsl


def _all_operations() -> list[dict[str, Any]]:
    return [
        {
            "op": "traverse_edge",
            "from": "start",
            "edge": "edge",
            "to": "end",
            "direction": "forward",
        },
        {
            "op": "variable_path",
            "bind_as": "path",
            "start": "start",
            "through": {
                "vertex_ref": "through",
                "filters": [
                    {
                        "property": {"owner": "NetworkElement", "name": "id"},
                        "operator": "eq",
                        "value": {
                            "raw": "ne-0001",
                            "normalized": "ne-0001",
                            "resolver_match_type": "value_index_exact",
                        },
                    }
                ],
            },
            "allowed_edges": ["PATH_THROUGH"],
            "min_hops": 1,
            "max_hops": 8,
        },
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
        },
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
        },
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
                    "alias": "device_count",
                    "function": "count",
                    "target": "device",
                    "property": {"owner": "NetworkElement", "name": "id"},
                }
            ],
        },
        {"op": "sort", "by": [{"source": "measure.device_count", "direction": "desc"}]},
        {"op": "limit", "value": 5},
        {
            "op": "subquery",
            "bind_as": "device_counts",
            "query_shape": "ad_hoc_aggregate",
            "group_by": [
                {
                    "alias": "device",
                    "target": "device",
                    "property": {"owner": "NetworkElement", "name": "id"},
                }
            ],
            "measures": [
                {
                    "alias": "device_count",
                    "function": "count",
                    "target": "device",
                    "property": {"owner": "NetworkElement", "name": "id"},
                }
            ],
        },
        {
            "op": "filter_subquery",
            "source": "device_counts",
            "predicate": {"property": "device_count", "operator": "gt", "value": 10},
        },
    ]


def _error_codes(error: RestrictedDslValidationError) -> set[str]:
    return {issue.code for issue in error.errors}
