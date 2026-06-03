from __future__ import annotations

from dataclasses import dataclass, field

from .models import OperationType, QueryShape


@dataclass(frozen=True)
class PropertyReference:
    owner: str
    name: str


@dataclass(frozen=True)
class RoleReference:
    alias: str
    vertex_name: str


@dataclass(frozen=True)
class EdgeReference:
    alias: str
    edge_name: str


@dataclass(frozen=True)
class SourceReference:
    raw: str
    namespace: str
    name: str

    @classmethod
    def from_text(cls, source: str) -> "SourceReference":
        namespace, separator, name = source.partition(".")
        if not separator:
            return cls(raw=source, namespace="", name=source)
        return cls(raw=source, namespace=namespace, name=name)


@dataclass(frozen=True)
class ValueLiteral:
    raw: object | None
    normalized: object | None
    resolver_match_type: str | None = None

    @property
    def effective_value(self) -> object | None:
        return self.normalized if self.normalized is not None else self.raw


@dataclass(frozen=True)
class Filter:
    target: RoleReference | None
    property: PropertyReference
    operator: str
    value: ValueLiteral


@dataclass(frozen=True)
class Dimension:
    alias: str
    target: RoleReference
    property: PropertyReference


@dataclass(frozen=True)
class MetricDimension:
    alias: str
    target_alias: str
    target_owner: str
    property: PropertyReference


@dataclass(frozen=True)
class Measure:
    alias: str
    function: str
    target: RoleReference
    property: PropertyReference


@dataclass(frozen=True)
class Predicate:
    property: str
    operator: str
    value: ValueLiteral


@dataclass(frozen=True)
class ProjectionItem:
    alias: str | None
    target: RoleReference | None = None
    property: PropertyReference | None = None
    source: SourceReference | None = None
    vertex_full: bool = False


@dataclass(frozen=True)
class Projection:
    items: list[ProjectionItem]


@dataclass(frozen=True)
class SortItem:
    source: SourceReference
    direction: str


@dataclass(frozen=True)
class TraverseEdgeOperation:
    op: OperationType
    from_role: RoleReference
    edge_role: EdgeReference
    to_role: RoleReference
    direction: str


@dataclass(frozen=True)
class VariablePathOperation:
    op: OperationType
    bind_as: str
    start: RoleReference
    through: RoleReference
    through_filters: list[Filter]
    allowed_edges: list[str]
    min_hops: int
    max_hops: int


@dataclass(frozen=True)
class UsePathPatternOperation:
    op: OperationType
    path_pattern_name: str
    bind_as: str
    parameters: dict[str, ValueLiteral]


@dataclass(frozen=True)
class MetricAggregateOperation:
    op: OperationType
    metric_name: str
    group_by: list[MetricDimension]
    filters: list[Filter]


@dataclass(frozen=True)
class AggregateOperation:
    op: OperationType
    group_by: list[Dimension]
    measures: list[Measure]


@dataclass(frozen=True)
class SortOperation:
    op: OperationType
    by: list[SortItem]


@dataclass(frozen=True)
class LimitOperation:
    op: OperationType
    value: int


@dataclass(frozen=True)
class SubqueryOperation:
    op: OperationType
    bind_as: str
    query_shape: QueryShape
    group_by: list[Dimension]
    measures: list[Measure]
    carry_roles: list[RoleReference] = field(default_factory=list)
    operations: list[TraverseEdgeOperation] = field(default_factory=list)


@dataclass(frozen=True)
class FilterSubqueryOperation:
    op: OperationType
    source: str
    predicate: Predicate


RestrictedOperation = (
    TraverseEdgeOperation
    | VariablePathOperation
    | UsePathPatternOperation
    | MetricAggregateOperation
    | AggregateOperation
    | SortOperation
    | LimitOperation
    | SubqueryOperation
    | FilterSubqueryOperation
)


@dataclass(frozen=True)
class RestrictedQueryAst:
    schema_version: str
    query_id: str
    query_shape: QueryShape
    source_question: str
    operations: list[RestrictedOperation]
    projection: Projection
    filters: list[Filter] = field(default_factory=list)
    sort: list[SortItem] = field(default_factory=list)
    limit: int | None = None
