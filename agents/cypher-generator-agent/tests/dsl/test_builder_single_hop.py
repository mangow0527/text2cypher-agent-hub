from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.cypher_generator_agent.app.binding.models import (
    BindingPlan,
    CandidateBinding,
    EdgeBinding,
    FilterBinding,
    LiteralBinding,
    VertexBinding,
)
from services.cypher_generator_agent.app.dsl.builder import RestrictedDslBuilder
from services.cypher_generator_agent.app.dsl.parser import parse_restricted_query_dsl
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry, load_graph_semantic_model


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "network_topology_graph_model.yaml"


@pytest.fixture(scope="module")
def registry() -> GraphSemanticRegistry:
    return load_graph_semantic_model(FIXTURE_PATH).registry


def test_gold_service_tunnel_plan_builds_single_hop_dsl(registry: GraphSemanticRegistry) -> None:
    literal = LiteralBinding(
        raw_literal="Gold",
        resolved=True,
        value="GOLD",
        normalized_value="GOLD",
        match_type="value_synonym",
        confidence=0.98,
        owner="Service",
        property="quality_of_service",
    )
    plan = BindingPlan(
        query_shape="single_hop_traversal",
        vertex_bindings=[
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
            VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel")),
        ],
        edge_bindings=[
            EdgeBinding(
                name="SERVICE_USES_TUNNEL",
                candidate=_candidate("edge", "SERVICE_USES_TUNNEL"),
                direction="forward",
            )
        ],
        literal_bindings=[literal],
        filters=[
            FilterBinding(
                owner="Service",
                property="quality_of_service",
                operator="eq",
                raw_literal="Gold",
                value="GOLD",
                literal=literal,
            )
        ],
        projection=[
            {
                "semantic_type": "property",
                "owner": "Tunnel",
                "name": "id",
                "alias": "tunnel_id",
            }
        ],
        assumptions=[
            {
                "type": "literal_fuzzy_match",
                "raw_literal": "Gold",
                "owner": "Service",
                "property": "quality_of_service",
                "value": "GOLD",
                "confidence": 0.98,
            }
        ],
    )

    dsl = RestrictedDslBuilder(registry).build(
        plan,
        source_question="Gold 服务使用了哪些隧道",
        query_id="q-single-hop",
    )

    assert dsl == {
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
                    "resolver_match_type": "value_synonym",
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
        "assumptions": [
            {
                "type": "literal_fuzzy_match",
                "raw_literal": "Gold",
                "owner": "Service",
                "property": "quality_of_service",
                "value": "GOLD",
                "confidence": 0.98,
            }
        ],
    }
    assert "raw_cypher" not in _all_keys(dsl)
    ast = parse_restricted_query_dsl(dsl, registry)
    assert ast.operations[0].edge_role.edge_name == "SERVICE_USES_TUNNEL"
    assert ast.filters[0].target.alias == "start"
    assert ast.projection.items[0].target.alias == "end"


def test_single_hop_uses_backward_edge_direction(registry: GraphSemanticRegistry) -> None:
    plan = BindingPlan(
        query_shape="single_hop_traversal",
        vertex_bindings=[
            VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel")),
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
        ],
        edge_bindings=[
            EdgeBinding(
                name="SERVICE_USES_TUNNEL",
                candidate=_candidate("edge", "SERVICE_USES_TUNNEL"),
                direction="backward",
            )
        ],
        projection=[{"target": "end", "property": {"owner": "Service", "name": "id"}}],
    )

    dsl = RestrictedDslBuilder(registry).build(
        plan,
        source_question="哪些服务使用了这个隧道",
        query_id="q-backward",
    )

    assert dsl["operations"][0]["direction"] == "backward"
    assert dsl["operations"][0]["from"] == "start"
    assert dsl["operations"][0]["to"] == "end"
    assert dsl["bindings"]["start"] == {"vertex_name": "Tunnel"}
    assert dsl["bindings"]["end"] == {"vertex_name": "Service"}


