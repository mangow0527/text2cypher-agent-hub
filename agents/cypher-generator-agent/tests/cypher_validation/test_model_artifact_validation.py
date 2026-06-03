from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from services.cypher_generator_agent.app.semantic_model.loader import load_graph_semantic_model
from services.cypher_generator_agent.app.semantic_model.validator import GraphModelValidationError


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "network_topology_graph_model.yaml"


def test_loader_rejects_mutating_path_pattern_cypher() -> None:
    model = _load_model_dict()
    model["path_patterns"][0]["cypher"] = (
        "MATCH (t:Tunnel {id: $tunnel_id})-[p:PATH_THROUGH]->(ne:NetworkElement)\n"
        "SET ne.name = 'bad'\n"
        "RETURN ne AS device, p.hop_order AS hop"
    )

    with pytest.raises(GraphModelValidationError) as error:
        load_graph_semantic_model(model)

    assert "cypher_readonly_violation" in _error_codes(error.value)
    assert "path_patterns.tunnel_full_path.cypher" in _error_locations(error.value)
    assert "self_validation_check=readonly" in str(error.value)
    assert "self_validation_location=" in str(error.value)


def test_loader_rejects_metric_full_cypher_with_unknown_schema_reference() -> None:
    model = _load_model_dict()
    metric = model["metrics"][0]
    metric.pop("pattern")
    metric.pop("expression")
    metric["valid_dimensions"] = []
    metric["full_cypher"] = "MATCH (x:MissingVertex) RETURN count(x) AS device_count"

    with pytest.raises(GraphModelValidationError) as error:
        load_graph_semantic_model(model)

    assert "cypher_schema_reference_invalid" in _error_codes(error.value)
    assert "metrics.device_count.full_cypher" in _error_locations(error.value)
    assert "self_validation_check=schema_reference" in str(error.value)
    assert "MissingVertex" in str(error.value)


def test_loader_rejects_metric_full_cypher_with_invalid_aggregate_property_type() -> None:
    model = _load_model_dict()
    metric = model["metrics"][0]
    metric.pop("pattern")
    metric.pop("expression")
    metric["valid_dimensions"] = []
    metric["full_cypher"] = "MATCH (ne:NetworkElement) RETURN avg(ne.name + 1) AS avg_name"

    with pytest.raises(GraphModelValidationError) as error:
        load_graph_semantic_model(model)

    assert "cypher_schema_reference_invalid" in _error_codes(error.value)
    assert "metrics.device_count.full_cypher" in _error_locations(error.value)
    assert "self_validation_check=schema_reference" in str(error.value)
    assert "avg" in str(error.value)


def test_loader_accepts_readonly_schema_valid_metric_full_cypher() -> None:
    model = _load_model_dict()
    metric = model["metrics"][0]
    metric.pop("pattern")
    metric.pop("expression")
    metric["valid_dimensions"] = []
    metric["full_cypher"] = "MATCH (ne:NetworkElement) RETURN count(ne) AS device_count"

    loaded = load_graph_semantic_model(model)

    assert loaded.validation_result.is_valid is True
    assert loaded.registry.get_metric("device_count").full_cypher is not None


def _load_model_dict() -> dict[str, Any]:
    with FIXTURE_PATH.open(encoding="utf-8") as file:
        document = yaml.safe_load(file)
    return deepcopy(document["semantic_model"][0])


def _error_codes(error: GraphModelValidationError) -> set[str]:
    return {issue.code for issue in error.validation_result.errors}


def _error_locations(error: GraphModelValidationError) -> set[str]:
    return {issue.location for issue in error.validation_result.errors}
