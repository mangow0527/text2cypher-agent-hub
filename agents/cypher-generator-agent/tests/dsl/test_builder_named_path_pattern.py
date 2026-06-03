from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.binding.models import (
    BindingPlan,
    CandidateBinding,
    FilterBinding,
    LiteralBinding,
    PathPatternBinding,
    VertexBinding,
)
from services.cypher_generator_agent.app.dsl.builder import RestrictedDslBuilder
from services.cypher_generator_agent.app.dsl.parser import (
    RestrictedDslValidationError,
    parse_restricted_query_dsl,
)
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry, load_graph_semantic_model


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "network_topology_graph_model.yaml"


@pytest.fixture(scope="module")
def registry() -> GraphSemanticRegistry:
    return load_graph_semantic_model(FIXTURE_PATH).registry


def test_tunnel_full_path_plan_builds_named_path_pattern_dsl(
    registry: GraphSemanticRegistry,
) -> None:
    literal = LiteralBinding(
        raw_literal="tun-mpls-001",
        resolved=True,
        value="tun-mpls-001",
        normalized_value="tun-mpls-001",
        match_type="value_index_exact",
        confidence=1.0,
        owner="Tunnel",
        property="id",
    )
    plan = BindingPlan(
        query_shape="named_path_pattern",
        vertex_bindings=[VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel"))],
        path_pattern_bindings=[
            PathPatternBinding(
                name="tunnel_full_path",
                candidate=_candidate("path_pattern", "tunnel_full_path"),
            )
        ],
        literal_bindings=[literal],
        filters=[
            FilterBinding(
                owner="Tunnel",
                property="id",
                operator="eq",
                raw_literal="tun-mpls-001",
                value="tun-mpls-001",
                literal=literal,
            )
        ],
        projection=[
            {"alias": "device", "source": "path.device"},
            {"alias": "hop", "source": "path.hop"},
        ],
        assumptions=[{"type": "path_pattern_selected", "name": "tunnel_full_path"}],
    )

    dsl = RestrictedDslBuilder(registry).build(
        plan,
        source_question="隧道 tun-mpls-001 经过哪些设备",
        query_id="q-path",
    )

    assert dsl == {
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
        "assumptions": [{"type": "path_pattern_selected", "name": "tunnel_full_path"}],
    }
    assert "raw_cypher" not in str(dsl)
    ast = parse_restricted_query_dsl(dsl, registry)
    assert ast.operations[0].path_pattern_name == "tunnel_full_path"
    assert ast.operations[0].parameters["tunnel_id"].normalized == "tun-mpls-001"
    assert ast.projection.items[0].source.raw == "path.device"


def test_named_path_pattern_parser_owns_generated_dsl_validation(
    registry: GraphSemanticRegistry,
) -> None:
    plan = BindingPlan(
        query_shape="named_path_pattern",
        vertex_bindings=[VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel"))],
        path_pattern_bindings=[
            PathPatternBinding(
                name="tunnel_full_path",
                candidate=_candidate("path_pattern", "tunnel_full_path"),
            )
        ],
        projection=[{"alias": "device", "source": "path.device"}],
    )

    dsl = RestrictedDslBuilder(registry).build(
        plan,
        source_question="隧道经过哪些设备",
        query_id="q-invalid-path",
    )

    with pytest.raises(RestrictedDslValidationError) as error:
        parse_restricted_query_dsl(dsl, registry)

    assert {issue.code for issue in error.value.errors} == {"missing_path_pattern_parameter"}


def test_named_path_pattern_rejects_filters_that_cannot_bind_to_template_parameters(
    registry: GraphSemanticRegistry,
) -> None:
    tunnel_literal = LiteralBinding(
        raw_literal="tun-mpls-001",
        resolved=True,
        value="tun-mpls-001",
        normalized_value="tun-mpls-001",
        match_type="value_index_exact",
        confidence=1.0,
        owner="Tunnel",
        property="id",
    )
    bandwidth_literal = LiteralBinding(
        raw_literal="100",
        resolved=True,
        value=100,
        normalized_value=100,
        match_type="numeric_parse",
        confidence=1.0,
        owner="Tunnel",
        property="bandwidth",
    )
    plan = BindingPlan(
        query_shape="named_path_pattern",
        vertex_bindings=[VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel"))],
        path_pattern_bindings=[
            PathPatternBinding(
                name="tunnel_full_path",
                candidate=_candidate("path_pattern", "tunnel_full_path"),
            )
        ],
        literal_bindings=[tunnel_literal, bandwidth_literal],
        filters=[
            FilterBinding(
                owner="Tunnel",
                property="id",
                operator="eq",
                raw_literal="tun-mpls-001",
                value="tun-mpls-001",
                literal=tunnel_literal,
            ),
            FilterBinding(
                owner="Tunnel",
                property="bandwidth",
                operator="gt",
                raw_literal="100",
                value=100,
                literal=bandwidth_literal,
            ),
        ],
        projection=[
            {"alias": "device", "source": "path.device"},
            {"alias": "hop", "source": "path.hop"},
        ],
    )

    with pytest.raises(ValueError, match="cannot be represented"):
        RestrictedDslBuilder(registry).build(
            plan,
            source_question="带宽大于 100 的隧道经过哪些设备",
            query_id="q-path-extra-filter",
        )


def test_named_path_pattern_rejects_non_eq_filter_for_template_parameter(
    registry: GraphSemanticRegistry,
) -> None:
    literal = LiteralBinding(
        raw_literal="tun-mpls-001",
        resolved=True,
        value="tun-mpls-001",
        normalized_value="tun-mpls-001",
        match_type="value_index_exact",
        confidence=1.0,
        owner="Tunnel",
        property="id",
    )
    plan = BindingPlan(
        query_shape="named_path_pattern",
        vertex_bindings=[VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel"))],
        path_pattern_bindings=[
            PathPatternBinding(
                name="tunnel_full_path",
                candidate=_candidate("path_pattern", "tunnel_full_path"),
            )
        ],
        literal_bindings=[literal],
        filters=[
            FilterBinding(
                owner="Tunnel",
                property="id",
                operator="neq",
                raw_literal="tun-mpls-001",
                value="tun-mpls-001",
                literal=literal,
            )
        ],
        projection=[
            {"alias": "device", "source": "path.device"},
            {"alias": "hop", "source": "path.hop"},
        ],
    )

    with pytest.raises(ValueError, match="cannot be represented"):
        RestrictedDslBuilder(registry).build(
            plan,
            source_question="不是 tun-mpls-001 的隧道经过哪些设备",
            query_id="q-path-neq-filter",
        )


def test_named_path_pattern_rejects_sort_or_limit_until_compiler_supports_them(
    registry: GraphSemanticRegistry,
) -> None:
    literal = LiteralBinding(
        raw_literal="tun-mpls-001",
        resolved=True,
        value="tun-mpls-001",
        normalized_value="tun-mpls-001",
        match_type="value_index_exact",
        confidence=1.0,
        owner="Tunnel",
        property="id",
    )
    plan = BindingPlan(
        query_shape="named_path_pattern",
        vertex_bindings=[VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel"))],
        path_pattern_bindings=[
            PathPatternBinding(
                name="tunnel_full_path",
                candidate=_candidate("path_pattern", "tunnel_full_path"),
            )
        ],
        literal_bindings=[literal],
        filters=[
            FilterBinding(
                owner="Tunnel",
                property="id",
                operator="eq",
                raw_literal="tun-mpls-001",
                value="tun-mpls-001",
                literal=literal,
            )
        ],
        projection=[
            {"alias": "device", "source": "path.device"},
            {"alias": "hop", "source": "path.hop"},
        ],
        sort=[{"source": "path.hop", "direction": "asc"}],
    )

    with pytest.raises(ValueError, match="sort/limit"):
        RestrictedDslBuilder(registry).build(
            plan,
            source_question="隧道 tun-mpls-001 经过哪些设备",
            query_id="q-path-sort",
        )


def _candidate(semantic_type: str, semantic_id: str) -> CandidateBinding:
    return CandidateBinding(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
        semantic_name=semantic_id,
        score=1.0,
        match_type="exact",
    )
