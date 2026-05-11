from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


SemanticQueryKind = Literal[
    "record_selection",
    "metric_aggregation",
    "dimension_breakdown",
    "ranking",
    "existence_check",
]

Direction = Literal["out", "in", "undirected"]
SortDirection = Literal["ASC", "DESC"]


@dataclass(frozen=True)
class SemanticEntityRef:
    name: str
    label: str
    alias: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticRelationshipRef:
    name: str
    from_entity: str
    to_entity: str
    edge: str
    direction: Direction = "out"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticFieldRef:
    name: str
    entity: str
    alias: str
    property: str
    output_alias: str

    @property
    def expression(self) -> str:
        if self.property == "*":
            return self.alias
        return f"{self.alias}.{self.property}"

    def to_dict(self) -> dict[str, str]:
        payload = asdict(self)
        payload["expression"] = self.expression
        return payload


@dataclass(frozen=True)
class SemanticMetricRef:
    name: str
    entity: str
    alias: str
    aggregation: str
    expression: str
    output_alias: str
    property: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticFilterRef:
    entity: str
    alias: str
    property: str
    operator: str
    value: str | int | float | bool

    @property
    def left(self) -> str:
        return f"{self.alias}.{self.property}"

    def to_dict(self) -> dict[str, str | int | float | bool]:
        payload = asdict(self)
        payload["left"] = self.left
        return payload


@dataclass(frozen=True)
class SemanticOrderBy:
    expression: str
    direction: SortDirection = "ASC"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticWithStage:
    carry_aliases: tuple[str, ...]
    metric: SemanticMetricRef
    output_alias: str

    def to_dict(self) -> dict[str, object]:
        return {
            "carry_aliases": list(self.carry_aliases),
            "metric": self.metric.to_dict(),
            "output_alias": self.output_alias,
        }


