from __future__ import annotations

from typing import Any

import pytest

from services.cypher_generator_agent.app.compiler import CypherCompilerError
from services.cypher_generator_agent.app.compiler import compile_restricted_query_ast
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry

from .conftest import parse_dsl


def test_vertex_lookup_compiles_inline_filter_and_keeps_template(
    registry: GraphSemanticRegistry,
) -> None:
    ast = parse_dsl(_vertex_lookup_dsl(), registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher == (
        "MATCH (ne:NetworkElement)\n"
        "WHERE ne.id = 'ne-0001'\n"
        "RETURN ne.name AS name"
    )
    assert result.cypher_executable == result.cypher
    assert result.cypher_template == (
        "MATCH (ne:NetworkElement)\n"
        "WHERE ne.id = $id\n"
        "RETURN ne.name AS name"
    )
    assert result.parameters == {"id": "ne-0001"}
    assert "$id" not in result.cypher
    assert result.validation_result.valid is True


def test_vertex_lookup_compiles_explicit_vertex_full_projection(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-service-full",
        "query_shape": "vertex_lookup",
        "source_question": "查询所有服务",
        "bindings": {"target": {"vertex_name": "Service"}},
        "operations": [],
        "projection": {
            "items": [
                {
                    "alias": "service",
                    "target": "target",
                    "vertex_full": True,
                }
            ]
        },
    }
    ast = parse_dsl(dsl, registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher == "MATCH (svc:Service)\nRETURN svc AS service"
    assert result.validation_result.valid is True


def test_vertex_lookup_compiles_limit_tail(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _vertex_lookup_dsl()
    dsl["operations"] = [{"op": "limit", "value": 3}]
    ast = parse_dsl(dsl, registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher.endswith("RETURN ne.name AS name\nLIMIT 3")
    assert result.validation_result.valid is True


def test_projection_alias_with_chinese_surface_text_uses_legal_canonical_alias(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _vertex_lookup_dsl()
    dsl["projection"]["items"][0] = {
        "alias": "网元位置",
        "target": "target",
        "property": {"owner": "NetworkElement", "name": "location"},
    }
    ast = parse_dsl(dsl, registry)

    result = compile_restricted_query_ast(ast, registry)

    assert result.cypher == (
        "MATCH (ne:NetworkElement)\n"
        "WHERE ne.id = 'ne-0001'\n"
        "RETURN ne.location AS location"
    )
    assert result.validation_result.valid is True


def test_vertex_lookup_rejects_unresolved_filter_literal(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _vertex_lookup_dsl()
    dsl["filters"][0]["value"] = {
        "raw": "疑似设备",
        "normalized": None,
        "resolver_match_type": "unresolved",
    }
    ast = parse_dsl(dsl, registry)

    with pytest.raises(CypherCompilerError, match="unresolved literal"):
        compile_restricted_query_ast(ast, registry)


def test_vertex_lookup_rejects_filter_literal_without_resolution_evidence(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = _vertex_lookup_dsl()
    dsl["filters"][0]["value"] = {
        "raw": "ne-0001",
        "normalized": None,
    }
    ast = parse_dsl(dsl, registry)

    with pytest.raises(CypherCompilerError, match="resolution evidence"):
        compile_restricted_query_ast(ast, registry)


def _vertex_lookup_dsl() -> dict[str, Any]:
    return {
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
