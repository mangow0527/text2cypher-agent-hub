from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from . import resource_paths


Direction = Literal["out", "in", "undirected"]


class GraphSemanticViewConfigError(ValueError):
    """Raised when the graph semantic view cannot be trusted."""


@dataclass(frozen=True)
class SemanticEntity:
    name: str
    name_zh: str
    label: str
    alias: str
    description: str
    synonyms: tuple[str, ...]
    primary_key: str
    display_fields: tuple[str, ...]
    default_order_by: str | None = None


@dataclass(frozen=True)
class SemanticRelationship:
    name: str
    name_zh: str
    from_entity: str
    edge: str
    to_entity: str
    direction: Direction
    description: str
    synonyms: tuple[str, ...]
    negative_phrases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticField:
    name: str
    name_zh: str
    owner: str
    property: str
    description: str
    synonyms: tuple[str, ...]
    roles: tuple[str, ...]
    value_type: str
    enum_values: tuple[str, ...] = ()
    value_aliases: dict[str, tuple[str, ...]] | None = None
    unit: str | None = None
    default_aggregations: tuple[str, ...] = ()
    field_kind: Literal["dimension", "fact"] = "dimension"


@dataclass(frozen=True)
class SemanticMetric:
    name: str
    name_zh: str
    description: str
    aggregation: str
    target_entity: str
    expression: str
    property: str | None
    synonyms: tuple[str, ...]
    default_alias: str
    path_semantic: str | None = None


@dataclass(frozen=True)
class SemanticPath:
    name: str
    name_zh: str
    description: str
    source_entity: str
    target_entity: str
    intermediate_entities: tuple[str, ...]
    relationships: tuple[str, ...]
    trigger_phrases: tuple[str, ...]
    negative_phrases: tuple[str, ...]
    default_return_fields: tuple[str, ...]


@dataclass(frozen=True)
class SemanticReturnPolicy:
    name: str
    name_zh: str
    applies_to_path_semantics: tuple[str, ...]
    applies_to_entities: tuple[str, ...]
    rules: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class SemanticDisambiguationRule:
    rule_id: str
    name_zh: str
    positive_patterns: tuple[str, ...]
    negative_patterns: tuple[str, ...]
    prefer: str
    reject: tuple[str, ...]
    explanation: str


@dataclass(frozen=True)
class GraphSchema:
    vertex_properties: dict[str, set[str]]
    edge_properties: dict[str, set[str]]
    edge_constraints: dict[str, set[tuple[str, str]]]


class GraphSemanticView:
    def __init__(
        self,
        *,
        version: int,
        view_id: str,
        name_zh: str,
        description: str,
        entities: list[SemanticEntity],
        dimensions: list[SemanticField],
        facts: list[SemanticField],
        metrics: list[SemanticMetric],
        relationships: list[SemanticRelationship],
        path_semantics: list[SemanticPath],
        return_policies: list[SemanticReturnPolicy],
        disambiguation_rules: list[SemanticDisambiguationRule],
    ) -> None:
        self.version = version
        self.view_id = view_id
        self.name_zh = name_zh
        self.description = description
        self.entities = {entity.name: entity for entity in entities}
        self.dimensions = {field.name: field for field in dimensions}
        self.facts = {field.name: field for field in facts}
        self.metrics = {metric.name: metric for metric in metrics}
        self.relationships = {relationship.name: relationship for relationship in relationships}
        self.path_semantics = {path.name: path for path in path_semantics}
        self.return_policies = {policy.name: policy for policy in return_policies}
        self.disambiguation_rules = tuple(disambiguation_rules)

    @property
    def fields(self) -> dict[str, SemanticField]:
        return {**self.dimensions, **self.facts}

    def entity(self, name: str) -> SemanticEntity:
        return self.entities[name]

    def dimension(self, name: str) -> SemanticField:
        return self.dimensions[name]

    def fact(self, name: str) -> SemanticField:
        return self.facts[name]

    def field(self, name: str) -> SemanticField:
        return self.fields[name]

    def metric(self, name: str) -> SemanticMetric:
        return self.metrics[name]

    def relationship(self, name: str) -> SemanticRelationship:
        return self.relationships[name]

    def path_semantic(self, name: str) -> SemanticPath:
        return self.path_semantics[name]

    def return_policy(self, name: str) -> SemanticReturnPolicy:
        return self.return_policies[name]

    def field_by_owner_property(self, owner: str, property_name: str) -> SemanticField | None:
        for field in self.fields.values():
            if field.owner == owner and field.property == property_name:
                return field
        return None


