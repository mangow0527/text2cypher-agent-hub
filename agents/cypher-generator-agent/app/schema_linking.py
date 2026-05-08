from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class LinkedEntity:
    semantic_name: str
    label: str
    alias: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class LinkedRelationship:
    semantic_name: str
    from_entity: str
    to_entity: str
    edge: str
    direction: str = "out"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class LinkedProperty:
    semantic_name: str
    owner: str
    property: str
    alias: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class LinkedFilter:
    owner: str
    property: str
    operator: str
    value: str | int | float | bool

    def to_dict(self) -> dict[str, str | int | float | bool]:
        return asdict(self)


@dataclass(frozen=True)
class LinkedMetric:
    semantic_name: str
    expression: str
    alias: str
    owner: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True)
class LinkedOrderBy:
    expression: str
    direction: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class LinkedSemantics:
    entities: list[LinkedEntity] = field(default_factory=list)
    relationships: list[LinkedRelationship] = field(default_factory=list)
    return_fields: list[LinkedProperty] = field(default_factory=list)
    filters: list[LinkedFilter] = field(default_factory=list)
    metrics: list[LinkedMetric] = field(default_factory=list)
    group_by: list[LinkedProperty] = field(default_factory=list)
    order_by: list[LinkedOrderBy] = field(default_factory=list)
    limit: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "entities": [entity.to_dict() for entity in self.entities],
            "relationships": [relationship.to_dict() for relationship in self.relationships],
            "return_fields": [field_value.to_dict() for field_value in self.return_fields],
            "filters": [filter_value.to_dict() for filter_value in self.filters],
            "metrics": [metric.to_dict() for metric in self.metrics],
            "group_by": [field_value.to_dict() for field_value in self.group_by],
            "order_by": [order.to_dict() for order in self.order_by],
            "limit": self.limit,
        }


