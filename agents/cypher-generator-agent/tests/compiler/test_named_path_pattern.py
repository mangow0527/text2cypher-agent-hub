from __future__ import annotations

from services.cypher_generator_agent.app.compiler import compile_restricted_query_ast
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry

from .conftest import named_path_pattern_dsl, parse_dsl


FIXTURE_TEMPLATE = (
    "MATCH (t:Tunnel {id: $tunnel_id})-[p:PATH_THROUGH]->(ne:NetworkElement)\n"
    "RETURN ne AS device, p.hop_order AS hop\n"
    "ORDER BY p.hop_order ASC"
)


def test_tunnel_full_path_uses_fixture_template_and_instantiates_parameters(
    registry: GraphSemanticRegistry,
) -> None:
    ast = parse_dsl(named_path_pattern_dsl(), registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher_template == FIXTURE_TEMPLATE
    assert result.cypher == (
        "MATCH (t:Tunnel {id: 'tun-mpls-001'})-[p:PATH_THROUGH]->(ne:NetworkElement)\n"
        "RETURN ne AS device, p.hop_order AS hop\n"
        "ORDER BY p.hop_order ASC"
    )
    assert result.cypher_executable == result.cypher
    assert result.parameters == {"tunnel_id": "tun-mpls-001"}
    assert "$tunnel_id" not in result.cypher
    assert result.validation_result.valid is True