@dataclass(frozen=True)
class SemanticQuerySpec:
    kind: SemanticQueryKind
    entities: tuple[SemanticEntityRef, ...]
    relationships: tuple[SemanticRelationshipRef, ...] = ()
    projections: tuple[SemanticFieldRef, ...] = ()
    dimensions: tuple[SemanticFieldRef, ...] = ()
    metrics: tuple[SemanticMetricRef, ...] = ()
    filters: tuple[SemanticFilterRef, ...] = ()
    order_by: tuple[SemanticOrderBy, ...] = ()
    with_stage: SemanticWithStage | None = None
    limit: int | None = None
    output_alias: str | None = None
    intent: str | None = None
    schema_id: str | None = None
    scenario_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "intent": self.intent,
            "schema_id": self.schema_id,
            "scenario_id": self.scenario_id,
            "entities": [entity.to_dict() for entity in self.entities],
            "relationships": [relationship.to_dict() for relationship in self.relationships],
            "projections": [projection.to_dict() for projection in self.projections],
            "dimensions": [dimension.to_dict() for dimension in self.dimensions],
            "metrics": [metric.to_dict() for metric in self.metrics],
            "filters": [filter_ref.to_dict() for filter_ref in self.filters],
            "order_by": [order.to_dict() for order in self.order_by],
            "with_stage": self.with_stage.to_dict() if self.with_stage is not None else None,
            "limit": self.limit,
            "output_alias": self.output_alias,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class SemanticQueryBuilder:
    def build(
        self,
        *,
        intent_result: Any,
        linked_semantics: Any,
        business_slots: Any | None = None,
    ) -> SemanticQuerySpec:
        primary_intent = getattr(intent_result, "primary_intent", None)
        secondary_intent = getattr(intent_result, "secondary_intent", None)
        if primary_intent == "record_retrieval_query":
            return self._record_selection(intent_result, linked_semantics, business_slots)
        if primary_intent == "metric_query":
            return self._metric_aggregation(intent_result, linked_semantics, business_slots)
        if primary_intent == "breakdown_query":
            return self._dimension_breakdown(intent_result, linked_semantics, business_slots)
        if primary_intent == "ranking_query":
            return self._ranking(intent_result, linked_semantics, business_slots)
        if primary_intent == "existence_query":
            return self._existence_check(intent_result, linked_semantics, business_slots)
        raise ValueError(f"unsupported intent for deterministic semantic query: {primary_intent}.{secondary_intent}")

    def _record_selection(self, intent_result: Any, linked: Any, business_slots: Any | None) -> SemanticQuerySpec:
        projections = list(_fields(getattr(linked, "return_fields", []), linked))
        if not projections:
            projections = _default_record_projections(intent_result, linked)
        return SemanticQuerySpec(
            kind="record_selection",
            intent=_intent_name(intent_result),
            schema_id=_business_value(business_slots, "schema_id"),
            scenario_id=_business_value(business_slots, "scenario_id"),
            entities=tuple(_entities(linked)),
            relationships=tuple(_relationships(linked)),
            projections=tuple(projections),
            filters=tuple(_filters(linked)),
        )

    def _metric_aggregation(self, intent_result: Any, linked: Any, business_slots: Any | None) -> SemanticQuerySpec:
        entity = _metric_entity(linked)
        metric = _first(getattr(linked, "metrics", []))
        if metric is None:
            metric_ref = _count_metric(entity, f"{entity.semantic_name}_count")
        else:
            metric_ref = _metric_ref(metric, linked, fallback_entity=entity)
        return SemanticQuerySpec(
            kind="metric_aggregation",
            intent=_intent_name(intent_result),
            schema_id=_business_value(business_slots, "schema_id"),
            scenario_id=_business_value(business_slots, "scenario_id"),
            entities=tuple(_entities(linked)),
            metrics=(metric_ref,),
            filters=tuple(_filters(linked)),
        )

    def _dimension_breakdown(self, intent_result: Any, linked: Any, business_slots: Any | None) -> SemanticQuerySpec:
        entity = _metric_entity(linked)
        dimension = _first(getattr(linked, "group_by", [])) or _first(getattr(linked, "return_fields", []))
        if dimension is None:
            raise ValueError("dimension breakdown requires a linked dimension")
        metric = _first(getattr(linked, "metrics", []))
        metric_ref = _metric_ref(metric, linked, fallback_entity=entity) if metric is not None else _count_metric(entity, f"{entity.semantic_name}_count")
        return SemanticQuerySpec(
            kind="dimension_breakdown",
            intent=_intent_name(intent_result),
            schema_id=_business_value(business_slots, "schema_id"),
            scenario_id=_business_value(business_slots, "scenario_id"),
            entities=tuple(_entities(linked)),
            dimensions=(_field_ref(dimension, linked),),
            metrics=(metric_ref,),
            filters=tuple(_filters(linked)),
            order_by=(SemanticOrderBy(expression=metric_ref.output_alias, direction="DESC"),),
        )

    def _ranking(self, intent_result: Any, linked: Any, business_slots: Any | None) -> SemanticQuerySpec:
        entity = _metric_entity(linked)
        projections = list(_fields(getattr(linked, "return_fields", []), linked))
        if not projections:
            projections.append(
                SemanticFieldRef(
                    name=f"{entity.semantic_name}_name",
                    entity=entity.semantic_name,
                    alias=entity.alias,
                    property="name",
                    output_alias=f"{entity.semantic_name}_name",
                )
            )
        order = _first(getattr(linked, "order_by", []))
        if order is None and projections:
            order_by = SemanticOrderBy(expression=projections[-1].expression, direction="DESC")
        else:
            order_by = SemanticOrderBy(expression=order.expression, direction=_sort_direction(order.direction))
            projections = _ensure_order_projection(linked, projections, order.expression)
        return SemanticQuerySpec(
            kind="ranking",
            intent=_intent_name(intent_result),
            schema_id=_business_value(business_slots, "schema_id"),
            scenario_id=_business_value(business_slots, "scenario_id"),
            entities=tuple(_entities(linked)),
            projections=tuple(projections),
            filters=tuple(_filters(linked)),
            order_by=(order_by,),
            limit=getattr(linked, "limit", None) or 5,
        )

    def _existence_check(self, intent_result: Any, linked: Any, business_slots: Any | None) -> SemanticQuerySpec:
        return SemanticQuerySpec(
            kind="existence_check",
            intent=_intent_name(intent_result),
            schema_id=_business_value(business_slots, "schema_id"),
            scenario_id=_business_value(business_slots, "scenario_id"),
            entities=tuple(_entities(linked)),
            relationships=tuple(_relationships(linked)),
            filters=tuple(_filters(linked)),
            output_alias="exists",
        )


def _entities(linked: Any) -> list[SemanticEntityRef]:
    return [
        SemanticEntityRef(name=entity.semantic_name, label=entity.label, alias=entity.alias)
        for entity in getattr(linked, "entities", [])
    ]


def _relationships(linked: Any) -> list[SemanticRelationshipRef]:
    return [
        SemanticRelationshipRef(
            name=relationship.semantic_name,
            from_entity=relationship.from_entity,
            to_entity=relationship.to_entity,
            edge=relationship.edge,
            direction=relationship.direction,
        )
        for relationship in getattr(linked, "relationships", [])
    ]


def _filters(linked: Any) -> list[SemanticFilterRef]:
    filters: list[SemanticFilterRef] = []
    for filter_value in getattr(linked, "filters", []):
        entity = _entity(linked, filter_value.owner)
        filters.append(
            SemanticFilterRef(
                entity=entity.semantic_name,
                alias=entity.alias,
                property=filter_value.property,
                operator=filter_value.operator,
                value=filter_value.value,
            )
        )
    return filters


