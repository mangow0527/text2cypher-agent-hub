from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.assembly.direction import DirectionMapper, DirectionStatus, derive_direction_mapping
from services.cypher_generator_agent.app.semantic_model.loader import load_graph_semantic_model
from services.cypher_generator_agent.app.semantic_model.model import EdgeDefinition, GraphSemanticModel, VertexDefinition
from services.cypher_generator_agent.app.semantic_model.registry import GraphSemanticRegistry


ARTIFACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "semantic_model"
    / "artifacts"
    / "tugraph_network_semantic_model.yaml"
)


@pytest.fixture(scope="module")
def tugraph_registry() -> GraphSemanticRegistry:
    return load_graph_semantic_model(ARTIFACT_PATH).registry


@pytest.mark.parametrize(
    ("question", "expected_edge"),
    [
        ("查询隧道源设备", "TUNNEL_SRC"),
        ("查询隧道起点设备", "TUNNEL_SRC"),
        ("查询隧道目的设备", "TUNNEL_DST"),
        ("查询隧道宿端设备", "TUNNEL_DST"),
        ("查询隧道到达设备", "TUNNEL_DST"),
        ("查询隧道终点设备", "TUNNEL_DST"),
        ("查询隧道经过的设备", "PATH_THROUGH"),
        ("查询隧道穿过的设备", "PATH_THROUGH"),
        ("查询隧道途经的设备", "PATH_THROUGH"),
    ],
)
def test_resolves_tugraph_tunnel_direction_terms_from_real_semantic_model(
    tugraph_registry: GraphSemanticRegistry,
    question: str,
    expected_edge: str,
) -> None:
    result = DirectionMapper(tugraph_registry).resolve_direction_terms(question)

    assert result.status == DirectionStatus.RESOLVED
    assert result.edge_names == [expected_edge]


def test_source_and_destination_terms_conflict_instead_of_choosing_one(tugraph_registry: GraphSemanticRegistry) -> None:
    result = derive_direction_mapping(tugraph_registry).resolve_direction_terms("查询隧道源和目的设备")

    assert result.status == DirectionStatus.AMBIGUOUS
    assert set(result.edge_names) == {"TUNNEL_SRC", "TUNNEL_DST"}
    assert result.reason == "conflicting_direction_terms"


def test_unqualified_source_term_is_ambiguous_when_registry_has_multiple_source_edges(
    tugraph_registry: GraphSemanticRegistry,
) -> None:
    result = DirectionMapper(tugraph_registry).resolve_direction_terms(["源"])

    assert result.status == DirectionStatus.AMBIGUOUS
    assert {"FIBER_SRC", "LINK_SRC", "TUNNEL_SRC"}.issubset(set(result.edge_names))


def test_unknown_direction_like_word_is_not_open_classified(
    tugraph_registry: GraphSemanticRegistry,
) -> None:
    result = DirectionMapper(tugraph_registry).resolve_direction_terms("查询隧道左侧设备")

    assert result.status == DirectionStatus.UNRESOLVED
    assert result.edge_names == []


def test_unmatched_direction_term_is_unresolved() -> None:
    registry = GraphSemanticRegistry(
        GraphSemanticModel(
            name="tiny",
            vertices=[
                VertexDefinition(name="Tunnel", id_property="id"),
                VertexDefinition(name="NetworkElement", id_property="id"),
            ],
            edges=[
                EdgeDefinition(
                    name="TUNNEL_SRC",
                    **{"from": "Tunnel", "to": "NetworkElement"},
                    cardinality="many_to_one",
                    direction_semantics="语义方向：该 NetworkElement 是隧道源端设备。",
                    ai_context={"synonyms": ["隧道源端"]},
                )
            ],
        )
    )

    result = DirectionMapper(registry).resolve_direction_terms("查询隧道管理设备")

    assert result.status == DirectionStatus.UNRESOLVED
    assert result.edge_names == []
