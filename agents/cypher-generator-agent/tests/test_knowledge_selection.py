from __future__ import annotations

from services.cypher_generator_agent.app.knowledge_selection import semantic_query_to_rag_payload
from services.cypher_generator_agent.app.semantic_query import (
    SemanticEntityRef,
    SemanticFieldRef,
    SemanticFilterRef,
    SemanticQuerySpec,
    SemanticRelationshipRef,
)


def test_semantic_query_to_rag_payload_flattens_symbolic_retrieval_keys() -> None:
    semantic_query = SemanticQuerySpec(
        kind="record_selection",
        intent="record_retrieval_query.related_record_query",
        schema_id="graph_inventory.related_record",
        scenario_id="ops_inventory_static",
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
            ),
        ),
        projections=(
            SemanticFieldRef(
                name="tunnel_name",
                entity="tunnel",
                alias="t",
                property="name",
                output_alias="tunnel_name",
            ),
            SemanticFieldRef(
                name="tunnel_latency",
                entity="tunnel",
                alias="t",
                property="latency",
                output_alias="tunnel_latency",
            ),
        ),
        filters=(
            SemanticFilterRef(
                entity="service",
                alias="s",
                property="quality_of_service",
                operator="=",
                value="Gold",
            ),
        ),
    )

    payload = semantic_query_to_rag_payload(semantic_query)

    assert payload["kind"] == "record_selection"
    assert payload["intent"] == "record_retrieval_query.related_record_query"
    assert payload["schema_id"] == "graph_inventory.related_record"
    assert payload["scenario_id"] == "ops_inventory_static"
    assert payload["entities"] == ["Service", "Tunnel"]
    assert payload["relationships"] == ["SERVICE_USES_TUNNEL"]
    assert payload["properties"] == ["name", "latency", "quality_of_service"]
    assert payload["metrics"] == []
