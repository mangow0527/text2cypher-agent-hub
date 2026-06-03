from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.cypher_validation import CypherSelfValidator
from services.cypher_generator_agent.app.semantic_model import load_graph_semantic_model


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "network_topology_graph_model.yaml"


@pytest.fixture
def validator() -> CypherSelfValidator:
    return CypherSelfValidator(load_graph_semantic_model(FIXTURE_PATH).registry)


def test_return_aliases_match_expected_projection_shape(
    validator: CypherSelfValidator,
) -> None:
    result = validator.validate_generated_query(
        (
            "MATCH (t:Tunnel)-[p:PATH_THROUGH]->(ne:NetworkElement)\n"
            "RETURN ne AS device, p.hop_order AS hop\n"
            "ORDER BY p.hop_order ASC"
        ),
        expected_return_aliases=["device", "hop"],
    )

    assert result.valid is True
    assert {check.name: check.status for check in result.checks}["shape"] == "passed"


def test_return_alias_mismatch_fails_compiler_shape_check(
    validator: CypherSelfValidator,
) -> None:
    result = validator.validate_generated_query(
        "MATCH (ne:NetworkElement) RETURN ne AS device",
        expected_return_aliases=["device_id"],
    )

    assert result.valid is False
    assert result.errors[0].code == "compiler_shape_mismatch"
    assert result.errors[0].check == "shape"
    assert "expected ['device_id']" in result.errors[0].message
    assert "actual ['device']" in result.errors[0].message


def test_shape_check_is_skipped_without_expected_return_aliases(
    validator: CypherSelfValidator,
) -> None:
    result = validator.validate_generated_query("MATCH (ne:NetworkElement) RETURN ne AS device")

    assert result.valid is True
    assert {check.name: check.status for check in result.checks}["shape"] == "skipped"
