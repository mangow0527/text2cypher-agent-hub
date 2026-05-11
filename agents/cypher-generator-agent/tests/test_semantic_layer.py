from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from services.cypher_generator_agent.app import resource_paths
from services.cypher_generator_agent.app.semantic_layer import (
    SemanticLayerConfigError,
    get_default_semantic_layer,
    load_semantic_layer,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "services/testing_agent/docs/reference/schema.json"


def test_default_semantic_layer_loads_core_service_tunnel_objects() -> None:
    semantic_layer = get_default_semantic_layer()

    service = semantic_layer.entity("service")
    tunnel = semantic_layer.entity("tunnel")
    relationship = semantic_layer.relationship("service_uses_tunnel")
    qos = semantic_layer.property("service_qos")
    latency_metric = semantic_layer.metric("avg_tunnel_latency")

    assert service.label == "Service"
    assert tunnel.label == "Tunnel"
    assert relationship.from_entity == "service"
    assert relationship.edge == "SERVICE_USES_TUNNEL"
    assert relationship.to_entity == "tunnel"
    assert qos.owner == "service"
    assert qos.property == "quality_of_service"
    assert latency_metric.expression == "avg(t.latency)"


def test_default_semantic_layer_uses_tugraph_elem_type_field_for_type_semantics() -> None:
    semantic_layer = get_default_semantic_layer()

    assert semantic_layer.property("service_type").property == "elem_type"
    assert semantic_layer.property("tunnel_type").property == "elem_type"
    assert semantic_layer.property("network_element_type").property == "elem_type"
    assert semantic_layer.property("port_type").property == "elem_type"
    assert semantic_layer.property("fiber_type").property == "elem_type"
    assert semantic_layer.property("link_type").property == "elem_type"


def test_slot_parse_patterns_entity_properties_are_exposed_by_semantic_layer() -> None:
    slot_patterns = yaml.safe_load(resource_paths.slot_parse_patterns_path().read_text(encoding="utf-8"))
    semantic_layer = get_default_semantic_layer()
    exposed_properties = {
        (field.owner, field.property)
        for field in semantic_layer.properties.values()
    }

    missing = sorted(
        f"{entity}.{property_name}"
        for entity, property_names in slot_patterns["entity_properties"].items()
        for property_name in property_names
        if (entity, property_name) not in exposed_properties
    )

    assert missing == []


def test_loader_rejects_invalid_schema_references(tmp_path: Path) -> None:
    config_path = tmp_path / "semantic_layer.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "entities": [
                    {"name": "ghost", "label": "Ghost", "alias": "g", "synonyms": ["幽灵"]},
                    {"name": "service", "label": "Service", "alias": "s", "synonyms": ["服务"]},
                ],
                "relationships": [
                    {
                        "name": "bad_edge",
                        "from": "service",
                        "edge": "SERVICE_USES_TUNNEL",
                        "to": "ghost",
                        "direction": "out",
                        "synonyms": ["使用"],
                    }
                ],
                "properties": [
                    {
                        "name": "bad_property",
                        "owner": "service",
                        "property": "not_in_schema",
                        "synonyms": ["不存在字段"],
                    }
                ],
                "metrics": [],
                "path_patterns": [],
                "value_mappings": [],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(SemanticLayerConfigError) as exc_info:
        load_semantic_layer(config_path, schema_path=SCHEMA_PATH)

    message = str(exc_info.value)
    assert "Ghost" in message
    assert "not_in_schema" in message


def test_loader_rejects_synonym_collisions(tmp_path: Path) -> None:
    config_path = tmp_path / "semantic_layer.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "entities": [
                    {"name": "service", "label": "Service", "alias": "s", "synonyms": ["业务"]},
                    {"name": "tunnel", "label": "Tunnel", "alias": "t", "synonyms": ["业务"]},
                ],
                "relationships": [],
                "properties": [],
                "metrics": [],
                "path_patterns": [],
                "value_mappings": [],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(SemanticLayerConfigError, match="业务"):
        load_semantic_layer(config_path, schema_path=SCHEMA_PATH)