def load_graph_semantic_view(path: Path, *, schema_path: Path | None = None) -> GraphSemanticView:
    document = _load_yaml_mapping(path)
    schema = _load_graph_schema(schema_path or _default_schema_path())

    entities = [
        SemanticEntity(
            name=name,
            name_zh=_required_str(item, "name_zh", f"entities.{name}"),
            label=_required_str(item, "label", f"entities.{name}"),
            alias=_required_str(item, "alias", f"entities.{name}"),
            description=_required_str(item, "description", f"entities.{name}"),
            synonyms=_string_tuple(item.get("synonyms", []), f"entities.{name}.synonyms"),
            primary_key=_required_str(item, "primary_key", f"entities.{name}"),
            display_fields=_string_tuple(item.get("display_fields", []), f"entities.{name}.display_fields"),
            default_order_by=_optional_str(item.get("default_order_by"), f"entities.{name}.default_order_by"),
        )
        for name, item in _required_mapping(document, "entities").items()
        if _is_mapping(item, f"entities.{name}")
    ]
    dimensions = [
        _field_from_config(name, item, kind="dimension")
        for name, item in _required_mapping(document, "dimensions").items()
        if _is_mapping(item, f"dimensions.{name}")
    ]
    facts = [
        _field_from_config(name, item, kind="fact")
        for name, item in _required_mapping(document, "facts").items()
        if _is_mapping(item, f"facts.{name}")
    ]
    metrics = [
        SemanticMetric(
            name=name,
            name_zh=_required_str(item, "name_zh", f"metrics.{name}"),
            description=_required_str(item, "description", f"metrics.{name}"),
            aggregation=_required_str(item, "aggregation", f"metrics.{name}"),
            target_entity=_required_str(_required_mapping(item, "target"), "entity", f"metrics.{name}.target"),
            property=_optional_str(_required_mapping(item, "target").get("field"), f"metrics.{name}.target.field"),
            expression=_required_str(item, "expression", f"metrics.{name}"),
            path_semantic=_optional_str(item.get("path_semantic"), f"metrics.{name}.path_semantic"),
            synonyms=_string_tuple(item.get("synonyms", []), f"metrics.{name}.synonyms"),
            default_alias=_required_str(item, "default_alias", f"metrics.{name}"),
        )
        for name, item in _required_mapping(document, "metrics").items()
        if _is_mapping(item, f"metrics.{name}")
    ]
    relationships = [
        SemanticRelationship(
            name=name,
            name_zh=_required_str(item, "name_zh", f"relationships.{name}"),
            from_entity=_required_str(item, "from", f"relationships.{name}"),
            edge=_required_str(item, "edge", f"relationships.{name}"),
            to_entity=_required_str(item, "to", f"relationships.{name}"),
            direction=_direction(_required_str(item, "direction", f"relationships.{name}")),
            description=_required_str(item, "description", f"relationships.{name}"),
            synonyms=_string_tuple(item.get("synonyms", []), f"relationships.{name}.synonyms"),
            negative_phrases=_string_tuple(item.get("negative_phrases", []), f"relationships.{name}.negative_phrases"),
        )
        for name, item in _required_mapping(document, "relationships").items()
        if _is_mapping(item, f"relationships.{name}")
    ]
    path_semantics = [
        SemanticPath(
            name=name,
            name_zh=_required_str(item, "name_zh", f"path_semantics.{name}"),
            description=_required_str(item, "description", f"path_semantics.{name}"),
            source_entity=_required_str(item, "source_entity", f"path_semantics.{name}"),
            target_entity=_required_str(item, "target_entity", f"path_semantics.{name}"),
            intermediate_entities=_string_tuple(
                item.get("intermediate_entities", []), f"path_semantics.{name}.intermediate_entities"
            ),
            relationships=tuple(
                _required_str(path_item, "relationship", f"path_semantics.{name}.path")
                for path_item in _required_list(item, "path")
                if _is_mapping(path_item, f"path_semantics.{name}.path")
            ),
            trigger_phrases=_string_tuple(item.get("trigger_phrases", []), f"path_semantics.{name}.trigger_phrases"),
            negative_phrases=_string_tuple(item.get("negative_phrases", []), f"path_semantics.{name}.negative_phrases"),
            default_return_fields=_string_tuple(
                item.get("default_return_fields", []), f"path_semantics.{name}.default_return_fields"
            ),
        )
        for name, item in _required_mapping(document, "path_semantics").items()
        if _is_mapping(item, f"path_semantics.{name}")
    ]
    return_policies = [
        SemanticReturnPolicy(
            name=name,
            name_zh=_required_str(item, "name_zh", f"return_policies.{name}"),
            applies_to_path_semantics=_string_tuple(
                _required_mapping(item, "applies_to").get("path_semantics", []),
                f"return_policies.{name}.applies_to.path_semantics",
            ),
            applies_to_entities=_string_tuple(
                _required_mapping(item, "applies_to").get("entities", []),
                f"return_policies.{name}.applies_to.entities",
            ),
            rules=tuple(dict(rule) for rule in _required_list(item, "rules") if _is_mapping(rule, f"return_policies.{name}.rules")),
        )
        for name, item in _required_mapping(document, "return_policies").items()
        if _is_mapping(item, f"return_policies.{name}")
    ]
    disambiguation_rules = [
        SemanticDisambiguationRule(
            rule_id=_required_str(item, "rule_id", "disambiguation_rules"),
            name_zh=_required_str(item, "name_zh", "disambiguation_rules"),
            positive_patterns=_string_tuple(item.get("positive_patterns", []), "disambiguation_rules.positive_patterns"),
            negative_patterns=_string_tuple(item.get("negative_patterns", []), "disambiguation_rules.negative_patterns"),
            prefer=_required_str(item, "prefer", "disambiguation_rules"),
            reject=_string_tuple(item.get("reject", []), "disambiguation_rules.reject"),
            explanation=_required_str(item, "explanation", "disambiguation_rules"),
        )
        for item in _required_list(document, "disambiguation_rules")
        if _is_mapping(item, "disambiguation_rules")
    ]

    view = GraphSemanticView(
        version=int(document.get("version", 1)),
        view_id=_required_str(document, "view_id", "root"),
        name_zh=_required_str(document, "name_zh", "root"),
        description=_required_str(document, "description", "root"),
        entities=entities,
        dimensions=dimensions,
        facts=facts,
        metrics=metrics,
        relationships=relationships,
        path_semantics=path_semantics,
        return_policies=return_policies,
        disambiguation_rules=disambiguation_rules,
    )
    _validate_graph_semantic_view(view, schema)
    return view


