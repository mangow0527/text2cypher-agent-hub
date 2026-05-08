from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml


Direction = Literal["out", "in", "undirected"]


class SemanticLayerConfigError(ValueError):
    """Raised when semantic layer configuration cannot be trusted."""


@dataclass(frozen=True)
class SemanticEntity:
    name: str
    label: str
    alias: str
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticRelationship:
    name: str
    from_entity: str
    edge: str
    to_entity: str
    direction: Direction
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticProperty:
    name: str
    owner: str
    property: str
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticMetric:
    name: str
    owner: str
    aggregation: str
    expression: str
    property: str | None = None
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticPathPattern:
    name: str
    relationships: tuple[str, ...]


@dataclass(frozen=True)
class SemanticValueMapping:
    name: str
    owner: str
    property: str
    values: dict[str, str]


@dataclass(frozen=True)
class GraphSchema:
    vertex_properties: dict[str, set[str]]
    edge_properties: dict[str, set[str]]
    edge_constraints: dict[str, set[tuple[str, str]]]


class SemanticLayer:
    def __init__(
        self,
        *,
        entities: list[SemanticEntity],
        relationships: list[SemanticRelationship],
        properties: list[SemanticProperty],
        metrics: list[SemanticMetric],
        path_patterns: list[SemanticPathPattern],
        value_mappings: list[SemanticValueMapping],
    ) -> None:
        self.entities = {entity.name: entity for entity in entities}
        self.relationships = {relationship.name: relationship for relationship in relationships}
        self.properties = {field.name: field for field in properties}
        self.metrics = {metric.name: metric for metric in metrics}
        self.path_patterns = {pattern.name: pattern for pattern in path_patterns}
        self.value_mappings = {mapping.name: mapping for mapping in value_mappings}

    def entity(self, name: str) -> SemanticEntity:
        return self.entities[name]

    def relationship(self, name: str) -> SemanticRelationship:
        return self.relationships[name]

    def property(self, name: str) -> SemanticProperty:
        return self.properties[name]

    def metric(self, name: str) -> SemanticMetric:
        return self.metrics[name]


def load_semantic_layer(path: Path, *, schema_path: Path | None = None) -> SemanticLayer:
    document = _load_yaml_mapping(path)
    schema = _load_graph_schema(schema_path or _default_schema_path())

    entities = [
        SemanticEntity(
            name=_required_str(item, "name", "entities"),
            label=_required_str(item, "label", "entities"),
            alias=_required_str(item, "alias", "entities"),
            synonyms=_string_tuple(item.get("synonyms", []), "entities.synonyms"),
        )
        for item in _required_list(document, "entities")
        if _is_mapping(item, "entities")
    ]
    relationships = [
        SemanticRelationship(
            name=_required_str(item, "name", "relationships"),
            from_entity=_required_str(item, "from", "relationships"),
            edge=_required_str(item, "edge", "relationships"),
            to_entity=_required_str(item, "to", "relationships"),
            direction=_direction(_required_str(item, "direction", "relationships")),
            synonyms=_string_tuple(item.get("synonyms", []), "relationships.synonyms"),
        )
        for item in _required_list(document, "relationships")
        if _is_mapping(item, "relationships")
    ]
    properties = [
        SemanticProperty(
            name=_required_str(item, "name", "properties"),
            owner=_required_str(item, "owner", "properties"),
            property=_required_str(item, "property", "properties"),
            synonyms=_string_tuple(item.get("synonyms", []), "properties.synonyms"),
        )
        for item in _required_list(document, "properties")
        if _is_mapping(item, "properties")
    ]
    metrics = [
        SemanticMetric(
            name=_required_str(item, "name", "metrics"),
            owner=_required_str(item, "owner", "metrics"),
            property=_optional_str(item.get("property"), "metrics.property"),
            aggregation=_required_str(item, "aggregation", "metrics"),
            expression=_required_str(item, "expression", "metrics"),
            synonyms=_string_tuple(item.get("synonyms", []), "metrics.synonyms"),
        )
        for item in _required_list(document, "metrics")
        if _is_mapping(item, "metrics")
    ]
    path_patterns = [
        SemanticPathPattern(
            name=_required_str(item, "name", "path_patterns"),
            relationships=_string_tuple(item.get("relationships", []), "path_patterns.relationships"),
        )
        for item in _required_list(document, "path_patterns")
        if _is_mapping(item, "path_patterns")
    ]
    value_mappings = [
        SemanticValueMapping(
            name=_required_str(item, "name", "value_mappings"),
            owner=_required_str(item, "owner", "value_mappings"),
            property=_required_str(item, "property", "value_mappings"),
            values=_string_mapping(item.get("values", {}), "value_mappings.values"),
        )
        for item in _required_list(document, "value_mappings")
        if _is_mapping(item, "value_mappings")
    ]

    layer = SemanticLayer(
        entities=entities,
        relationships=relationships,
        properties=properties,
        metrics=metrics,
        path_patterns=path_patterns,
        value_mappings=value_mappings,
    )
    _validate_semantic_layer(layer, schema)
    return layer