class SchemaLinker:
    def __init__(self, semantic_layer: Any) -> None:
        self.semantic_layer = semantic_layer

    def link(self, slots: Any) -> LinkedSemantics:
        entities = self._link_entities(_slot_list(slots, "entities"))
        relationships = self._link_relationships(_slot_list(slots, "relationships"), entities)
        return_fields = self._link_properties(_slot_list(slots, "return_fields"), entities)
        filters = self._link_filters(_slot_list(slots, "filters"), entities)
        metrics = self._link_metrics(_slot_list(slots, "metrics"))
        group_by = self._link_properties(_slot_list(slots, "group_by"), entities)
        order_by = self._link_order_by(_slot_list(slots, "order_by"), entities)
        raw_limit = getattr(slots, "limit", None)
        limit = getattr(raw_limit, "value", raw_limit)
        return LinkedSemantics(
            entities=entities,
            relationships=relationships,
            return_fields=return_fields,
            filters=filters,
            metrics=metrics,
            group_by=group_by,
            order_by=order_by,
            limit=limit,
        )

    def _link_entities(self, slots: list[Any]) -> list[LinkedEntity]:
        linked: list[LinkedEntity] = []
        used_aliases: set[str] = set()
        for slot in slots:
            name = _slot_name(slot)
            entity = _lookup(self.semantic_layer, "entity", name)
            if entity is None:
                continue
            semantic_name = _value(entity, "semantic_name", "name", default=name)
            alias = _value(entity, "alias", default=_default_alias(semantic_name, used_aliases))
            used_aliases.add(alias)
            linked.append(
                LinkedEntity(
                    semantic_name=semantic_name,
                    label=_value(entity, "label", default=semantic_name),
                    alias=alias,
                )
            )
        return _dedupe_entities(linked)

    def _link_relationships(self, slots: list[Any], entities: list[LinkedEntity]) -> list[LinkedRelationship]:
        linked: list[LinkedRelationship] = []
        entity_names = {entity.semantic_name for entity in entities}
        for slot in slots:
            name = _slot_name(slot)
            relationship = _lookup(self.semantic_layer, "relationship", name)
            if relationship is None:
                inferred = self._infer_relationship(entity_names)
                if inferred is None:
                    continue
                relationship = inferred
            from_entity = _value(relationship, "from_entity", "from", default="")
            to_entity = _value(relationship, "to_entity", "to", default="")
            linked.append(
                LinkedRelationship(
                    semantic_name=_value(relationship, "semantic_name", "name", default=name),
                    from_entity=from_entity,
                    to_entity=to_entity,
                    edge=_value(relationship, "edge", default=""),
                    direction=_value(relationship, "direction", default="out"),
                )
            )
        if not linked:
            inferred = self._infer_relationship(entity_names)
            if inferred is not None:
                linked.append(
                    LinkedRelationship(
                        semantic_name=_value(inferred, "semantic_name", "name", default=""),
                        from_entity=_value(inferred, "from_entity", "from", default=""),
                        to_entity=_value(inferred, "to_entity", "to", default=""),
                        edge=_value(inferred, "edge", default=""),
                        direction=_value(inferred, "direction", default="out"),
                    )
                )
        return linked

    def _link_properties(self, slots: list[Any], entities: list[LinkedEntity]) -> list[LinkedProperty]:
        linked: list[LinkedProperty] = []
        for slot in slots:
            name = _slot_name(slot)
            owner_hint = _value(slot, "owner", "entity", default=None)
            prop = self._lookup_property(name, owner_hint, entities)
            if prop is None:
                continue
            owner = _value(prop, "owner", "entity", default=owner_hint or "")
            property_name = _value(prop, "property", "physical", default=name)
            semantic_name = _value(prop, "semantic_name", "name", default=f"{owner}_{property_name}")
            linked.append(
                LinkedProperty(
                    semantic_name=semantic_name,
                    owner=owner,
                    property=property_name,
                    alias=_value(prop, "alias", default=semantic_name),
                )
            )
        return _dedupe_properties(linked)

    def _link_filters(self, slots: list[Any], entities: list[LinkedEntity]) -> list[LinkedFilter]:
        linked: list[LinkedFilter] = []
        for slot in slots:
            property_name = _value(slot, "property", "candidate", default="")
            owner_hint = _value(slot, "owner", "entity", default=None)
            prop = self._lookup_property(property_name, owner_hint, entities)
            if prop is None:
                continue
            linked.append(
                LinkedFilter(
                    owner=_value(prop, "owner", "entity", default=owner_hint or ""),
                    property=_value(prop, "property", "physical", default=property_name),
                    operator=_value(slot, "operator", default="="),
                    value=_value(slot, "value", default=""),
                )
            )
        return linked

    def _link_metrics(self, slots: list[Any]) -> list[LinkedMetric]:
        linked: list[LinkedMetric] = []
        for slot in slots:
            name = _slot_name(slot)
            metric = _lookup(self.semantic_layer, "metric", name)
            if metric is None:
                metric = self._lookup_metric_by_parts(slot)
            if metric is None:
                continue
            semantic_name = _value(metric, "semantic_name", "name", default=name)
            linked.append(
                LinkedMetric(
                    semantic_name=semantic_name,
                    expression=_value(metric, "expression", "expr", default=""),
                    alias=_value(metric, "alias", default=semantic_name),
                    owner=_value(metric, "owner", "base_entity", default=None),
                )
            )
        return linked

    def _lookup_metric_by_parts(self, slot: Any) -> Any | None:
        entity = _value(slot, "entity", "owner", default="")
        aggregation = _value(slot, "aggregation", default="")
        property_name = _value(slot, "property", default=None)
        for metric in _collection(self.semantic_layer, "metrics"):
            if _value(metric, "owner", "base_entity", default="") != entity:
                continue
            if _value(metric, "aggregation", default="") != aggregation:
                continue
            metric_property = _value(metric, "property", default=None)
            if property_name is None or metric_property == property_name:
                return metric
        return None

    def _link_order_by(self, slots: list[Any], entities: list[LinkedEntity]) -> list[LinkedOrderBy]:
        linked: list[LinkedOrderBy] = []
        for slot in slots:
            property_name = _value(slot, "property", "candidate", default="")
            direction = _value(slot, "direction", default="DESC").upper()
            prop = self._lookup_property(property_name, _value(slot, "owner", "entity", default=None), entities)
            if prop is None:
                metric = _lookup(self.semantic_layer, "metric", property_name)
                if metric is not None:
                    linked.append(
                        LinkedOrderBy(
                            expression=_value(metric, "alias", "semantic_name", "name", default=property_name),
                            direction=direction,
                        )
                    )
                continue
            owner_alias = self._entity_alias(_value(prop, "owner", "entity", default=""), entities)
            linked.append(
                LinkedOrderBy(
                    expression=f"{owner_alias}.{_value(prop, 'property', 'physical', default=property_name)}",
                    direction=direction,
                )
            )
        return linked

    def _lookup_property(
        self,
        name: str,
        owner_hint: str | None,
        entities: list[LinkedEntity],
    ) -> Any | None:
        if owner_hint:
            prop = _lookup(self.semantic_layer, "property", f"{owner_hint}.{name}")
            if prop is not None:
                return prop
            prop = _lookup(self.semantic_layer, "property", f"{owner_hint}_{name}")
            if prop is not None:
                return prop
            prop = self._lookup_property_by_owner_and_physical_name(owner_hint, name)
            if prop is not None:
                return prop
        for entity in reversed(entities):
            prop = _lookup(self.semantic_layer, "property", f"{entity.semantic_name}.{name}")
            if prop is not None:
                return prop
            prop = _lookup(self.semantic_layer, "property", f"{entity.semantic_name}_{name}")
            if prop is not None:
                return prop
            prop = self._lookup_property_by_owner_and_physical_name(entity.semantic_name, name)
            if prop is not None:
                return prop
        return _lookup(self.semantic_layer, "property", name)

    def _lookup_property_by_owner_and_physical_name(self, owner: str, property_name: str) -> Any | None:
        for prop in _collection(self.semantic_layer, "properties"):
            if _value(prop, "owner", "entity", default="") == owner and _value(prop, "property", "physical", default="") == property_name:
                return prop
        return None

    def _infer_relationship(self, entity_names: set[str]) -> Any | None:
        relationships = _collection(self.semantic_layer, "relationships")
        for relationship in relationships:
            from_entity = _value(relationship, "from_entity", "from", default="")
            to_entity = _value(relationship, "to_entity", "to", default="")
            if from_entity in entity_names and to_entity in entity_names:
                return relationship
        return None

    def _entity_alias(self, semantic_name: str, entities: list[LinkedEntity]) -> str:
        for entity in entities:
            if entity.semantic_name == semantic_name:
                return entity.alias
        return semantic_name[:1] or "n"


