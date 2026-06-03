from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.cypher_validation import CypherSelfValidator
from services.cypher_generator_agent.app.semantic_model import load_graph_semantic_model


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "network_topology_graph_model.yaml"


@pytest.fixture
def validator() -> CypherSelfValidator:
    return CypherSelfValidator(load_graph_semantic_model(FIXTURE_PATH).registry)


def test_generated_query_entrypoint_sets_generated_query_mode(
    validator: CypherSelfValidator,
) -> None:
    result = validator.validate_generated_query("MATCH (ne:NetworkElement) RETURN ne.id AS id")

    assert result.valid is True
    assert result.mode == "generated_query"


def test_generated_query_rejects_parameter_placeholders(
    validator: CypherSelfValidator,
) -> None:
    result = validator.validate_generated_query("MATCH (ne:NetworkElement) WHERE ne.id = $id RETURN ne.id AS id")

    assert result.valid is False
    assert result.errors[0].code == "cypher_parameter_placeholder_not_allowed"
    assert result.errors[0].check == "parameters_inline"


def test_model_artifact_entrypoint_sets_model_artifact_mode(
    validator: CypherSelfValidator,
) -> None:
    result = validator.validate_model_artifact(
        "MATCH (t:Tunnel {id: $tunnel_id})-[:PATH_THROUGH]->(ne:NetworkElement) RETURN ne",
        source_kind="path_pattern",
        source_name="tunnel_full_path",
    )

    assert result.valid is True
    assert result.mode == "model_artifact"
    assert {check.name: check.status for check in result.checks}["model_artifact"] == "passed"
