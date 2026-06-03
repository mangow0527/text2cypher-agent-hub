from __future__ import annotations

import json
from pathlib import Path

from services.cypher_generator_agent.app.semantic_model.loader import load_graph_semantic_model
from services.cypher_generator_agent.app.semantic_model.tugraph_schema import (
    build_graph_semantic_model_from_tugraph_schema,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "testing_agent"
    / "docs"
    / "reference"
    / "schema.json"
)


def test_converts_reference_tugraph_schema_to_loadable_graph_semantic_model() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    model = build_graph_semantic_model_from_tugraph_schema(
        schema,
        model_name="network_schema_v10",
    )
    loaded = load_graph_semantic_model(model)

    registry = loaded.registry
    assert registry.model.name == "network_schema_v10"
    assert {vertex.name for vertex in registry.model.vertices} == {
        "NetworkElement",
        "Protocol",
        "Tunnel",
        "Service",
        "Port",
        "Fiber",
        "Link",
    }
    assert {edge.name for edge in registry.model.edges} == {
        "HAS_PORT",
        "FIBER_SRC",
        "FIBER_DST",
        "LINK_SRC",
        "LINK_DST",
        "TUNNEL_SRC",
        "TUNNEL_DST",
        "TUNNEL_PROTO",
        "PATH_THROUGH",
        "SERVICE_USES_TUNNEL",
    }
    assert registry.get_edge("SERVICE_USES_TUNNEL").from_vertex == "Service"
    assert registry.get_edge("SERVICE_USES_TUNNEL").to_vertex == "Tunnel"
    assert registry.get_property("PATH_THROUGH", "hop_order").type == "int"
    assert registry.get_property("Tunnel", "bandwidth").type == "float"
    assert registry.get_property("Service", "quality_of_service").valid_values == [
        "Gold",
        "Silver",
        "Bronze",
        "Best_Effort",
    ]
    assert registry.get_property("NetworkElement", "elem_type").value_synonyms["firewall"] == [
        "防火墙",
        "FW",
    ]
    assert registry.get_path_pattern("tunnel_full_path").name == "tunnel_full_path"
    assert registry.get_metric("device_count").valid_dimensions == [
        "ne.elem_type",
        "ne.vendor",
        "ne.location",
    ]
