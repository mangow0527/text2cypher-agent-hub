from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.cypher_validation import CypherSelfValidator
from services.cypher_generator_agent.app.semantic_model import load_graph_semantic_model


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "network_topology_graph_model.yaml"


@pytest.fixture
def validator() -> CypherSelfValidator:
    return CypherSelfValidator(load_graph_semantic_model(FIXTURE_PATH).registry)


@pytest.mark.parametrize(
    ("function_name", "expression"),
    [
        ("count", "ne.id"),
        ("sum", "t.bandwidth"),
        ("avg", "t.bandwidth"),
        ("min", "t.bandwidth"),
        ("max", "t.bandwidth"),
    ],
)
def test_allowed_aggregate_functions_pass_target_dialect(
    validator: CypherSelfValidator,
    function_name: str,
    expression: str,
) -> None:
    label = "Tunnel" if expression.startswith("t.") else "NetworkElement"
    variable = expression.split(".", 1)[0]
    result = validator.validate_generated_query(
        f"MATCH ({variable}:{label}) RETURN {function_name}({expression}) AS value"
    )

    assert result.valid is True
    assert {check.name: check.status for check in result.checks}["dialect"] == "passed"


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (ne:NetworkElement) RETURN collect(ne.id) AS device_ids",
        "MATCH (ne:NetworkElement) RETURN toString(ne.id) AS device_id",
        "MATCH (ne:NetworkElement) RETURN toInteger(ne.id) AS numeric_id",
        "MATCH (t:Tunnel) RETURN toFloat(t.bandwidth) AS bandwidth",
        "MATCH (ne:NetworkElement) RETURN coalesce(ne.name, 'unknown') AS device_name",
    ],
)
def test_spec_allowlisted_scalar_functions_pass_target_dialect(
    validator: CypherSelfValidator,
    cypher: str,
) -> None:
    result = validator.validate_generated_query(cypher)

    assert result.valid is True
    assert {check.name: check.status for check in result.checks}["dialect"] == "passed"


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (t:Tunnel {id: 'tun-mpls-001'}) RETURN t.id AS tunnel_id",
        (
            "MATCH (t:Tunnel)-[p:PATH_THROUGH {hop_order: 1}]->(ne:NetworkElement) "
            "RETURN ne.id AS device_id, p.hop_order AS hop"
        ),
    ],
)
def test_graph_pattern_property_maps_pass_target_dialect(
    validator: CypherSelfValidator,
    cypher: str,
) -> None:
    result = validator.validate_generated_query(cypher)

    assert result.valid is True
    assert {check.name: check.status for check in result.checks}["dialect"] == "passed"


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH p = shortestPath((a:NetworkElement)-[:HAS_PORT*1..8]->(b:Port)) RETURN p",
        "MATCH (ne:NetworkElement) RETURN apoc.text.join([ne.id], ',') AS joined",
    ],
)
def test_non_allowlisted_functions_fail_target_dialect(
    validator: CypherSelfValidator,
    cypher: str,
) -> None:
    result = validator.validate_generated_query(cypher)

    assert result.valid is False
    assert result.errors[0].code == "target_dialect_static_error"
    assert result.errors[0].check == "dialect"
    assert "function" in result.errors[0].message


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n:$(label)) RETURN n",
        "MATCH (n:$label) RETURN n",
        "MATCH (n)-[:$edge_type]->(m) RETURN m",
        "MATCH (n:NetworkElement) RETURN n[$property_name] AS value",
    ],
)
def test_dynamic_schema_references_fail_target_dialect(
    validator: CypherSelfValidator,
    cypher: str,
) -> None:
    result = validator.validate_generated_query(cypher)

    assert result.valid is False
    assert result.errors[0].code == "target_dialect_static_error"
    assert result.errors[0].check == "dialect"
    assert "dynamic" in result.errors[0].message


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (ne:NetworkElement) RETURN ne { .id, .name } AS device",
        "MATCH (ne:NetworkElement) RETURN ne { .* } AS device",
        "MATCH (ne:NetworkElement) RETURN ne { id } AS device",
        "MATCH (ne:NetworkElement) RETURN ne { id: $id } AS device",
        "MATCH (ne:NetworkElement) RETURN ne { answer: 42 } AS device",
        "MATCH (ne:NetworkElement) RETURN (ne { .id }) AS device",
        "MATCH (ne:NetworkElement) RETURN {device: ne { .id }} AS row",
        "MATCH (ne:NetworkElement) RETURN ne { nested: { id: 1 } } AS device",
        "MATCH (ne:NetworkElement) RETURN [(ne)-[:HAS_PORT]->(p:Port) | p.id] AS port_ids",
        "MATCH (ne:NetworkElement) RETURN [p = (ne)-[:HAS_PORT]->(:Port) | p] AS paths",
        "MATCH (ne:NetworkElement) RETURN [(ne {name: '(core)'})-->(p) | p] AS paths",
        "MATCH (ne:NetworkElement) RETURN [({name: coalesce(ne.name, '')})-->(p) | p] AS paths",
        "MATCH (ne:NetworkElement) RETURN [(ne)-[:HAS_PORT]-(p:Port) | 1] AS flags",
        "MATCH (ne:NetworkElement) RETURN [(ne)-[r:HAS_PORT]-(p:Port) | ne.id] AS ids",
        "MATCH (ne:NetworkElement) RETURN ne.id AS id ORDER BY ne { .id }",
        "MATCH (ne:NetworkElement) WITH ne ORDER BY ne { .id } RETURN ne.id AS id",
        "MATCH (ne:NetworkElement) WHERE ne { .id } IS NOT NULL RETURN ne.id AS id",
        "MATCH (ne:NetworkElement) UNWIND [ne { .id }] AS m RETURN ne.id AS id",
    ],
)
def test_pattern_and_map_projection_fragments_fail_target_dialect(
    validator: CypherSelfValidator,
    cypher: str,
) -> None:
    result = validator.validate_generated_query(cypher)

    assert result.valid is False
    assert result.errors[0].code == "target_dialect_static_error"
    assert result.errors[0].check == "dialect"


def test_pattern_like_text_inside_string_literal_is_allowed(
    validator: CypherSelfValidator,
) -> None:
    result = validator.validate_generated_query(
        "MATCH (ne:NetworkElement) RETURN 'ne { .id } and [(ne)-->(p) | p]' AS text"
    )

    assert result.valid is True
    assert result.errors == []


def test_pattern_like_text_inside_list_string_literal_is_allowed(
    validator: CypherSelfValidator,
) -> None:
    result = validator.validate_generated_query(
        "MATCH (ne:NetworkElement) RETURN ['(ne)-->(p) | p'] AS examples"
    )

    assert result.valid is True
    assert result.errors == []


def test_function_like_text_inside_string_literal_is_allowed(
    validator: CypherSelfValidator,
) -> None:
    result = validator.validate_generated_query(
        "MATCH (ne:NetworkElement) RETURN 'shortestPath((a)-->(b)) and apoc.text.join()' AS text"
    )

    assert result.valid is True
    assert result.errors == []
