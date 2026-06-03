from __future__ import annotations

from services.cypher_generator_agent.app.compiler import CypherCompiler, compile_restricted_query_ast
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry

from .conftest import parse_dsl, single_hop_dsl


def test_gold_service_uses_tunnel_compiles_executable_cypher_with_template_trace(
    registry: GraphSemanticRegistry,
) -> None:
    ast = parse_dsl(single_hop_dsl(), registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.schema_version == "cypher_compilation_result_v1"
    assert result.cypher == (
        "MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun:Tunnel)\n"
        "WHERE svc.quality_of_service = 'GOLD'\n"
        "RETURN tun.id AS tunnel_id"
    )
    assert result.cypher_executable == result.cypher
    assert result.cypher_template == (
        "MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun:Tunnel)\n"
        "WHERE svc.quality_of_service = $quality_of_service\n"
        "RETURN tun.id AS tunnel_id"
    )
    assert result.parameters == {"quality_of_service": "GOLD"}
    assert "$quality_of_service" not in result.cypher
    assert result.validation_result.valid is True
    assert {check.name: check.status for check in result.validation_result.checks}["shape"] == "passed"


def test_single_hop_duplicate_filter_property_names_get_unique_parameters(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = single_hop_dsl()
    dsl["filters"] = [
        {
            "target": "start",
            "property": {"owner": "Service", "name": "id"},
            "operator": "eq",
            "value": {
                "raw": "svc-gold-001",
                "normalized": "svc-gold-001",
                "resolver_match_type": "value_index_exact",
            },
        },
        {
            "target": "end",
            "property": {"owner": "Tunnel", "name": "id"},
            "operator": "eq",
            "value": {
                "raw": "tun-mpls-001",
                "normalized": "tun-mpls-001",
                "resolver_match_type": "value_index_exact",
            },
        },
    ]
    dsl["projection"]["items"][0]["alias"] = "id"
    ast = parse_dsl(dsl, registry)

    result = compile_restricted_query_ast(ast, registry)

    assert "svc.id = 'svc-gold-001'" in result.cypher
    assert "tun.id = 'tun-mpls-001'" in result.cypher
    assert "svc.id = $id" in result.cypher_template
    assert "tun.id = $id_2" in result.cypher_template
    assert result.parameters == {"id": "svc-gold-001", "id_2": "tun-mpls-001"}
    assert "$id" not in result.cypher
    assert "$id_2" not in result.cypher


def test_projection_alias_controls_return_alias(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = single_hop_dsl()
    dsl["projection"]["items"][0]["alias"] = "service_tunnel"
    ast = parse_dsl(dsl, registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher.endswith("RETURN tun.id AS service_tunnel")


def test_duplicate_projection_aliases_are_canonicalized_per_return_clause(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = single_hop_dsl()
    dsl["projection"]["items"] = [
        {
            "target": "start",
            "property": {"owner": "Service", "name": "id"},
            "alias": "id",
        },
        {
            "target": "end",
            "property": {"owner": "Tunnel", "name": "id"},
            "alias": "id",
        },
        {
            "target": "start",
            "property": {"owner": "Service", "name": "quality_of_service"},
            "alias": "value",
        },
        {
            "target": "end",
            "property": {"owner": "Tunnel", "name": "bandwidth"},
            "alias": "value",
        },
    ]
    ast = parse_dsl(dsl, registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher.endswith(
        "RETURN svc.id AS service_id, tun.id AS tunnel_id, "
        "svc.quality_of_service AS service_value, tun.bandwidth AS tunnel_value"
    )
    assert result.validation_result.valid is True


def test_projection_alias_avoids_cypher_reserved_keyword(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = single_hop_dsl()
    dsl["projection"]["items"] = [
        {
            "alias": "end",
            "target": "end",
            "vertex_full": True,
        }
    ]
    ast = parse_dsl(dsl, registry)

    result = compile_restricted_query_ast(ast, registry)
    draft = CypherCompiler(registry).compile_draft(ast)

    assert result.cypher.endswith("RETURN tun AS tunnel")
    assert " AS end" not in result.cypher
    assert draft.expected_return_aliases == ["tunnel"]
    assert result.validation_result.valid is True


def test_property_projection_reserved_alias_gets_generic_suffix(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = single_hop_dsl()
    dsl["projection"]["items"][0]["alias"] = "return"
    ast = parse_dsl(dsl, registry)

    result = compile_restricted_query_ast(ast, registry)
    draft = CypherCompiler(registry).compile_draft(ast)

    assert result.cypher.endswith("RETURN tun.id AS return_value")
    assert " AS return\n" not in result.cypher
    assert draft.expected_return_aliases == ["return_value"]
    assert result.validation_result.valid is True
