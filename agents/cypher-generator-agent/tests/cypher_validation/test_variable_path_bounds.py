from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.cypher_validation import CypherSelfValidator
from services.cypher_generator_agent.app.semantic_model import load_graph_semantic_model


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "network_topology_graph_model.yaml"


@pytest.fixture
def validator() -> CypherSelfValidator:
    return CypherSelfValidator(load_graph_semantic_model(FIXTURE_PATH).registry)


def test_bounded_variable_path_passes_static_self_validation(
    validator: CypherSelfValidator,
) -> None:
    result = validator.validate_generated_query(
        "MATCH path = (t:Tunnel)-[:PATH_THROUGH*1..8]->(ne:NetworkElement) "
        "WHERE ne.id = 'ne-0001' RETURN t.id AS tunnel_id"
    )

    assert result.valid is True
    assert {check.name: check.status for check in result.checks}["dialect"] == "passed"


def test_unbounded_variable_path_is_rejected_by_static_self_validation(
    validator: CypherSelfValidator,
) -> None:
    result = validator.validate_generated_query(
        "MATCH path = (t:Tunnel)-[:PATH_THROUGH*1..]->(ne:NetworkElement) RETURN t.id AS tunnel_id"
    )

    assert result.valid is False
    assert result.errors[0].code == "target_dialect_static_error"
    assert result.errors[0].check == "dialect"


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH path = (t:Tunnel)-[*1..]->(ne:NetworkElement) RETURN t.id AS tunnel_id",
        "MATCH path = (t:Tunnel)-[:PATH_THROUGH*]->(ne:NetworkElement) RETURN t.id AS tunnel_id",
    ],
)
def test_other_unbounded_variable_path_forms_are_rejected_by_static_self_validation(
    validator: CypherSelfValidator,
    cypher: str,
) -> None:
    result = validator.validate_generated_query(cypher)

    assert result.valid is False
    assert result.errors[0].code == "target_dialect_static_error"
    assert result.errors[0].check == "dialect"


def test_variable_path_upper_bound_above_eight_is_rejected_by_static_self_validation(
    validator: CypherSelfValidator,
) -> None:
    result = validator.validate_generated_query(
        "MATCH path = (t:Tunnel)-[:PATH_THROUGH*1..9]->(ne:NetworkElement) RETURN t.id AS tunnel_id"
    )

    assert result.valid is False
    assert result.errors[0].code == "target_dialect_static_error"
    assert "max_hops" in result.errors[0].message


def test_exact_length_variable_path_above_eight_is_rejected_by_static_self_validation(
    validator: CypherSelfValidator,
) -> None:
    result = validator.validate_generated_query(
        "MATCH path = (t:Tunnel)-[:PATH_THROUGH*9]->(ne:NetworkElement) RETURN t.id AS tunnel_id"
    )

    assert result.valid is False
    assert result.errors[0].code == "target_dialect_static_error"
    assert "max_hops" in result.errors[0].message
