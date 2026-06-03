from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.semantic_model.loader import load_graph_semantic_model
from services.cypher_generator_agent.app.semantic_model.registry import RegistryLookupError, UnsupportedDirectionError


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "network_topology_graph_model.yaml"
)


@pytest.fixture
def registry():
    return load_graph_semantic_model(FIXTURE_PATH).registry


@pytest.mark.parametrize(
    ("lookup", "args"),
    [
        ("get_vertex", ("MissingVertex",)),
        ("get_edge", ("MISSING_EDGE",)),
        ("get_property", ("NetworkElement", "missing_property")),
        ("get_metric", ("missing_metric",)),
        ("get_path_pattern", ("missing_path",)),
    ],
)
def test_registry_missing_lookup_raises_typed_error(registry, lookup: str, args: tuple[str, ...]) -> None:
    with pytest.raises(RegistryLookupError):
        getattr(registry, lookup)(*args)


def test_edge_connects_respects_storage_direction_and_explicit_reverse(registry) -> None:
    assert registry.edge_connects("SERVICE_USES_TUNNEL", "Service", "Tunnel") is True
    assert registry.edge_connects("SERVICE_USES_TUNNEL", "Tunnel", "Service") is False
    assert registry.edge_connects(
        "SERVICE_USES_TUNNEL",
        "Tunnel",
        "Service",
        direction="reverse",
    ) is True


def test_edge_connects_rejects_unknown_direction_with_typed_error(registry) -> None:
    with pytest.raises(UnsupportedDirectionError):
        registry.edge_connects("SERVICE_USES_TUNNEL", "Service", "Tunnel", direction="sideways")


@pytest.mark.parametrize(
    ("owner", "property_name", "expected_type"),
    [
        ("NetworkElement", "elem_type", "string"),
        ("Tunnel", "bandwidth", "float"),
        ("PATH_THROUGH", "hop_order", "int"),
    ],
)
def test_property_type_returns_declared_property_type(
    registry,
    owner: str,
    property_name: str,
    expected_type: str,
) -> None:
    assert registry.property_type(owner, property_name) == expected_type