@lru_cache(maxsize=1)
def get_default_semantic_layer() -> SemanticLayer:
    return load_semantic_layer(_default_config_path())


def _validate_semantic_layer(layer: SemanticLayer, schema: GraphSchema) -> None:
    diagnostics: list[str] = []
    diagnostics.extend(_duplicate_name_diagnostics("entity", layer.entities))
    diagnostics.extend(_duplicate_name_diagnostics("relationship", layer.relationships))
    diagnostics.extend(_duplicate_name_diagnostics("property", layer.properties))
    diagnostics.extend(_duplicate_name_diagnostics("metric", layer.metrics))

    for entity in layer.entities.values():
        if entity.label not in schema.vertex_properties:
            diagnostics.append(f"entity {entity.name!r} references unknown label {entity.label!r}")

    for relationship in layer.relationships.values():
        from_entity = layer.entities.get(relationship.from_entity)
        to_entity = layer.entities.get(relationship.to_entity)
        if from_entity is None:
            diagnostics.append(
                f"relationship {relationship.name!r} references unknown from entity {relationship.from_entity!r}"
            )
        if to_entity is None:
            diagnostics.append(f"relationship {relationship.name!r} references unknown to entity {relationship.to_entity!r}")
        if relationship.edge not in schema.edge_constraints:
            diagnostics.append(f"relationship {relationship.name!r} references unknown edge {relationship.edge!r}")
        if from_entity and to_entity and relationship.edge in schema.edge_constraints:
            expected = (from_entity.label, to_entity.label)
            constraints = schema.edge_constraints[relationship.edge]
            if relationship.direction == "in":
                expected = (to_entity.label, from_entity.label)
            if expected not in constraints:
                diagnostics.append(
                    f"relationship {relationship.name!r} edge {relationship.edge!r} does not allow "
                    f"{from_entity.label!r} -> {to_entity.label!r}"
                )

    for field in layer.properties.values():
        _validate_owner_property("property", field.name, field.owner, field.property, layer, schema, diagnostics)

    for metric in layer.metrics.values():
        if metric.owner not in layer.entities:
            diagnostics.append(f"metric {metric.name!r} references unknown owner {metric.owner!r}")
        if metric.property is not None:
            _validate_owner_property("metric", metric.name, metric.owner, metric.property, layer, schema, diagnostics)

    for pattern in layer.path_patterns.values():
        for relationship_name in pattern.relationships:
            if relationship_name not in layer.relationships:
                diagnostics.append(
                    f"path pattern {pattern.name!r} references unknown relationship {relationship_name!r}"
                )

    for mapping in layer.value_mappings.values():
        _validate_owner_property("value mapping", mapping.name, mapping.owner, mapping.property, layer, schema, diagnostics)

    diagnostics.extend(_synonym_collision_diagnostics(layer))

    if diagnostics:
        raise SemanticLayerConfigError("; ".join(diagnostics))


