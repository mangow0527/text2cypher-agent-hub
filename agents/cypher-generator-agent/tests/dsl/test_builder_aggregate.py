from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.cypher_generator_agent.app.binding.models import (
    BindingPlan,
    CandidateBinding,
    FilterBinding,
    LiteralBinding,
    MetricBinding,
    VertexBinding,
)
from services.cypher_generator_agent.app.compiler import compile_restricted_query_ast
from services.cypher_generator_agent.app.dsl.builder import RestrictedDslBuilder
from services.cypher_generator_agent.app.dsl.parser import parse_restricted_query_dsl
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry, load_graph_semantic_model


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "network_topology_graph_model.yaml"


@pytest.fixture(scope="module")
def registry() -> GraphSemanticRegistry:
    return load_graph_semantic_model(FIXTURE_PATH).registry


def test_metric_aggregate_plan_builds_metric_dsl(registry: GraphSemanticRegistry) -> None:
    literal = LiteralBinding(
        raw_literal="防火墙",
        resolved=True,
        value="firewall",
        normalized_value="firewall",
        match_type="value_synonym",
        confidence=1.0,
        owner="NetworkElement",
        property="elem_type",
    )
    plan = BindingPlan(
        query_shape="metric_aggregate",
        metric_bindings=[MetricBinding(name="device_count", candidate=_candidate("metric", "device_count"))],
        literal_bindings=[literal],
        filters=[
            FilterBinding(
                owner="NetworkElement",
                property="elem_type",
                operator="eq",
                raw_literal="防火墙",
                value="firewall",
                literal=literal,
            )
        ],
        projection=[{"alias": "device_count", "source": "metric.device_count"}],
    )

    dsl = RestrictedDslBuilder(registry).build(
        plan,
        source_question="全网有多少台防火墙",
        query_id="q-device-count-firewall",
    )

    assert dsl == {
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
    ast = parse_restricted_query_dsl(dsl, registry)
    assert ast.operations[0].metric_name == "device_count"
    assert ast.operations[0].filters[0].target.alias == "ne"


def test_ad_hoc_aggregate_plan_builds_aggregate_dsl(registry: GraphSemanticRegistry) -> None:
    plan = BindingPlan(
        query_shape="ad_hoc_aggregate",
        vertex_bindings=[VertexBinding(name="Port", candidate=_candidate("vertex", "Port"))],
        group_by=[
            {
                "alias": "status",
                "target": "port",
                "property": {"owner": "Port", "name": "status"},
            }
        ],
        measures=[
            {
                "alias": "port_count",
                "function": "count",
                "target": "port",
                "property": {"owner": "Port", "name": "id"},
            }
        ],
        projection=[
            {"alias": "status", "source": "group.status"},
            {"alias": "port_count", "source": "measure.port_count"},
        ],
    )

    dsl = RestrictedDslBuilder(registry).build(
        plan,
        source_question="按状态统计端口数量",
        query_id="q-port-count-by-status",
    )

    assert dsl["bindings"] == {"port": {"vertex_name": "Port"}}
    assert dsl["operations"] == [
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
    ]
    ast = parse_restricted_query_dsl(dsl, registry)
    assert ast.operations[0].measures[0].alias == "port_count"


def test_two_step_aggregate_property_projection_alias_maps_to_measure_source(
    registry: GraphSemanticRegistry,
) -> None:
    plan = BindingPlan(
        query_shape="two_step_aggregate",
        vertex_bindings=[
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
            VertexBinding(name="NetworkElement", candidate=_candidate("vertex", "NetworkElement")),
        ],
        group_by=[
            {
                "alias": "network_element_location",
                "target": "network_element",
                "property": {"owner": "NetworkElement", "name": "location"},
            }
        ],
        measures=[
            {
                "alias": "cnt",
                "function": "count",
                "target": "network_element",
                "property": {"owner": "NetworkElement", "name": "id"},
            }
        ],
        projection=[
            {"alias": "network_element_location", "source": "group.network_element_location"},
            {"semantic_type": "property", "owner": "NetworkElement", "name": "id", "alias": "cnt"},
        ],
        sort=[{"source": "measure.cnt", "direction": "asc"}],
        limit=10,
    )

    dsl = RestrictedDslBuilder(registry).build(
        plan,
        source_question="统计服务所经隧道穿过的网络元素中，按位置分组统计数量。",
        query_id="q-network-element-location-count",
    )

    assert dsl["projection"]["items"] == [
        {"alias": "network_element_location", "source": "subquery.network_element_location"},
        {"alias": "cnt", "source": "subquery.cnt"},
    ]
    assert dsl["operations"][1] == {
        "op": "sort",
        "by": [{"source": "subquery.cnt", "direction": "asc"}],
    }
    ast = parse_restricted_query_dsl(dsl, registry)
    assert ast.projection.items[1].source.raw == "subquery.cnt"
    assert compile_restricted_query_ast(ast, registry).cypher == (
        "MATCH (ne:NetworkElement)\n"
        "WITH ne.location AS network_element_location, count(ne.id) AS cnt\n"
        "RETURN network_element_location AS network_element_location, cnt AS cnt\n"
        "ORDER BY cnt ASC\n"
        "LIMIT 10"
    )


def test_ad_hoc_aggregate_property_projection_alias_maps_to_measure_source(
    registry: GraphSemanticRegistry,
) -> None:
    plan = BindingPlan(
        query_shape="ad_hoc_aggregate",
        vertex_bindings=[VertexBinding(name="Port", candidate=_candidate("vertex", "Port"))],
        measures=[
            {
                "alias": "port_count",
                "function": "count",
                "target": "port",
                "property": {"owner": "Port", "name": "id"},
            }
        ],
        projection=[
            {"semantic_type": "property", "owner": "Port", "name": "id", "alias": "port_count"}
        ],
    )

    dsl = RestrictedDslBuilder(registry).build(
        plan,
        source_question="统计端口数量",
        query_id="q-port-count",
    )

    assert dsl["projection"]["items"] == [{"alias": "port_count", "source": "measure.port_count"}]
    ast = parse_restricted_query_dsl(dsl, registry)
    assert ast.projection.items[0].source.raw == "measure.port_count"


def test_aggregate_property_projection_raises_when_output_match_is_ambiguous(
    registry: GraphSemanticRegistry,
) -> None:
    plan = BindingPlan(
        query_shape="ad_hoc_aggregate",
        vertex_bindings=[VertexBinding(name="Port", candidate=_candidate("vertex", "Port"))],
        group_by=[
            {
                "alias": "port_id",
                "target": "port",
                "property": {"owner": "Port", "name": "id"},
            }
        ],
        measures=[
            {
                "alias": "port_count",
                "function": "count",
                "target": "port",
                "property": {"owner": "Port", "name": "id"},
            }
        ],
        projection=[{"semantic_type": "property", "owner": "Port", "name": "id"}],
    )

    with pytest.raises(ValueError, match="ambiguous aggregate projection"):
        RestrictedDslBuilder(registry).build(
            plan,
            source_question="按端口统计端口数量",
            query_id="q-ambiguous-port-id",
        )


def _candidate(semantic_type: str, semantic_id: str) -> CandidateBinding:
    return CandidateBinding(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
        semantic_name=semantic_id,
        score=1.0,
        match_type="exact",
    )
