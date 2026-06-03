from __future__ import annotations

from typing import Any

import pytest

from services.cypher_generator_agent.app.compiler import CypherCompilerError, compile_restricted_query_ast
from services.cypher_generator_agent.app.dsl.parser import RestrictedDslValidationError
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry

from .conftest import parse_dsl


def test_variable_path_compiles_bounded_path_with_through_filter(
    registry: GraphSemanticRegistry,
) -> None:
    ast = parse_dsl(_variable_path_dsl(), registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher == (
        "MATCH path = (tun:Tunnel)-[:PATH_THROUGH*1..8]->(ne:NetworkElement)\n"
        "WHERE ne.id = 'ne-0001'\n"
        "RETURN tun.id AS tunnel_id"
    )
    assert result.cypher_template == (
        "MATCH path = (tun:Tunnel)-[:PATH_THROUGH*1..8]->(ne:NetworkElement)\n"
        "WHERE ne.id = $id\n"
        "RETURN tun.id AS tunnel_id"
    )
    assert result.parameters == {"id": "ne-0001"}
    assert "$id" not in result.cypher
    assert result.validation_result.valid is True


def test_variable_path_max_hops_above_eight_is_rejected(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _variable_path_dsl()
    dsl["operations"][0]["max_hops"] = 9

    with pytest.raises(RestrictedDslValidationError, match="less than or equal to 8"):
        parse_dsl(dsl, registry)


def test_variable_path_missing_max_hops_is_rejected(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _variable_path_dsl()
    del dsl["operations"][0]["max_hops"]

    with pytest.raises(RestrictedDslValidationError, match="max_hops"):
        parse_dsl(dsl, registry)


def test_variable_path_multiple_allowed_edges_are_rejected_by_compiler(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _variable_path_dsl()
    dsl["operations"][0]["allowed_edges"] = ["PATH_THROUGH", "SERVICE_USES_TUNNEL"]
    ast = parse_dsl(dsl, registry)

    with pytest.raises(CypherCompilerError, match="exactly one allowed edge"):
        compile_restricted_query_ast(ast, registry)


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
                            "target": "through",
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
