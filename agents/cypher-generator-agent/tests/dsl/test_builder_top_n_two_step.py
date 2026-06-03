from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.binding.models import BindingPlan, CandidateBinding, MetricBinding, VertexBinding
from services.cypher_generator_agent.app.dsl.builder import RestrictedDslBuilder
from services.cypher_generator_agent.app.dsl.parser import parse_restricted_query_dsl
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry, load_graph_semantic_model


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "network_topology_graph_model.yaml"


@pytest.fixture(scope="module")
def registry() -> GraphSemanticRegistry:
    return load_graph_semantic_model(FIXTURE_PATH).registry


def test_top_n_metric_plan_builds_sort_limit_dsl(registry: GraphSemanticRegistry) -> None:
    plan = BindingPlan(
        query_shape="top_n",
        metric_bindings=[MetricBinding(name="port_count", candidate=_candidate("metric", "port_count"))],
        group_by=[
            {
                "alias": "device",
                "target": "ne",
                "property": {"owner": "NetworkElement", "name": "id"},
            }
        ],
        projection=[
            {"alias": "device", "source": "group.device"},
            {"alias": "port_count", "source": "metric.port_count"},
        ],
        sort=[{"source": "metric.port_count", "direction": "desc"}],
        limit=5,
    )

    dsl = RestrictedDslBuilder(registry).build(
        plan,
        source_question="端口最多的 5 台设备",
        query_id="q-top-n-ports",
    )

    assert [operation["op"] for operation in dsl["operations"]] == ["metric_aggregate", "sort", "limit"]
    assert dsl["operations"][1] == {"op": "sort", "by": [{"source": "metric.port_count", "direction": "desc"}]}
    assert dsl["operations"][2] == {"op": "limit", "value": 5}
    ast = parse_restricted_query_dsl(dsl, registry)
    assert ast.query_shape.value == "top_n"


def test_two_step_plan_builds_subquery_sort_limit_dsl(registry: GraphSemanticRegistry) -> None:
    plan = BindingPlan(
        query_shape="two_step_aggregate",
        vertex_bindings=[
            VertexBinding(name="Port", candidate=_candidate("vertex", "Port")),
        ],
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
            {"alias": "status", "source": "port_status_counts.status"},
            {"alias": "port_count", "source": "port_status_counts.port_count"},
        ],
        sort=[{"source": "port_status_counts.port_count", "direction": "desc"}],
        limit=5,
    )

    dsl = RestrictedDslBuilder(registry).build(
        plan,
        source_question="先按状态统计端口，再取最多的 5 个状态",
        query_id="q-two-step-port-status",
    )

    assert [operation["op"] for operation in dsl["operations"]] == ["subquery", "sort", "limit"]
    assert dsl["operations"][0]["bind_as"] == "port_status_counts"
    assert dsl["operations"][0]["measures"][0]["alias"] == "port_count"
    ast = parse_restricted_query_dsl(dsl, registry)
    assert ast.query_shape.value == "two_step_aggregate"


def _candidate(semantic_type: str, semantic_id: str) -> CandidateBinding:
    return CandidateBinding(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
        semantic_name=semantic_id,
        score=1.0,
        match_type="exact",
    )