def test_single_hop_builder_emits_multihop_traversal_chain(
    registry: GraphSemanticRegistry,
) -> None:
    plan = BindingPlan(
        query_shape="single_hop_traversal",
        vertex_bindings=[
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
            VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel")),
            VertexBinding(name="NetworkElement", candidate=_candidate("vertex", "NetworkElement")),
        ],
        edge_bindings=[
            EdgeBinding(name="SERVICE_USES_TUNNEL", candidate=_candidate("edge", "SERVICE_USES_TUNNEL")),
            EdgeBinding(name="PATH_THROUGH", candidate=_candidate("edge", "PATH_THROUGH")),
        ],
        projection=[
            {
                "semantic_type": "property",
                "owner": "NetworkElement",
                "name": "name",
                "alias": "network_element_name",
            }
        ],
    )

    dsl = RestrictedDslBuilder(registry).build(
        plan,
        source_question="查询服务经过隧道穿过的网元IP地址",
        query_id="q-multihop-chain",
    )

    assert dsl["bindings"] == {
        "v0": {"vertex_name": "Service"},
        "v1": {"vertex_name": "Tunnel"},
        "v2": {"vertex_name": "NetworkElement"},
        "e0": {"edge_name": "SERVICE_USES_TUNNEL"},
        "e1": {"edge_name": "PATH_THROUGH"},
    }
    assert dsl["operations"] == [
        {"op": "traverse_edge", "from": "v0", "edge": "e0", "to": "v1", "direction": "forward"},
        {"op": "traverse_edge", "from": "v1", "edge": "e1", "to": "v2", "direction": "forward"},
    ]
    assert dsl["projection"]["items"] == [
        {
            "alias": "network_element_name",
            "target": "v2",
            "property": {"owner": "NetworkElement", "name": "name"},
        }
    ]
    parse_restricted_query_dsl(dsl, registry)


def test_builder_rejects_bare_vertex_projection_instead_of_guessing_id(
    registry: GraphSemanticRegistry,
) -> None:
    plan = BindingPlan(
        query_shape="single_hop_traversal",
        vertex_bindings=[
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
            VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel")),
        ],
        edge_bindings=[
            EdgeBinding(name="SERVICE_USES_TUNNEL", candidate=_candidate("edge", "SERVICE_USES_TUNNEL")),
        ],
        projection=[{"semantic_type": "vertex", "name": "Tunnel"}],
    )

    with pytest.raises(ValueError, match="ambiguous bare vertex projection"):
        RestrictedDslBuilder(registry).build(
            plan,
            source_question="Gold 服务使用了哪些隧道",
            query_id="q-bare-vertex",
        )


def test_builder_accepts_explicit_vertex_full_projection(
    registry: GraphSemanticRegistry,
) -> None:
    plan = BindingPlan(
        query_shape="vertex_lookup",
        vertex_bindings=[
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
        ],
        projection=[{"semantic_type": "vertex_full", "name": "Service", "alias": "service"}],
    )

    dsl = RestrictedDslBuilder(registry).build(
        plan,
        source_question="查询所有服务",
        query_id="q-service-full",
    )

    assert dsl["projection"] == {
        "items": [
            {
                "alias": "service",
                "target": "target",
                "vertex_full": True,
            }
        ]
    }
    ast = parse_restricted_query_dsl(dsl, registry)
    assert ast.projection.items[0].vertex_full is True


def test_builder_rejects_property_projection_when_owner_is_not_bound(
    registry: GraphSemanticRegistry,
) -> None:
    plan = BindingPlan(
        query_shape="single_hop_traversal",
        vertex_bindings=[
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
            VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel")),
        ],
        edge_bindings=[
            EdgeBinding(name="SERVICE_USES_TUNNEL", candidate=_candidate("edge", "SERVICE_USES_TUNNEL")),
        ],
        projection=[{"semantic_type": "property", "owner": "Port", "name": "id"}],
    )

    with pytest.raises(ValueError, match="projection owner Port is not bound"):
        RestrictedDslBuilder(registry).build(
            plan,
            source_question="查询服务相关端口",
            query_id="q-missing-projection-owner",
        )


