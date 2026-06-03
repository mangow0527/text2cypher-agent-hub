from __future__ import annotations

from services.cypher_generator_agent.app.compiler import compile_restricted_query_ast
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry

from .conftest import parse_dsl


def test_metric_aggregate_compiles_inline_filter(
    registry: GraphSemanticRegistry,
) -> None:
    ast = parse_dsl(_device_count_firewall_dsl(), registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher == (
        "MATCH (ne:NetworkElement)\n"
        "WHERE ne.elem_type = 'firewall'\n"
        "RETURN count(ne) AS device_count"
    )
    assert result.cypher_template == (
        "MATCH (ne:NetworkElement)\n"
        "WHERE ne.elem_type = $elem_type\n"
        "RETURN count(ne) AS device_count"
    )
    assert result.parameters == {"elem_type": "firewall"}
    assert "$elem_type" not in result.cypher
    assert result.validation_result.valid is True


def test_metric_aggregate_compiles_group_by_dimension(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _device_count_firewall_dsl()
    dsl["source_question"] = "按设备类型统计设备数量"
    dsl["operations"][0]["filters"] = []
    dsl["operations"][0]["group_by"] = [
        {
            "alias": "elem_type",
            "target": "ne",
            "property": {"owner": "NetworkElement", "name": "elem_type"},
        }
    ]
    dsl["projection"]["items"] = [
        {"alias": "elem_type", "source": "group.elem_type"},
        {"alias": "device_count", "source": "metric.device_count"},
    ]
    ast = parse_dsl(dsl, registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher == (
        "MATCH (ne:NetworkElement)\n"
        "RETURN ne.elem_type AS elem_type, count(ne) AS device_count"
    )
    assert result.parameters == {}
    assert result.validation_result.valid is True


def test_metric_aggregate_compiles_sort_and_limit_tail(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _device_count_firewall_dsl()
    dsl["operations"][0]["filters"] = []
    dsl["operations"][0]["group_by"] = [
        {
            "alias": "elem_type",
            "target": "ne",
            "property": {"owner": "NetworkElement", "name": "elem_type"},
        }
    ]
    dsl["projection"]["items"] = [
        {"alias": "elem_type", "source": "group.elem_type"},
        {"alias": "device_count", "source": "metric.device_count"},
    ]
    dsl["operations"].extend(
        [
            {"op": "sort", "by": [{"source": "metric.device_count", "direction": "desc"}]},
            {"op": "limit", "value": 5},
        ]
    )
    ast = parse_dsl(dsl, registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher == (
        "MATCH (ne:NetworkElement)\n"
        "RETURN ne.elem_type AS elem_type, count(ne) AS device_count\n"
        "ORDER BY device_count DESC\n"
        "LIMIT 5"
    )


def test_ad_hoc_aggregate_compiles_count_by_status(
    registry: GraphSemanticRegistry,
) -> None:
    ast = parse_dsl(_port_count_by_status_dsl(), registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher == (
        "MATCH (port:Port)\n"
        "RETURN port.status AS status, count(port.id) AS port_count"
    )
    assert result.parameters == {}
    assert result.validation_result.valid is True


def test_ad_hoc_aggregate_compiles_sort_and_limit_tail(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _port_count_by_status_dsl()
    dsl["operations"].extend(
        [
            {"op": "sort", "by": [{"source": "measure.port_count", "direction": "desc"}]},
            {"op": "limit", "value": 5},
        ]
    )
    ast = parse_dsl(dsl, registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher == (
        "MATCH (port:Port)\n"
        "RETURN port.status AS status, count(port.id) AS port_count\n"
        "ORDER BY port_count DESC\n"
        "LIMIT 5"
    )


def _device_count_firewall_dsl() -> dict:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-device-count-firewall",
        "query_shape": "metric_aggregate",
        "source_question": "全网有多少台防火墙",
        "bindings": {"metric": {"metric_name": "device_count"}},
        "operations": [
            {
                "op": "metric_aggregate",
                "metric_name": "device_count",
                "group_by": [],
                "filters": [
                    {
                        "target": "ne",
                        "property": {"owner": "NetworkElement", "name": "elem_type"},
                        "operator": "eq",
                        "value": {
                            "raw": "防火墙",
                            "normalized": "firewall",
                            "resolver_match_type": "value_synonym",
                        },
                    }
                ],
            }
        ],
        "projection": {"items": [{"alias": "device_count", "source": "metric.device_count"}]},
    }


def _port_count_by_status_dsl() -> dict:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-port-count-by-status",
        "query_shape": "ad_hoc_aggregate",
        "source_question": "按状态统计端口数量",
        "bindings": {"port": {"vertex_name": "Port"}},
        "operations": [
            {
                "op": "aggregate",
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
            }
        ],
        "projection": {
            "items": [
                {"alias": "status", "source": "group.status"},
                {"alias": "port_count", "source": "measure.port_count"},
            ]
        },
    }
