from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal


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