def test_builder_rejects_vertex_full_projection_when_owner_is_not_bound(
    registry: GraphSemanticRegistry,
) -> None:
    plan = BindingPlan(
        query_shape="vertex_lookup",
        vertex_bindings=[
            VertexBinding(name="Port", candidate=_candidate("vertex", "Port")),
        ],
        projection=[{"semantic_type": "vertex_full", "name": "Service"}],
    )

    with pytest.raises(ValueError, match="projection owner Service is not bound"):
        RestrictedDslBuilder(registry).build(
            plan,
            source_question="查询端口关联服务",
            query_id="q-missing-vertex-full-owner",
        )


def test_single_hop_rejects_sort_or_limit_until_compiler_supports_them(
    registry: GraphSemanticRegistry,
) -> None:
    plan = BindingPlan(
        query_shape="single_hop_traversal",
        vertex_bindings=[
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
            VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel")),
        ],
        edge_bindings=[
            EdgeBinding(name="SERVICE_USES_TUNNEL", candidate=_candidate("edge", "SERVICE_USES_TUNNEL")),
        ],
        projection=[{"semantic_type": "vertex", "name": "Tunnel"}],
        limit=10,
    )

    with pytest.raises(ValueError, match="sort/limit"):
        RestrictedDslBuilder(registry).build(
            plan,
            source_question="Gold 服务使用了哪些隧道",
            query_id="q-single-hop-limit",
        )


def test_vertex_lookup_allows_limit_operation(
    registry: GraphSemanticRegistry,
) -> None:
    plan = BindingPlan(
        query_shape="vertex_lookup",
        vertex_bindings=[VertexBinding(name="Service", candidate=_candidate("vertex", "Service"))],
        projection=[{"semantic_type": "property", "owner": "Service", "name": "id"}],
        limit=3,
    )

    dsl = RestrictedDslBuilder(registry).build(
        plan,
        source_question="查询名称为 Service_003 的服务节点，最多返回 3 条记录。",
        query_id="q-service-limit",
    )

    assert dsl["operations"] == [{"op": "limit", "value": 3}]
    ast = parse_restricted_query_dsl(dsl, registry)
    assert ast.limit == 3


def test_vertex_lookup_plan_builds_vertex_lookup_dsl(registry: GraphSemanticRegistry) -> None:
    literal = LiteralBinding(
        raw_literal="ne-0001",
        resolved=True,
        value="ne-0001",
        normalized_value="ne-0001",
        match_type="value_index_exact",
        confidence=1.0,
        owner="NetworkElement",
        property="id",
    )
    plan = BindingPlan(
        query_shape="vertex_lookup",
        vertex_bindings=[
            VertexBinding(name="NetworkElement", candidate=_candidate("vertex", "NetworkElement")),
        ],
        literal_bindings=[literal],
        filters=[
            FilterBinding(
                owner="NetworkElement",
                property="id",
                operator="eq",
                raw_literal="ne-0001",
                value="ne-0001",
                literal=literal,
            )
        ],
        projection=[
            {
                "alias": "name",
                "target": "target",
                "property": {"owner": "NetworkElement", "name": "name"},
            }
        ],
    )

    dsl = RestrictedDslBuilder(registry).build(
        plan,
        source_question="查询设备 ne-0001 的名称",
        query_id="q-vertex",
    )

    assert dsl == {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-vertex",
        "query_shape": "vertex_lookup",
        "source_question": "查询设备 ne-0001 的名称",
        "bindings": {"target": {"vertex_name": "NetworkElement"}},
        "operations": [],
        "filters": [
            {
                "target": "target",
                "property": {"owner": "NetworkElement", "name": "id"},
                "operator": "eq",
                "value": {
                    "raw": "ne-0001",
                    "normalized": "ne-0001",
                    "resolver_match_type": "value_index_exact",
                },
            }
        ],
        "projection": {
            "items": [
                {
                    "alias": "name",
                    "target": "target",
                    "property": {"owner": "NetworkElement", "name": "name"},
                }
            ]
        },
    }
    ast = parse_restricted_query_dsl(dsl, registry)
    assert ast.query_shape.value == "vertex_lookup"


def _candidate(semantic_type: str, semantic_id: str) -> CandidateBinding:
    return CandidateBinding(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
        semantic_name=semantic_id,
        score=1.0,
        match_type="exact",
    )


def _all_keys(value: Any) -> set[str]:
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
