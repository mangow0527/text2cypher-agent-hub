from __future__ import annotations

import importlib.util
import json

from services.cypher_generator_agent.app.cypher_renderer import CypherRenderer
from services.cypher_generator_agent.app.semantic_query import (
    SemanticEntityRef,
    SemanticFieldRef,
    SemanticFilterRef,
    SemanticMetricRef,
    SemanticOrderBy,
    SemanticQuerySpec,
    SemanticRelationshipRef,
)


def test_record_selection_semantic_query_is_serializable_and_renders_cypher() -> None:
    spec = SemanticQuerySpec(
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
            SemanticFieldRef(name="tunnel_latency", entity="tunnel", alias="t", property="latency", output_alias="tunnel_latency"),
        ),
        filters=(
            SemanticFilterRef(entity="service", alias="s", property="quality_of_service", operator="=", value="Gold"),
        ),
    )

    serialized = spec.to_dict()
    assert serialized["kind"] == "record_selection"
    assert serialized["entities"][0]["name"] == "service"
    assert serialized["relationships"][0]["name"] == "service_uses_tunnel"
    assert serialized["projections"][0]["name"] == "tunnel_name"
    assert "match" not in serialized
    assert "return" not in serialized
    assert json.loads(spec.to_json()) == serialized
    assert CypherRenderer().render(spec) == (
        "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\n"
        "WHERE s.quality_of_service = 'Gold'\n"
        "RETURN t.name AS tunnel_name, t.latency AS tunnel_latency"
    )


def test_metric_semantic_query_renders_count_cypher() -> None:
    spec = SemanticQuerySpec(
        kind="metric_aggregation",
        entities=(SemanticEntityRef(name="service", label="Service", alias="s"),),
        metrics=(
            SemanticMetricRef(
                name="service_count",
                entity="service",
                alias="s",
                aggregation="count",
                expression="count(s)",
                output_alias="service_count",
            ),
        ),
        filters=(SemanticFilterRef(entity="service", alias="s", property="status", operator="=", value="down"),),
    )

    assert CypherRenderer().render(spec) == (
        "MATCH (s:Service)\n"
        "WHERE s.status = 'down'\n"
        "RETURN count(s) AS service_count"
    )


def test_breakdown_semantic_query_renders_grouped_count_cypher() -> None:
    spec = SemanticQuerySpec(
        kind="dimension_breakdown",
        entities=(SemanticEntityRef(name="network_element", label="NetworkElement", alias="ne"),),
        dimensions=(
            SemanticFieldRef(
                name="network_element_vendor",
                entity="network_element",
                alias="ne",
                property="vendor",
                output_alias="network_element_vendor",
            ),
        ),
        metrics=(
            SemanticMetricRef(
                name="network_element_count",
                entity="network_element",
                alias="ne",
                aggregation="count",
                expression="count(ne)",
                output_alias="network_element_count",
            ),
        ),
        order_by=(SemanticOrderBy(expression="network_element_count", direction="DESC"),),
    )

    assert CypherRenderer().render(spec) == (
        "MATCH (ne:NetworkElement)\n"
        "RETURN ne.vendor AS network_element_vendor, count(ne) AS network_element_count\n"
        "ORDER BY network_element_count DESC"
    )


def test_ranking_semantic_query_renders_limit_cypher() -> None:
    spec = SemanticQuerySpec(
        kind="ranking",
        entities=(SemanticEntityRef(name="tunnel", label="Tunnel", alias="t"),),
        projections=(
            SemanticFieldRef(name="tunnel_name", entity="tunnel", alias="t", property="name", output_alias="tunnel_name"),
            SemanticFieldRef(name="tunnel_latency", entity="tunnel", alias="t", property="latency", output_alias="tunnel_latency"),
        ),
        order_by=(SemanticOrderBy(expression="t.latency", direction="DESC"),),
        limit=5,
    )

    assert CypherRenderer().render(spec) == (
        "MATCH (t:Tunnel)\n"
        "RETURN t.name AS tunnel_name, t.latency AS tunnel_latency\n"
        "ORDER BY t.latency DESC\n"
        "LIMIT 5"
    )


def test_existence_semantic_query_renders_boolean_cypher() -> None:
    spec = SemanticQuerySpec(
        kind="existence_check",
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
        filters=(
            SemanticFilterRef(entity="service", alias="s", property="name", operator="=", value="svc-001"),
            SemanticFilterRef(entity="tunnel", alias="t", property="name", operator="=", value="tun-001"),
        ),
        output_alias="exists",
    )

    assert CypherRenderer().render(spec) == (
        "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\n"
        "WHERE s.name = 'svc-001' AND t.name = 'tun-001'\n"
        "RETURN count(*) > 0 AS exists"
    )


def test_legacy_query_plan_module_is_removed() -> None:
    assert importlib.util.find_spec("services.cypher_generator_agent.app.query_plan") is None
