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


@pytest.mark.parametrize(
    ("cypher", "message_fragment"),
    [
        ("MATCH (x:UnknownLabel) RETURN x", "UnknownLabel"),
        ("MATCH (ne:NetworkElement) RETURN ne.unknown_property AS x", "unknown_property"),
        ("MATCH (t:Tunnel)-[:UNKNOWN_EDGE]->(ne:NetworkElement) RETURN ne", "UNKNOWN_EDGE"),
        ("MATCH (ne:NetworkElement)-[:PATH_THROUGH]->(t:Tunnel) RETURN ne", "PATH_THROUGH"),
            ("MATCH (ne:NetworkElement {unknown_property: 'ne-0001'}) RETURN ne", "unknown_property"),
    ],
)
def test_unknown_schema_references_are_invalid(
    validator: CypherSelfValidator,
    cypher: str,
    message_fragment: str,
) -> None:
    result = validate(validator, cypher)

    assert result.valid is False
    assert [error.code for error in result.errors] == ["cypher_schema_reference_invalid"]
    assert result.errors[0].check == "schema_reference"
    assert message_fragment in result.errors[0].message


def test_edge_variable_property_passes_when_property_belongs_to_edge(
    validator: CypherSelfValidator,
) -> None:
    result = validate(
        validator,
        "MATCH (t:Tunnel)-[p:PATH_THROUGH]->(ne:NetworkElement) RETURN p.hop_order AS hop",
    )

    assert result.valid is True
    assert result.errors == []


def test_edge_variable_property_is_rejected_when_property_does_not_belong_to_edge(
    validator: CypherSelfValidator,
) -> None:
    result = validate(
        validator,
        "MATCH (t:Tunnel)-[p:PATH_THROUGH]->(ne:NetworkElement) RETURN p.unknown_property AS hop",
    )

    assert result.valid is False
    assert [error.code for error in result.errors] == ["cypher_schema_reference_invalid"]
    assert "unknown_property" in result.errors[0].message


@pytest.mark.parametrize(
    ("cypher", "message_fragment"),
    [
        ("MATCH (ne:NetworkElement) RETURN avg(ne.name) AS avg_name", "avg"),
        ("MATCH (ne:NetworkElement) RETURN sum(ne.location) AS total_location", "sum"),
        ("MATCH (t:Tunnel) RETURN avg(t.id) AS avg_id", "string"),
        ("MATCH (ne:NetworkElement) RETURN avg(ne.name + 1) AS avg_name", "avg"),
        ("MATCH (ne:NetworkElement) RETURN sum(DISTINCT ne.location + 1) AS total_location", "sum"),
        ("MATCH (ne:NetworkElement) RETURN avg((ne.name + 1)) AS avg_name", "avg"),
        (
            "MATCH (ne:NetworkElement) WITH ne.name AS device_name RETURN avg(device_name) AS avg_name",
            "device_name",
        ),
    ],
)
def test_sum_and_avg_reject_non_numeric_properties(
    validator: CypherSelfValidator,
    cypher: str,
    message_fragment: str,
) -> None:
    result = validate(validator, cypher)

    assert result.valid is False
    assert [error.code for error in result.errors] == ["cypher_schema_reference_invalid"]
    assert result.errors[0].check == "schema_reference"
    assert message_fragment in result.errors[0].message


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (t:Tunnel) RETURN avg(t.bandwidth) AS avg_bandwidth",
        "MATCH (t:Tunnel)-[p:PATH_THROUGH]->(ne:NetworkElement) RETURN sum(p.hop_order) AS hops",
        "MATCH (ne:NetworkElement) RETURN count(ne.name) AS named_devices",
        "MATCH (ne:NetworkElement) RETURN min(ne.name) AS first_name",
        "MATCH (ne:NetworkElement) RETURN max(ne.name) AS last_name",
        "MATCH (t:Tunnel) WITH t.bandwidth AS bandwidth RETURN avg(bandwidth) AS avg_bandwidth",
    ],
)
def test_numeric_aggregates_accept_compatible_properties(
    validator: CypherSelfValidator,
    cypher: str,
) -> None:
    result = validate(validator, cypher)

    assert result.valid is True
    assert result.errors == []