@lru_cache(maxsize=1)
def get_default_graph_semantic_view() -> GraphSemanticView:
    return load_graph_semantic_view(resource_paths.graph_semantic_view_path())


def _field_from_config(name: str, item: dict[str, Any], *, kind: Literal["dimension", "fact"]) -> SemanticField:
    value_aliases = item.get("value_aliases")
    aliases = None
    if isinstance(value_aliases, dict):
        aliases = {
            str(value): _string_tuple(phrases, f"{kind}s.{name}.value_aliases.{value}")
            for value, phrases in value_aliases.items()
        }
    return SemanticField(
        name=name,
        name_zh=_required_str(item, "name_zh", f"{kind}s.{name}"),
        owner=_required_str(item, "owner", f"{kind}s.{name}"),
        property=_required_str(item, "property", f"{kind}s.{name}"),
        description=_required_str(item, "description", f"{kind}s.{name}"),
        synonyms=_string_tuple(item.get("synonyms", []), f"{kind}s.{name}.synonyms"),
        roles=_string_tuple(item.get("roles", []), f"{kind}s.{name}.roles"),
        value_type=_required_str(item, "value_type", f"{kind}s.{name}"),
        enum_values=_string_tuple(item.get("enum_values", []), f"{kind}s.{name}.enum_values"),
        value_aliases=aliases,
        unit=_optional_str(item.get("unit"), f"{kind}s.{name}.unit"),
        default_aggregations=_string_tuple(
            item.get("default_aggregations", []), f"{kind}s.{name}.default_aggregations"
        ),
        field_kind=kind,
    )


