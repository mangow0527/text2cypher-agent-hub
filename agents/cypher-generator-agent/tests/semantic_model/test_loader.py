from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from services.cypher_generator_agent.app.semantic_model.loader import load_graph_semantic_model
from services.cypher_generator_agent.app.semantic_model.validator import GraphModelValidationError


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "network_topology_graph_model.yaml"
)


def test_loads_network_topology_fixture_with_stable_checksum_and_registry() -> None:
    loaded_from_path = load_graph_semantic_model(FIXTURE_PATH)
    loaded_from_dict = load_graph_semantic_model(_load_model_dict())

    assert loaded_from_path.validation_result.is_valid is True
    assert loaded_from_path.model_checksum == loaded_from_dict.model_checksum
    assert len(loaded_from_path.model_checksum) == 64

    registry = loaded_from_path.registry
    assert registry.get_vertex("NetworkElement").id_property == "id"
    assert registry.get_edge("SERVICE_USES_TUNNEL").from_vertex == "Service"
    assert registry.get_property("NetworkElement", "elem_type").type == "string"
    assert registry.get_metric("device_count").expression == "count(ne)"
    assert registry.get_path_pattern("tunnel_full_path").parameters[0].name == "tunnel_id"


def test_rejects_unknown_edge_endpoint() -> None:
    model = _load_model_dict()
    model["edges"][0]["to"] = "MissingVertex"

    with pytest.raises(GraphModelValidationError) as error:
        load_graph_semantic_model(model)

    assert "unknown edge endpoint" in str(error.value)


def test_rejects_duplicate_vertex_names() -> None:
    model = _load_model_dict()
    duplicate = deepcopy(model["vertices"][0])
    model["vertices"].append(duplicate)

    with pytest.raises(GraphModelValidationError) as error:
        load_graph_semantic_model(model)

    assert "duplicate_vertex" in _error_codes(error.value)


def test_rejects_duplicate_property_names_within_owner() -> None:
    model = _load_model_dict()
    duplicate = deepcopy(_property(model, "NetworkElement", "id"))
    model["vertices"][0]["properties"].append(duplicate)

    with pytest.raises(GraphModelValidationError) as error:
        load_graph_semantic_model(model)

    assert "duplicate_property" in _error_codes(error.value)


def test_rejects_vertex_id_property_that_is_not_declared() -> None:
    model = _load_model_dict()
    model["vertices"][0]["id_property"] = "missing_id"

    with pytest.raises(GraphModelValidationError) as error:
        load_graph_semantic_model(model)

    assert "id_property" in str(error.value)


def test_rejects_value_synonyms_key_outside_valid_values() -> None:
    model = _load_model_dict()
    elem_type = _property(model, "NetworkElement", "elem_type")
    elem_type["value_synonyms"]["bridge"] = ["网桥"]

    with pytest.raises(GraphModelValidationError) as error:
        load_graph_semantic_model(model)

    assert "value_synonyms" in str(error.value)


@pytest.mark.parametrize(
    ("dimension", "expected_message"),
    [
        ("ghost.elem_type", "unknown alias"),
        ("ne.missing_property", "unknown property"),
    ],
)
def test_rejects_metric_dimensions_that_do_not_trace_to_pattern_property(
    dimension: str,
    expected_message: str,
) -> None:
    model = _load_model_dict()
    model["metrics"][0]["valid_dimensions"] = [dimension]

    with pytest.raises(GraphModelValidationError) as error:
        load_graph_semantic_model(model)

    assert expected_message in str(error.value)


def test_rejects_metric_pattern_with_unknown_anonymous_vertex_label() -> None:
    model = _load_model_dict()
    model["metrics"][0]["pattern"] = "(ne:NetworkElement)-[:HAS_PORT]->(:MissingVertex)"

    with pytest.raises(GraphModelValidationError) as error:
        load_graph_semantic_model(model)

    assert "unknown_metric_vertex" in _error_codes(error.value)


def test_rejects_metric_pattern_with_unknown_alternative_edge_type() -> None:
    model = _load_model_dict()
    model["metrics"][1]["pattern"] = "(ne:NetworkElement)-[:HAS_PORT|MISSING_EDGE]->(port:Port)"

    with pytest.raises(GraphModelValidationError) as error:
        load_graph_semantic_model(model)

    assert "unknown_metric_edge" in _error_codes(error.value)


def test_rejects_metric_expression_with_unknown_alias() -> None:
    model = _load_model_dict()
    model["metrics"][0]["expression"] = "count(ghost)"

    with pytest.raises(GraphModelValidationError) as error:
        load_graph_semantic_model(model)

    assert "unknown_metric_expression_alias" in _error_codes(error.value)


def test_rejects_metric_pattern_with_duplicate_alias() -> None:
    model = _load_model_dict()
    model["metrics"][1]["pattern"] = "(ne:NetworkElement)-[:HAS_PORT]->(ne:Port)"

    with pytest.raises(GraphModelValidationError) as error:
        load_graph_semantic_model(model)

    assert "duplicate_metric_alias" in _error_codes(error.value)


def test_rejects_metric_with_full_cypher_and_pattern_expression() -> None:
    model = _load_model_dict()
    model["metrics"][0]["full_cypher"] = "MATCH (ne:NetworkElement) RETURN count(ne)"

    with pytest.raises(GraphModelValidationError) as error:
        load_graph_semantic_model(model)

    assert "full_cypher" in str(error.value)
    assert "pattern" in str(error.value)


def _load_model_dict() -> dict[str, Any]:
    with FIXTURE_PATH.open(encoding="utf-8") as file:
        document = yaml.safe_load(file)
    return deepcopy(document["semantic_model"][0])


def _property(model: dict[str, Any], owner: str, property_name: str) -> dict[str, Any]:
    for vertex in model["vertices"]:
        if vertex["name"] == owner:
            return next(prop for prop in vertex["properties"] if prop["name"] == property_name)
    for edge in model["edges"]:
        if edge["name"] == owner:
            return next(prop for prop in edge.get("properties", []) if prop["name"] == property_name)
    raise AssertionError(f"missing property {owner}.{property_name}")


def _error_codes(error: GraphModelValidationError) -> set[str]:
    return {issue.code for issue in error.validation_result.errors}