@pytest.mark.parametrize(
    ("cypher", "message_fragment"),
    [
        ("MATCH (ne:NetworkElement) WHERE ne.name > 'core' RETURN ne.id AS id", "range"),
        ("MATCH (ne:NetworkElement) WHERE ne.elem_type <= 'router' RETURN ne.id AS id", "range"),
        ("MATCH (ne:NetworkElement) WHERE 'core' < ne.name RETURN ne.id AS id", "range"),
        ("MATCH (t:Tunnel) WHERE t.bandwidth CONTAINS 'needle' RETURN t.id AS id", "CONTAINS"),
        ("MATCH (t:Tunnel) WHERE 'needle' CONTAINS t.bandwidth RETURN t.id AS id", "CONTAINS"),
    ],
)
def test_operators_reject_incompatible_property_types(
    validator: CypherSelfValidator,
    cypher: str,
    message_fragment: str,
) -> None:
    result = validate(validator, cypher)

    assert result.valid is False
    assert [error.code for error in result.errors] == ["cypher_schema_reference_invalid"]
    assert result.errors[0].check == "schema_reference"
    assert message_fragment in result.errors[0].message


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (t:Tunnel) WHERE t.bandwidth >= 100 RETURN t.id AS id",
        "MATCH (t:Tunnel)-[:PATH_THROUGH]->(ne:NetworkElement) WHERE t.bandwidth < 1000 RETURN t.id AS id",
        "MATCH (ne:NetworkElement) WHERE ne.name CONTAINS 'core' RETURN ne.id AS id",
        "MATCH (ne:NetworkElement) WHERE ne.elem_type IN ['router', 'switch'] RETURN ne.id AS id",
        "MATCH (ne:NetworkElement) RETURN 'ne.name > $name' AS text",
    ],
)
def test_operators_accept_compatible_property_types(
    validator: CypherSelfValidator,
    cypher: str,
) -> None:
    result = validate(validator, cypher)

    assert result.valid is True
    assert result.errors == []


def test_schema_reference_ignores_property_like_text_inside_string_literals(
    validator: CypherSelfValidator,
) -> None:
    result = validate(
        validator,
        "MATCH (ne:NetworkElement) RETURN 'ne.unknown_property and avg(ne.name)' AS text",
    )

    assert result.valid is True
    assert result.errors == []


def test_reverse_edge_direction_uses_reversed_endpoint_validation(
    validator: CypherSelfValidator,
) -> None:
    result = validate(
        validator,
        "MATCH (ne:NetworkElement)<-[p:PATH_THROUGH]-(t:Tunnel) RETURN p.hop_order AS hop",
    )

    assert result.valid is True
    assert result.errors == []


@pytest.mark.parametrize(
    ("cypher", "message_fragment"),
    [
        ("MATCH (ne:NetworkElement:UnknownLabel) RETURN ne", "UnknownLabel"),
        ("MATCH (t:Tunnel)-[:PATH_THROUGH|UNKNOWN_EDGE]->(ne:NetworkElement) RETURN ne", "multiple edge types"),
        ("MATCH (ne:`NetworkElement`) RETURN ne", "backtick"),
    ],
)
def test_unsupported_compound_or_backtick_schema_references_are_invalid(
    validator: CypherSelfValidator,
    cypher: str,
    message_fragment: str,
) -> None:
    result = validate(validator, cypher)

    assert result.valid is False
    assert result.errors[0].code == "cypher_schema_reference_invalid"
    assert result.errors[0].check == "schema_reference"
    assert message_fragment in result.errors[0].message


def test_chained_pattern_validates_second_edge_endpoint(
    validator: CypherSelfValidator,
) -> None:
    result = validate(
        validator,
        (
            "MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)"
            "-[:HAS_PORT]->(p:Port) RETURN p"
        ),
    )

    assert result.valid is False
    assert result.errors[0].code == "cypher_schema_reference_invalid"
    assert "HAS_PORT" in result.errors[0].message


def test_endpoint_validation_uses_prior_variable_binding(
    validator: CypherSelfValidator,
) -> None:
    result = validate(
        validator,
        "MATCH (ne:NetworkElement) MATCH (ne)-[:HAS_PORT]->(p:Port) RETURN p.id AS port_id",
    )

    assert result.valid is True
    assert result.errors == []


def test_endpoint_validation_rejects_prior_variable_binding_mismatch(
    validator: CypherSelfValidator,
) -> None:
    result = validate(
        validator,
        "MATCH (t:Tunnel) MATCH (t)-[:HAS_PORT]->(p:Port) RETURN p.id AS port_id",
    )

    assert result.valid is False
    assert result.errors[0].code == "cypher_schema_reference_invalid"
    assert "HAS_PORT" in result.errors[0].message


def test_undirected_edge_endpoint_is_validated_against_either_direction(
    validator: CypherSelfValidator,
) -> None:
    result = validate(
        validator,
        "MATCH (t:Tunnel)-[:HAS_PORT]-(p:Port) RETURN p.id AS port_id",
    )

    assert result.valid is False
    assert result.errors[0].code == "cypher_schema_reference_invalid"
    assert "HAS_PORT" in result.errors[0].message