def _validate_graph_semantic_view(view: GraphSemanticView, schema: GraphSchema) -> None:
    diagnostics: list[str] = []
    diagnostics.extend(_duplicate_name_diagnostics("entity", view.entities))
    diagnostics.extend(_duplicate_name_diagnostics("dimension", view.dimensions))
    diagnostics.extend(_duplicate_name_diagnostics("fact", view.facts))
    diagnostics.extend(_duplicate_name_diagnostics("metric", view.metrics))
    diagnostics.extend(_duplicate_name_diagnostics("relationship", view.relationships))
    diagnostics.extend(_duplicate_name_diagnostics("path_semantic", view.path_semantics))

    for entity in view.entities.values():
        if entity.label not in schema.vertex_properties:
            diagnostics.append(f"entity {entity.name!r} references unknown label {entity.label!r}")
        for field_name in entity.display_fields:
            if field_name not in schema.vertex_properties.get(entity.label, set()):
                diagnostics.append(f"entity {entity.name!r} display field {field_name!r} is missing on {entity.label!r}")

    for field in view.fields.values():
        _validate_owner_property(field.field_kind, field.name, field.owner, field.property, view, schema, diagnostics)

    for metric in view.metrics.values():
        if metric.target_entity not in view.entities:
            diagnostics.append(f"metric {metric.name!r} references unknown target entity {metric.target_entity!r}")
        if metric.property is not None:
            _validate_owner_property("metric", metric.name, metric.target_entity, metric.property, view, schema, diagnostics)
        if metric.path_semantic is not None and metric.path_semantic not in view.path_semantics:
            diagnostics.append(f"metric {metric.name!r} references unknown path semantic {metric.path_semantic!r}")

    for relationship in view.relationships.values():
        _validate_relationship(relationship, view, schema, diagnostics)

    for path in view.path_semantics.values():
        if path.source_entity not in view.entities:
            diagnostics.append(f"path_semantic {path.name!r} references unknown source entity {path.source_entity!r}")
        if path.target_entity not in view.entities:
            diagnostics.append(f"path_semantic {path.name!r} references unknown target entity {path.target_entity!r}")
        for entity_name in path.intermediate_entities:
            if entity_name not in view.entities:
                diagnostics.append(f"path_semantic {path.name!r} references unknown intermediate entity {entity_name!r}")
        for relationship_name in path.relationships:
            if relationship_name not in view.relationships:
                diagnostics.append(f"path_semantic {path.name!r} references unknown relationship {relationship_name!r}")
        _validate_path_connectivity(path, view, diagnostics)
        for field_id in path.default_return_fields:
            _validate_field_ref("path_semantic", path.name, field_id, view, diagnostics)

    for policy in view.return_policies.values():
        for path_id in policy.applies_to_path_semantics:
            if path_id not in view.path_semantics:
                diagnostics.append(f"return policy {policy.name!r} references unknown path semantic {path_id!r}")
        for entity_id in policy.applies_to_entities:
            if entity_id not in view.entities:
                diagnostics.append(f"return policy {policy.name!r} references unknown entity {entity_id!r}")
        for rule in policy.rules:
            for field_id in rule.get("return_fields", []):
                if isinstance(field_id, str) and "." in field_id:
                    _validate_field_ref("return policy", policy.name, field_id, view, diagnostics)

    for rule in view.disambiguation_rules:
        if not _semantic_object_exists(rule.prefer, view):
            diagnostics.append(f"disambiguation rule {rule.rule_id!r} prefers unknown semantic object {rule.prefer!r}")
        for rejected in rule.reject:
            if not _semantic_object_exists(rejected, view):
                diagnostics.append(f"disambiguation rule {rule.rule_id!r} rejects unknown semantic object {rejected!r}")

    diagnostics.extend(_synonym_collision_diagnostics(view))

    if diagnostics:
        raise GraphSemanticViewConfigError("; ".join(diagnostics))


