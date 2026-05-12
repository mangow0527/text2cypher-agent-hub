from __future__ import annotations

from services.cypher_generator_agent.app.semantic_cypher_preflight import run_semantic_cypher_preflight
from services.cypher_generator_agent.app.semantic_query import (
    SemanticEntityRef,
    SemanticFieldRef,
    SemanticFilterRef,
    SemanticMetricRef,
    SemanticOrderBy,
    SemanticQuerySpec,
    SemanticRelationshipRef,
)


def test_semantic_cypher_preflight_accepts_renderer_aligned_query() -> None:
    spec = _service_tunnel_spec()
    cypher = (
        "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\n"
        "WHERE s.quality_of_service = 'Gold'\n"
        "RETURN t.name AS tunnel_name"
    )

    result = run_semantic_cypher_preflight(cypher, semantic_query=spec)

    assert result.accepted is True


def test_semantic_cypher_preflight_rejects_unauthorized_schema_reference() -> None:
    spec = _service_tunnel_spec()
    cypher = (
        "MATCH (s:Service)-[:UNDECLARED_EDGE]->(t:Tunnel)\n"
        "WHERE s.quality_of_service = 'Gold'\n"
        "RETURN t.name AS tunnel_name"
    )

    result = run_semantic_cypher_preflight(cypher, semantic_query=spec)

    assert result.accepted is False
    assert result.reason == "unauthorized_schema_reference"


def test_semantic_cypher_preflight_rejects_anonymous_unauthorized_label() -> None:
    spec = _service_tunnel_spec()
    cypher = (
        "MATCH (:Secret)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\n"
        "WHERE t.name = 'T1'\n"
        "RETURN t.name AS tunnel_name"
    )

    result = run_semantic_cypher_preflight(cypher, semantic_query=spec)

    assert result.accepted is False
    assert result.reason == "unauthorized_schema_reference"


def test_semantic_cypher_preflight_rejects_node_map_property_outside_semantic_query() -> None:
    spec = _service_tunnel_spec()
    cypher = (
        "MATCH (s:Service {secret: 'x'})-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\n"
        "WHERE s.quality_of_service = 'Gold'\n"
        "RETURN t.name AS tunnel_name"
    )

    result = run_semantic_cypher_preflight(cypher, semantic_query=spec)

    assert result.accepted is False
    assert result.reason == "unauthorized_schema_reference"


def test_semantic_cypher_preflight_rejects_missing_filter_from_semantic_query() -> None:
    spec = _service_tunnel_spec()
    cypher = "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\nRETURN t.name AS tunnel_name"

    result = run_semantic_cypher_preflight(cypher, semantic_query=spec)

    assert result.accepted is False
    assert result.reason == "logical_plan_mismatch"


def test_semantic_cypher_preflight_rejects_missing_projection_from_semantic_query() -> None:
    spec = _service_tunnel_spec()
    cypher = (
        "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\n"
        "WHERE s.quality_of_service = 'Gold'\n"
        "RETURN s.name AS service_name"
    )

    result = run_semantic_cypher_preflight(cypher, semantic_query=spec)

    assert result.accepted is False
    assert result.reason == "logical_plan_mismatch"


def test_semantic_cypher_preflight_rejects_missing_order_and_limit_from_semantic_query() -> None:
    spec = _ranking_spec()
    cypher = "MATCH (t:Tunnel)\nRETURN t.name AS tunnel_name, t.latency AS tunnel_latency"

    result = run_semantic_cypher_preflight(cypher, semantic_query=spec)

    assert result.accepted is False
    assert result.reason == "logical_plan_mismatch"


def _service_tunnel_spec() -> SemanticQuerySpec:
    return SemanticQuerySpec(
        kind="record_selection",
        entities=(
            SemanticEntityRef(name="service", label="Service", alias="s"),
            SemanticEntityRef(name="tunnel", label="Tunnel", alias="t"),
        ),
        relationships=(
            SemanticRelationshipRef(
                name="service_uses_tunnel",
                from_entity="service",
                to_entity="tunnel",
                edge="SERVICE_USES_TUNNEL",
                direction="out",
            ),
        ),
        projections=(
            SemanticFieldRef(name="tunnel_name", entity="tunnel", alias="t", property="name", output_alias="tunnel_name"),
        ),
        filters=(
            SemanticFilterRef(entity="service", alias="s", property="quality_of_service", operator="=", value="Gold"),
        ),
    )


def _ranking_spec() -> SemanticQuerySpec:
    return SemanticQuerySpec(
        kind="ranking",
        entities=(SemanticEntityRef(name="tunnel", label="Tunnel", alias="t"),),
        projections=(
            SemanticFieldRef(name="tunnel_name", entity="tunnel", alias="t", property="name", output_alias="tunnel_name"),
            SemanticFieldRef(
                name="tunnel_latency",
                entity="tunnel",
                alias="t",
                property="latency",
                output_alias="tunnel_latency",
            ),
        ),
        metrics=(
            SemanticMetricRef(
                name="avg_tunnel_latency",
                entity="tunnel",
                alias="t",
                aggregation="avg",
                property="latency",
                expression="avg(t.latency)",
                output_alias="avg_tunnel_latency",
            ),
        ),
        order_by=(SemanticOrderBy(expression="t.latency", direction="DESC"),),
        limit=5,
    )