def _fields(fields: Any, linked: Any) -> list[SemanticFieldRef]:
    return [_field_ref(field_value, linked) for field_value in fields]


def _default_record_projections(intent_result: Any, linked: Any) -> list[SemanticFieldRef]:
    entities = list(getattr(linked, "entities", []))
    if not entities:
        return []
    secondary_intent = getattr(intent_result, "secondary_intent", None)
    selected_entities = entities if secondary_intent == "related_record_query" else [entities[-1]]
    projections: list[SemanticFieldRef] = []
    for entity in selected_entities:
        for property_name in ("id", "name"):
            projections.append(
                SemanticFieldRef(
                    name=f"{entity.semantic_name}_{property_name}",
                    entity=entity.semantic_name,
                    alias=entity.alias,
                    property=property_name,
                    output_alias=f"{entity.semantic_name}_{property_name}",
                )
            )
    return projections


def _field_ref(field_value: Any, linked: Any) -> SemanticFieldRef:
    entity = _entity(linked, field_value.owner)
    return SemanticFieldRef(
        name=field_value.semantic_name,
        entity=entity.semantic_name,
        alias=entity.alias,
        property=field_value.property,
        output_alias=field_value.alias,
    )


def _metric_ref(metric: Any, linked: Any, *, fallback_entity: Any) -> SemanticMetricRef:
    owner = getattr(metric, "owner", None)
    entity = _entity(linked, owner) if owner else fallback_entity
    aggregation, property_name = _metric_parts(metric)
    return SemanticMetricRef(
        name=metric.semantic_name,
        entity=entity.semantic_name,
        alias=entity.alias,
        aggregation=aggregation,
        property=property_name,
        expression=metric.expression,
        output_alias=metric.alias,
    )


def _metric_parts(metric: Any) -> tuple[str, str | None]:
    expression = getattr(metric, "expression", "")
    if "(" not in expression or ")" not in expression:
        return "count", None
    function = expression.split("(", 1)[0].strip()
    inner = expression.split("(", 1)[1].rsplit(")", 1)[0].strip()
    property_name = inner.split(".", 1)[1] if "." in inner else None
    return function, property_name


def _count_metric(entity: Any, output_alias: str) -> SemanticMetricRef:
    return SemanticMetricRef(
        name=output_alias,
        entity=entity.semantic_name,
        alias=entity.alias,
        aggregation="count",
        property=None,
        expression=f"count({entity.alias})",
        output_alias=output_alias,
    )


def _metric_entity(linked: Any) -> Any:
    metric = _first(getattr(linked, "metrics", []))
    if metric is not None and getattr(metric, "owner", None):
        return _entity(linked, metric.owner)
    field_value = _first(getattr(linked, "return_fields", [])) or _first(getattr(linked, "group_by", []))
    if field_value is not None:
        return _entity(linked, field_value.owner)
    entities = list(getattr(linked, "entities", []))
    if entities:
        return entities[-1]
    raise ValueError("semantic query requires at least one linked entity")


def _entity(linked: Any, semantic_name: str) -> Any:
    for entity in getattr(linked, "entities", []):
        if entity.semantic_name == semantic_name:
            return entity
    raise ValueError(f"missing linked entity: {semantic_name}")


def _ensure_order_projection(linked: Any, projections: list[SemanticFieldRef], expression: str) -> list[SemanticFieldRef]:
    if "." not in expression:
        return projections
    alias, property_name = expression.split(".", 1)
    if any(projection.alias == alias and projection.property == property_name for projection in projections):
        return projections
    for entity in getattr(linked, "entities", []):
        if entity.alias == alias:
            return [
                *projections,
                SemanticFieldRef(
                    name=f"{entity.semantic_name}_{property_name}",
                    entity=entity.semantic_name,
                    alias=alias,
                    property=property_name,
                    output_alias=f"{entity.semantic_name}_{property_name}",
                ),
            ]
    return projections


def _sort_direction(value: str) -> SortDirection:
    return "ASC" if str(value).upper() == "ASC" else "DESC"


def _intent_name(intent_result: Any) -> str:
    primary = getattr(intent_result, "primary_intent", None)
    secondary = getattr(intent_result, "secondary_intent", None)
    if primary and secondary:
        return f"{primary}.{secondary}"
    return str(primary or secondary or "")


def _business_value(business_slots: Any | None, name: str) -> str | None:
    if business_slots is None:
        return None
    value = getattr(business_slots, name, None)
    return str(value) if value is not None else None


def _first(values: Any) -> Any | None:
    values = list(values or [])
    return values[0] if values else None
