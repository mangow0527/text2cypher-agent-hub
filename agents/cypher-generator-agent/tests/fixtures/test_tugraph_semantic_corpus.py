from __future__ import annotations

from pathlib import Path

from services.cypher_generator_agent.app.literals.value_index import StaticValueIndex
from services.cypher_generator_agent.app.semantic_model.loader import load_graph_semantic_model


FIXTURE_DIR = Path(__file__).resolve().parent
ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "app" / "semantic_model" / "artifacts"
TUGRAPH_MODEL_PATH = ARTIFACT_DIR / "tugraph_network_semantic_model.yaml"
TUGRAPH_VALUE_INDEX_PATH = ARTIFACT_DIR / "tugraph_value_index.json"


def test_tugraph_semantic_corpus_files_are_loadable() -> None:
    loaded = load_graph_semantic_model(TUGRAPH_MODEL_PATH)
    value_index = StaticValueIndex.from_path(TUGRAPH_VALUE_INDEX_PATH)

    registry = loaded.registry
    assert registry.model.name == "network_schema_v10"
    assert len(registry.model.vertices) == 7
    assert len(registry.model.edges) == 10
    assert registry.get_property("Service", "quality_of_service").valid_values == [
        "Gold",
        "Silver",
        "Bronze",
        "Best_Effort",
    ]
    assert registry.get_edge("SERVICE_USES_TUNNEL").from_vertex == "Service"
    assert registry.get_path_pattern("tunnel_full_path").parameters[0].name == "tunnel_id"

    assert value_index.lookup_exact("Service", "quality_of_service", "Gold").value == "Gold"
    assert value_index.lookup_exact("NetworkElement", "elem_type", "firewall").value == "firewall"
    assert value_index.lookup_exact("Tunnel", "elem_type", "MPLS-TE").value == "MPLS-TE"


def test_packaged_tugraph_semantic_model_matches_fixture_snapshot() -> None:
    assert TUGRAPH_MODEL_PATH.read_text(encoding="utf-8") == (
        FIXTURE_DIR / "tugraph_network_graph_model.yaml"
    ).read_text(encoding="utf-8")
    assert TUGRAPH_VALUE_INDEX_PATH.read_text(encoding="utf-8") == (
        FIXTURE_DIR / "tugraph_value_index.json"
    ).read_text(encoding="utf-8")
