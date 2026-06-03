from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.cypher_validation import (
    CypherSelfValidationRequest,
    CypherSelfValidator,
)
from services.cypher_generator_agent.app.semantic_model import load_graph_semantic_model


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "network_topology_graph_model.yaml"


@pytest.fixture
def validator() -> CypherSelfValidator:
    return CypherSelfValidator(load_graph_semantic_model(FIXTURE_PATH).registry)


def validate(validator: CypherSelfValidator, cypher: str):
    return validator.validate(
        CypherSelfValidationRequest(
            mode="generated_query",
            source_kind="compiled_query",
            cypher=cypher,
        )
    )


def test_readonly_match_return_query_passes(validator: CypherSelfValidator) -> None:
    result = validate(validator, "MATCH (ne:NetworkElement) RETURN ne.id AS id")

    assert result.schema_version == "cypher_self_validation_result_v1"
    assert result.valid is True
    assert result.errors == []
    assert {check.name: check.status for check in result.checks}["syntax"] == "passed"
    assert {check.name: check.status for check in result.checks}["readonly"] == "passed"


def test_set_clause_returns_readonly_violation(validator: CypherSelfValidator) -> None:
    result = validate(validator, 'MATCH (ne:NetworkElement) SET ne.name = "x" RETURN ne')

    assert result.valid is False
    assert [error.code for error in result.errors] == ["cypher_readonly_violation"]
    assert result.errors[0].check == "readonly"


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (ne:NetworkElement) RETURN ne; MATCH (t:Tunnel) RETURN t",
        "MATCH (ne:NetworkElement) RETURN ne; CREATE (x:NetworkElement)",
    ],
)
def test_multistatement_or_semicolon_chaining_is_syntax_invalid(
    validator: CypherSelfValidator,
    cypher: str,
) -> None:
    result = validate(validator, cypher)

    assert result.valid is False
    assert [error.code for error in result.errors] == ["cypher_syntax_invalid"]
    assert result.errors[0].check == "syntax"


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (ne:NetworkElement) OPTIONAL MATCH (ne)-[:HAS_PORT]->(p:Port) RETURN ne",
        "OPTIONAL MATCH (ne:NetworkElement) RETURN ne",
        "MATCH (ne:NetworkElement) USING INDEX ne:NetworkElement(id) RETURN ne",
        "MATCH (ne:NetworkElement) RETURN ne UNION MATCH (t:Tunnel) RETURN t",
        "MATCH (ne:NetworkElement) WHERE EXISTS { MATCH (ne)-[:HAS_PORT]->(p:Port) } RETURN ne",
    ],
)
def test_unsupported_read_clause_fragments_are_target_dialect_errors(
    validator: CypherSelfValidator,
    cypher: str,
) -> None:
    result = validate(validator, cypher)

    assert result.valid is False
    assert result.errors[0].code == "target_dialect_static_error"
    assert result.errors[0].check == "dialect"


def test_unsupported_read_fragment_inside_string_literal_is_allowed(
    validator: CypherSelfValidator,
) -> None:
    result = validate(validator, "RETURN 'OPTIONAL MATCH and UNION are documentation text' AS text")

    assert result.valid is True
    assert result.errors == []


def test_multistatement_without_semicolon_is_syntax_invalid(
    validator: CypherSelfValidator,
) -> None:
    result = validate(validator, "MATCH (ne:NetworkElement) RETURN ne MATCH (t:Tunnel) RETURN t")

    assert result.valid is False
    assert result.errors[0].code == "cypher_syntax_invalid"
    assert result.errors[0].check == "syntax"


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (ne:NetworkElement) RETURN 'UNION' AS text MATCH (t:Tunnel) RETURN t",
        "MATCH (ne:NetworkElement) RETURN union_count AS text MATCH (t:Tunnel) RETURN t",
    ],
)
def test_multistatement_without_semicolon_ignores_union_text(
    validator: CypherSelfValidator,
    cypher: str,
) -> None:
    result = validate(validator, cypher)

    assert result.valid is False
    assert result.errors[0].code == "cypher_syntax_invalid"
    assert result.errors[0].check == "syntax"


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (ne:NetworkElement) DROP DATABASE graph RETURN ne",
        "DROP DATABASE graph",
    ],
)
def test_drop_database_returns_readonly_violation(
    validator: CypherSelfValidator,
    cypher: str,
) -> None:
    result = validate(validator, cypher)

    assert result.valid is False
    assert result.errors[0].code == "cypher_readonly_violation"
    assert result.errors[0].check == "readonly"


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (ne:NetworkElement) CREATE (x:NetworkElement) RETURN ne",
        "MATCH (ne:NetworkElement) MERGE (x:NetworkElement {id: ne.id}) RETURN ne",
        "MATCH (ne:NetworkElement) DELETE ne RETURN ne",
        "MATCH (ne:NetworkElement) DETACH DELETE ne RETURN ne",
        "MATCH (ne:NetworkElement) REMOVE ne.name RETURN ne",
        "MATCH (ne:NetworkElement) CALL db.labels() RETURN ne",
        "LOAD CSV FROM 'file:///x.csv' AS row RETURN row",
        "MATCH (ne:NetworkElement) FOREACH (x IN [1] | SET ne.name = 'x') RETURN ne",
        "CREATE INDEX device_id IF NOT EXISTS FOR (ne:NetworkElement) ON (ne.id)",
        "DROP CONSTRAINT device_id IF EXISTS",
    ],
)
def test_mutating_or_ddl_clauses_return_readonly_violation(
    validator: CypherSelfValidator,
    cypher: str,
) -> None:
    result = validate(validator, cypher)

    assert result.valid is False
    assert result.errors[0].code == "cypher_readonly_violation"
    assert result.errors[0].check == "readonly"
