from __future__ import annotations

from services.cypher_generator_agent.app.compiler import compile_restricted_query_ast
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry

from .conftest import parse_dsl


def test_top_n_metric_aggregate_compiles_order_and_limit(
    registry: GraphSemanticRegistry,
) -> None:
    ast = parse_dsl(_top_n_port_count_dsl(), registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher == (
        "MATCH (ne:NetworkElement)-[:HAS_PORT]->(port:Port)\n"
        "RETURN ne.id AS device, count(port) AS port_count\n"
        "ORDER BY port_count DESC\n"
        "LIMIT 5"
    )
    assert result.parameters == {}
    assert result.validation_result.valid is True


def test_top_n_path_aggregate_compiles_traversal_group_order_limit(
    registry: GraphSemanticRegistry,
) -> None:
    ast = parse_dsl(_top_n_service_tunnel_count_dsl(), registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher == (
        "MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun:Tunnel)\n"
        "RETURN tun.id AS tunnel_id, count(svc.id) AS service_count\n"
        "ORDER BY service_count DESC\n"
        "LIMIT 3"
    )
    assert result.parameters == {}
    assert result.validation_result.valid is True


def test_two_step_aggregate_compiles_with_chain(
    registry: GraphSemanticRegistry,
) -> None:
    ast = parse_dsl(_two_step_status_count_dsl(), registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher == (
        "MATCH (port:Port)\n"
        "WITH port.status AS status, count(port.id) AS port_count\n"
        "RETURN status AS status, port_count AS port_count\n"
        "ORDER BY port_count DESC\n"
        "LIMIT 5"
    )
    assert result.parameters == {}
    assert result.validation_result.valid is True


def test_two_step_aggregate_compiles_filter_subquery(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _two_step_status_count_dsl()
    dsl["operations"].insert(
        1,
        {
            "op": "filter_subquery",
            "source": "port_status_counts",
            "predicate": {"property": "port_count", "operator": "gt", "value": 10},
        },
    )
    ast = parse_dsl(dsl, registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher == (
        "MATCH (port:Port)\n"
        "WITH port.status AS status, count(port.id) AS port_count\n"
        "WHERE port_count > 10\n"
        "RETURN status AS status, port_count AS port_count\n"
        "ORDER BY port_count DESC\n"
        "LIMIT 5"
    )
    assert result.cypher_template == (
        "MATCH (port:Port)\n"
        "WITH port.status AS status, count(port.id) AS port_count\n"
        "WHERE port_count > $port_count\n"
        "RETURN status AS status, port_count AS port_count\n"
        "ORDER BY port_count DESC\n"
        "LIMIT 5"
    )
    assert result.parameters == {"port_count": 10}


def test_two_step_path_aggregate_compiles_two_match_with_anchor_carry(
    registry: GraphSemanticRegistry,
) -> None:
    ast = parse_dsl(_two_step_path_service_qos_dsl(), registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher == (
        "MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun:Tunnel)-[:TUNNEL_DST]->(ne:NetworkElement)\n"
        "WITH svc, count(ne.id) AS first_total\n"
        "MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun2:Tunnel)-[:TUNNEL_DST]->(ne2:NetworkElement)\n"
        "RETURN svc.quality_of_service AS key, first_total AS first_total, count(ne2.id) AS total\n"
        "ORDER BY first_total ASC\n"
        "LIMIT 5"
    )
    assert result.parameters == {}
    assert result.validation_result.valid is True


def _top_n_port_count_dsl() -> dict:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-top-n-ports",
        "query_shape": "top_n",
        "source_question": "端口最多的 5 台设备",
        "bindings": {"metric": {"metric_name": "port_count"}},
        "operations": [
            {
                "op": "metric_aggregate",
                "metric_name": "port_count",
                "group_by": [
                    {
                        "alias": "device",
                        "target": "ne",
                        "property": {"owner": "NetworkElement", "name": "id"},
                    }
                ],
                "filters": [],
            },
            {"op": "sort", "by": [{"source": "metric.port_count", "direction": "desc"}]},
            {"op": "limit", "value": 5},
        ],
        "projection": {
            "items": [
                {"alias": "device", "source": "group.device"},
                {"alias": "port_count", "source": "metric.port_count"},
            ]
        },
    }


def _top_n_service_tunnel_count_dsl() -> dict:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-top-n-service-tunnel-count",
        "query_shape": "top_n",
        "source_question": "按隧道统计服务数量，返回前 3 个",
        "bindings": {
            "v0": {"vertex_name": "Service"},
            "edge_0": {"edge_name": "SERVICE_USES_TUNNEL"},
            "v1": {"vertex_name": "Tunnel"},
        },
        "operations": [
            {
                "op": "traverse_edge",
                "from": "v0",
                "edge": "edge_0",
                "to": "v1",
                "direction": "forward",
            },
            {
                "op": "aggregate",
                "group_by": [
                    {
                        "alias": "tunnel_id",
                        "target": "v1",
                        "property": {"owner": "Tunnel", "name": "id"},
                    }
                ],
                "measures": [
                    {
                        "alias": "service_count",
                        "function": "count",
                        "target": "v0",
                        "property": {"owner": "Service", "name": "id"},
                    }
                ],
            },
            {"op": "sort", "by": [{"source": "measure.service_count", "direction": "desc"}]},
            {"op": "limit", "value": 3},
        ],
        "projection": {
            "items": [
                {"alias": "tunnel_id", "source": "group.tunnel_id"},
                {"alias": "service_count", "source": "measure.service_count"},
            ]
        },
    }


def _two_step_status_count_dsl() -> dict:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-two-step-port-status",
        "query_shape": "two_step_aggregate",
        "source_question": "先按状态统计端口，再取最多的 5 个状态",
        "bindings": {"port": {"vertex_name": "Port"}},
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


def _two_step_path_service_qos_dsl() -> dict:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-two-step-path-service-qos",
        "query_shape": "two_step_aggregate",
        "source_question": "先统计服务目的网元数量，再按服务质量等级返回连接总数",
        "bindings": {
            "v0": {"vertex_name": "Service"},
            "edge_0": {"edge_name": "SERVICE_USES_TUNNEL"},
            "v1": {"vertex_name": "Tunnel"},
            "edge_1": {"edge_name": "TUNNEL_DST"},
            "v2": {"vertex_name": "NetworkElement"},
            "edge_2": {"edge_name": "SERVICE_USES_TUNNEL"},
            "v3": {"vertex_name": "Tunnel"},
            "edge_3": {"edge_name": "TUNNEL_DST"},
            "v4": {"vertex_name": "NetworkElement"},
        },
        "operations": [
            {
                "op": "subquery",
                "bind_as": "first_counts",
                "query_shape": "single_hop_traversal",
                "carry_roles": ["v0"],
                "operations": [
                    {"op": "traverse_edge", "from": "v0", "edge": "edge_0", "to": "v1", "direction": "forward"},
                    {"op": "traverse_edge", "from": "v1", "edge": "edge_1", "to": "v2", "direction": "forward"},
                ],
                "group_by": [],
                "measures": [
                    {
                        "alias": "first_total",
                        "function": "count",
                        "target": "v2",
                        "property": {"owner": "NetworkElement", "name": "id"},
                    }
                ],
            },
            {"op": "traverse_edge", "from": "v0", "edge": "edge_2", "to": "v3", "direction": "forward"},
            {"op": "traverse_edge", "from": "v3", "edge": "edge_3", "to": "v4", "direction": "forward"},
            {
                "op": "aggregate",
                "group_by": [],
                "measures": [
                    {
                        "alias": "total",
                        "function": "count",
                        "target": "v4",
                        "property": {"owner": "NetworkElement", "name": "id"},
                    }
                ],
            },
            {"op": "sort", "by": [{"source": "first_counts.first_total", "direction": "asc"}]},
            {"op": "limit", "value": 5},
        ],
        "projection": {
            "items": [
                {
                    "alias": "key",
                    "target": "v0",
                    "property": {"owner": "Service", "name": "quality_of_service"},
                },
                {"alias": "first_total", "source": "first_counts.first_total"},
                {"alias": "total", "source": "measure.total"},
            ]
        },
    }
