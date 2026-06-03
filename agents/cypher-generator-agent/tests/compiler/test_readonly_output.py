from __future__ import annotations

import pytest

from services.cypher_generator_agent.app.compiler import (
    CypherCompiler,
    CypherCompilerError,
    compile_restricted_query_ast,
)
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry

from .conftest import named_path_pattern_dsl, parse_dsl, single_hop_dsl


class _UnsupportedQueryShape:
    value = "unsupported"


def test_compiler_blocks_mutating_template_output(
    registry: GraphSemanticRegistry,
) -> None:
    ast = parse_dsl(named_path_pattern_dsl(), registry)
    compiler = CypherCompiler(
        registry,
        _path_pattern_template_overrides_for_tests={
            "tunnel_full_path": (
                "MATCH (t:Tunnel {id: $tunnel_id})-[p:PATH_THROUGH]->(ne:NetworkElement)\n"
                "SET ne.name = 'bad'\n"
                "RETURN ne AS device, p.hop_order AS hop"
            )
        },
    )

    with pytest.raises(CypherCompilerError, match="self-validation failed"):
        compiler.compile(ast)


def test_compiler_canonicalizes_invalid_projection_alias(
    registry: GraphSemanticRegistry,
) -> None:
    dsl = single_hop_dsl()
    dsl["projection"]["items"][0]["alias"] = "bad alias"
    ast = parse_dsl(dsl, registry)

    result = CypherCompiler(registry).compile(ast)

    assert result.cypher.endswith("RETURN tun.id AS id")
    assert result.validation_result.valid is True


def test_named_path_pattern_template_parameters_must_match_dsl_parameters(
    registry: GraphSemanticRegistry,
) -> None:
    ast = parse_dsl(named_path_pattern_dsl(), registry)
    compiler = CypherCompiler(
        registry,
        _path_pattern_template_overrides_for_tests={
            "tunnel_full_path": (
                "MATCH (t:Tunnel {id: $wrong_id})-[p:PATH_THROUGH]->(ne:NetworkElement)\n"
                "RETURN ne AS device, p.hop_order AS hop\n"
                "ORDER BY p.hop_order ASC"
            )
        },
    )

    with pytest.raises(CypherCompilerError, match="template parameters"):
        compiler.compile(ast)


def test_unsupported_query_shape_raises_compiler_error(
    registry: GraphSemanticRegistry,
) -> None:
    ast = parse_dsl(single_hop_dsl(), registry)
    object.__setattr__(ast, "query_shape", _UnsupportedQueryShape())

    with pytest.raises(CypherCompilerError, match="unsupported query_shape"):
        compile_restricted_query_ast(ast, registry)