def _slot_list(slots: Any, name: str) -> list[Any]:
    value = getattr(slots, name, [])
    if value is None:
        return []
    return list(value)


def _slot_name(slot: Any) -> str:
    return str(_value(slot, "semantic_name", "candidate", "normalized", "name", "text", default=""))


def _lookup(layer: Any, kind: str, name: str) -> Any | None:
    method = getattr(layer, f"get_{kind}", None)
    if callable(method):
        return method(name)
    collection = _collection(layer, _plural(kind))
    for item in collection:
        names = {
            _value(item, "semantic_name", "name", default=""),
            _value(item, "key", default=""),
        }
        if name in names:
            return item
    return None


def _collection(layer: Any, name: str) -> list[Any]:
    value = getattr(layer, name, [])
    if isinstance(value, dict):
        return list(value.values())
    return list(value or [])


def _plural(kind: str) -> str:
    return {
        "entity": "entities",
        "property": "properties",
        "relationship": "relationships",
        "metric": "metrics",
    }.get(kind, f"{kind}s")


def _value(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _default_alias(name: str, used_aliases: set[str]) -> str:
    preferred = {
        "service": "s",
        "tunnel": "t",
        "network_element": "ne",
        "port": "p",
        "link": "l",
        "fiber": "f",
        "protocol": "proto",
    }.get(name, name[:1] or "n")
    alias = preferred
    index = 2
    while alias in used_aliases:
        alias = f"{preferred}{index}"
        index += 1
    return alias


def _dedupe_entities(entities: list[LinkedEntity]) -> list[LinkedEntity]:
    seen: set[str] = set()
    result: list[LinkedEntity] = []
    for entity in entities:
        if entity.semantic_name in seen:
            continue
        seen.add(entity.semantic_name)
        result.append(entity)
    return result


def _dedupe_properties(properties: list[LinkedProperty]) -> list[LinkedProperty]:
    seen: set[tuple[str, str]] = set()
    result: list[LinkedProperty] = []
    for prop in properties:
        key = (prop.owner, prop.property)
        if key in seen:
            continue
        seen.add(key)
        result.append(prop)
    return result