def _validate_owner_property(
    kind: str,
    name: str,
    owner: str,
    property_name: str,
    layer: SemanticLayer,
    schema: GraphSchema,
    diagnostics: list[str],
) -> None:
    entity = layer.entities.get(owner)
    if entity is None:
        diagnostics.append(f"{kind} {name!r} references unknown owner {owner!r}")
        return
    if property_name not in schema.vertex_properties.get(entity.label, set()):
        diagnostics.append(f"{kind} {name!r} references unknown property {property_name!r} on {entity.label!r}")


def _synonym_collision_diagnostics(layer: SemanticLayer) -> list[str]:
    owners: dict[str, str] = {}
    diagnostics: list[str] = []
    collections: list[tuple[str, dict[str, Any]]] = [
        ("entity", layer.entities),
        ("relationship", layer.relationships),
        ("property", layer.properties),
        ("metric", layer.metrics),
    ]
    for kind, items in collections:
        for name, item in items.items():
            for synonym in item.synonyms:
                normalized = synonym.casefold().strip()
                previous = owners.get(normalized)
                owner = f"{kind} {name!r}"
                if previous is not None and previous != owner:
                    diagnostics.append(f"synonym {synonym!r} is shared by {previous} and {owner}")
                owners[normalized] = owner
    return diagnostics


def _load_graph_schema(path: Path) -> GraphSchema:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SemanticLayerConfigError(f"{path} must contain a JSON list")

    vertex_properties: dict[str, set[str]] = {}
    edge_properties: dict[str, set[str]] = {}
    edge_constraints: dict[str, set[tuple[str, str]]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        schema_type = item.get("type")
        properties = {
            prop["name"]
            for prop in item.get("properties", [])
            if isinstance(prop, dict) and isinstance(prop.get("name"), str)
        }
        if isinstance(label, str) and schema_type == "VERTEX":
            vertex_properties[label] = properties
        elif isinstance(label, str) and schema_type == "EDGE":
            edge_properties[label] = properties
            edge_constraints[label] = {
                (constraint[0], constraint[1])
                for constraint in item.get("constraints", [])
                if isinstance(constraint, list)
                and len(constraint) == 2
                and isinstance(constraint[0], str)
                and isinstance(constraint[1], str)
            }
    return GraphSchema(
        vertex_properties=vertex_properties,
        edge_properties=edge_properties,
        edge_constraints=edge_constraints,
    )


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SemanticLayerConfigError(f"{path} must contain a YAML mapping")
    return payload


def _required_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise SemanticLayerConfigError(f"{key} must be a list")
    return value


def _required_str(payload: dict[str, Any], key: str, section: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise SemanticLayerConfigError(f"{section}.{key} must be a non-empty string")
    return value


def _optional_str(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SemanticLayerConfigError(f"{field} must be a non-empty string when provided")
    return value


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise SemanticLayerConfigError(f"{field} must be a list of non-empty strings")
    return tuple(value)


def _string_mapping(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise SemanticLayerConfigError(f"{field} must be a string-to-string mapping")
    return value


def _direction(value: str) -> Direction:
    if value not in {"out", "in", "undirected"}:
        raise SemanticLayerConfigError(f"relationships.direction must be one of: out, in, undirected")
    return value


def _is_mapping(value: Any, section: str) -> bool:
    if not isinstance(value, dict):
        raise SemanticLayerConfigError(f"{section} entries must be mappings")
    return True


def _duplicate_name_diagnostics(kind: str, items: dict[str, Any]) -> list[str]:
    # Duplicates collapse in dictionaries; retained as a hook for callers that inspect diagnostics.
    return [f"duplicate {kind} name {name!r}" for name, item in items.items() if item.name != name]


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "semantic_layer.yaml"


def _default_schema_path() -> Path:
    return Path(__file__).resolve().parents[3] / "services" / "testing_agent" / "docs" / "reference" / "schema.json"