def _validate_owner_property(
    kind: str,
    name: str,
    owner: str,
    property_name: str,
    view: GraphSemanticView,
    schema: GraphSchema,
    diagnostics: list[str],
) -> None:
    entity = view.entities.get(owner)
    if entity is None:
        diagnostics.append(f"{kind} {name!r} references unknown owner {owner!r}")
        return
    if property_name not in schema.vertex_properties.get(entity.label, set()):
        diagnostics.append(f"{kind} {name!r} references unknown property {property_name!r} on {entity.label!r}")


def _validate_relationship(
    relationship: SemanticRelationship,
    view: GraphSemanticView,
    schema: GraphSchema,
    diagnostics: list[str],
) -> None:
    from_entity = view.entities.get(relationship.from_entity)
    to_entity = view.entities.get(relationship.to_entity)
    if from_entity is None:
        diagnostics.append(f"relationship {relationship.name!r} references unknown from entity {relationship.from_entity!r}")
    if to_entity is None:
        diagnostics.append(f"relationship {relationship.name!r} references unknown to entity {relationship.to_entity!r}")
    if relationship.edge not in schema.edge_constraints:
        diagnostics.append(f"relationship {relationship.name!r} references unknown edge {relationship.edge!r}")
    if from_entity and to_entity and relationship.edge in schema.edge_constraints:
        expected = (from_entity.label, to_entity.label)
        if relationship.direction == "in":
            expected = (to_entity.label, from_entity.label)
        if expected not in schema.edge_constraints[relationship.edge]:
            diagnostics.append(
                f"relationship {relationship.name!r} edge {relationship.edge!r} does not allow "
                f"{from_entity.label!r} -> {to_entity.label!r}"
            )


def _validate_path_connectivity(path: SemanticPath, view: GraphSemanticView, diagnostics: list[str]) -> None:
    if not path.relationships:
        diagnostics.append(f"path_semantic {path.name!r} must include at least one relationship")
        return
    current = path.source_entity
    for relationship_name in path.relationships:
        relationship = view.relationships.get(relationship_name)
        if relationship is None:
            return
        if relationship.from_entity != current:
            diagnostics.append(
                f"path_semantic {path.name!r} relationship {relationship_name!r} is not connected from {current!r}"
            )
            return
        current = relationship.to_entity
    if current != path.target_entity:
        diagnostics.append(f"path_semantic {path.name!r} ends at {current!r}, not target {path.target_entity!r}")


def _validate_field_ref(kind: str, name: str, field_id: str, view: GraphSemanticView, diagnostics: list[str]) -> None:
    if field_id in view.fields:
        return
    if field_id.endswith(".*") and field_id[:-2] in view.entities:
        return
    diagnostics.append(f"{kind} {name!r} references unknown field {field_id!r}")


def _semantic_object_exists(object_id: str, view: GraphSemanticView) -> bool:
    return (
        object_id in view.entities
        or object_id in view.fields
        or object_id in view.metrics
        or object_id in view.relationships
        or object_id in view.path_semantics
    )


def _synonym_collision_diagnostics(view: GraphSemanticView) -> list[str]:
    owners: dict[str, str] = {}
    diagnostics: list[str] = []
    collections: list[tuple[str, dict[str, Any]]] = [("entity", view.entities)]
    for kind, items in collections:
        for name, item in items.items():
            for synonym in getattr(item, "synonyms", ()) + getattr(item, "trigger_phrases", ()):
                normalized = synonym.casefold().strip()
                if not normalized:
                    continue
                previous = owners.setdefault(normalized, f"{kind}:{name}")
                if previous != f"{kind}:{name}":
                    diagnostics.append(f"synonym {synonym!r} is shared by {previous} and {kind}:{name}")
    return diagnostics


def _duplicate_name_diagnostics(kind: str, items: dict[str, object]) -> list[str]:
    seen: set[str] = set()
    diagnostics: list[str] = []
    for name in items:
        if name in seen:
            diagnostics.append(f"duplicate {kind} name {name!r}")
        seen.add(name)
    return diagnostics


def _load_graph_schema(path: Path) -> GraphSchema:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise GraphSemanticViewConfigError(f"{path} must contain a JSON list")
    vertex_properties: dict[str, set[str]] = {}
    edge_properties: dict[str, set[str]] = {}
    edge_constraints: dict[str, set[tuple[str, str]]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        kind = item.get("type")
        if not isinstance(label, str) or not isinstance(kind, str):
            continue
        properties = {
            prop.get("name")
            for prop in item.get("properties", [])
            if isinstance(prop, dict) and isinstance(prop.get("name"), str)
        }
        if kind == "VERTEX":
            vertex_properties[label] = set(properties)
        if kind == "EDGE":
            edge_properties[label] = set(properties)
            constraints = set()
            for pair in item.get("constraints", []):
                if isinstance(pair, list) and len(pair) == 2 and all(isinstance(value, str) for value in pair):
                    constraints.add((pair[0], pair[1]))
            edge_constraints[label] = constraints
    return GraphSchema(
        vertex_properties=vertex_properties,
        edge_properties=edge_properties,
        edge_constraints=edge_constraints,
    )


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise GraphSemanticViewConfigError(f"{path} must contain a YAML mapping")
    return payload


def _required_mapping(item: dict[str, Any], key: str) -> dict[str, Any]:
    value = item.get(key)
    if not isinstance(value, dict):
        raise GraphSemanticViewConfigError(f"{key} must be a mapping")
    return value


def _required_list(item: dict[str, Any], key: str) -> list[Any]:
    value = item.get(key)
    if not isinstance(value, list):
        raise GraphSemanticViewConfigError(f"{key} must be a list")
    return value


def _required_str(item: dict[str, Any], key: str, section: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GraphSemanticViewConfigError(f"{section}.{key} must be a non-empty string")
    return value


def _optional_str(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise GraphSemanticViewConfigError(f"{field} must be a non-empty string when provided")
    return value


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise GraphSemanticViewConfigError(f"{field} must be a list of non-empty strings")
    return tuple(value)


def _direction(value: str) -> Direction:
    if value not in {"out", "in", "undirected"}:
        raise GraphSemanticViewConfigError("relationships.direction must be one of: out, in, undirected")
    return value  # type: ignore[return-value]


def _is_mapping(item: Any, section: str) -> bool:
    if not isinstance(item, dict):
        raise GraphSemanticViewConfigError(f"{section} entries must be mappings")
    return True


def _default_schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "testing_agent" / "docs" / "reference" / "schema.json"


def semantic_object_source(object_id: str, view: GraphSemanticView) -> str:
    if object_id in view.entities:
        return f"entities.{object_id}"
    if object_id in view.dimensions:
        return f"dimensions.{object_id}"
    if object_id in view.facts:
        return f"facts.{object_id}"
    if object_id in view.metrics:
        return f"metrics.{object_id}"
    if object_id in view.relationships:
        return f"relationships.{object_id}"
    if object_id in view.path_semantics:
        return f"path_semantics.{object_id}"
    return object_id


def normalize_question(question: str) -> str:
    return re.sub(r"\s+", "", question)
