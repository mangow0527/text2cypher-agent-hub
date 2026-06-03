from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator


class QueryShape(str, Enum):
    VERTEX_LOOKUP = "vertex_lookup"
    SINGLE_HOP_TRAVERSAL = "single_hop_traversal"
    VARIABLE_PATH_TRAVERSAL = "variable_path_traversal"
    NAMED_PATH_PATTERN = "named_path_pattern"
    METRIC_AGGREGATE = "metric_aggregate"
    AD_HOC_AGGREGATE = "ad_hoc_aggregate"
    TOP_N = "top_n"
    TWO_STEP_AGGREGATE = "two_step_aggregate"


class OperationType(str, Enum):
    TRAVERSE_EDGE = "traverse_edge"
    VARIABLE_PATH = "variable_path"
    USE_PATH_PATTERN = "use_path_pattern"
    METRIC_AGGREGATE = "metric_aggregate"
    AGGREGATE = "aggregate"
    SORT = "sort"
    LIMIT = "limit"
    SUBQUERY = "subquery"
    FILTER_SUBQUERY = "filter_subquery"


Operator = Literal["eq", "neq", "gt", "gte", "lt", "lte", "in", "contains", "is_not_null"]


class RestrictedDslBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ValueModel(RestrictedDslBase):
    raw: Any | None = None
    normalized: Any | None = None
    resolver_match_type: str | None = None


class PropertyReferenceModel(RestrictedDslBase):
    owner: str
    name: str


class BindingModel(RestrictedDslBase):
    vertex_name: str | None = None
    edge_name: str | None = None
    metric_name: str | None = None
    property: PropertyReferenceModel | None = None


class FilterModel(RestrictedDslBase):
    target: str | None = None
    property: PropertyReferenceModel
    operator: Operator
    value: Any


class ProjectionItemModel(RestrictedDslBase):
    alias: str | None = None
    target: str | None = None
    property: PropertyReferenceModel | None = None
    source: str | None = None
    vertex_full: bool = False
    projection_terms: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_reference_shape(self) -> "ProjectionItemModel":
        has_target_property = self.target is not None and self.property is not None
        has_source = self.source is not None
        has_vertex_full = self.vertex_full and self.target is not None and self.property is None
        if sum(bool(item) for item in (has_target_property, has_source, has_vertex_full)) != 1:
            raise ValueError("projection item must use target/property, source, or target+vertex_full")
        return self


class ProjectionModel(RestrictedDslBase):
    items: list[ProjectionItemModel]


class SortItemModel(RestrictedDslBase):
    source: str
    direction: Literal["asc", "desc"]


class DimensionModel(RestrictedDslBase):
    alias: str
    target: str
    property: PropertyReferenceModel


class MeasureModel(RestrictedDslBase):
    alias: str
    function: Literal["count", "sum", "avg", "min", "max"]
    target: str
    property: PropertyReferenceModel


class PredicateModel(RestrictedDslBase):
    property: str
    operator: Operator
    value: Any


class TraverseEdgeOperationModel(RestrictedDslBase):
    op: Literal["traverse_edge"]
    from_ref: str = Field(alias="from")
    edge: str
    to: str
    direction: Literal["forward", "backward"]


class VariablePathThroughModel(RestrictedDslBase):
    vertex_ref: str
    filters: list[FilterModel] = Field(default_factory=list)


class VariablePathOperationModel(RestrictedDslBase):
    op: Literal["variable_path"]
    bind_as: str
    start: str
    through: VariablePathThroughModel
    allowed_edges: list[str] = Field(min_length=1)
    min_hops: int = Field(default=1, ge=0)
    max_hops: PositiveInt = Field(le=8)

    @model_validator(mode="after")
    def validate_hop_range(self) -> "VariablePathOperationModel":
        if self.min_hops > self.max_hops:
            raise ValueError("min_hops must be less than or equal to max_hops")
        return self


class UsePathPatternOperationModel(RestrictedDslBase):
    op: Literal["use_path_pattern"]
    path_pattern_name: str
    bind_as: str
    parameters: dict[str, ValueModel] = Field(default_factory=dict)


class MetricAggregateOperationModel(RestrictedDslBase):
    op: Literal["metric_aggregate"]
    metric_name: str
    group_by: list[DimensionModel] = Field(default_factory=list)
    filters: list[FilterModel] = Field(default_factory=list)


class AggregateOperationModel(RestrictedDslBase):
    op: Literal["aggregate"]
    group_by: list[DimensionModel] = Field(default_factory=list)
    measures: list[MeasureModel] = Field(min_length=1)


class SortOperationModel(RestrictedDslBase):
    op: Literal["sort"]
    by: list[SortItemModel] = Field(min_length=1)


class LimitOperationModel(RestrictedDslBase):
    op: Literal["limit"]
    value: PositiveInt


class SubqueryOperationModel(RestrictedDslBase):
    op: Literal["subquery"]
    bind_as: str
    query_shape: QueryShape
    carry_roles: list[str] = Field(default_factory=list)
    group_by: list[DimensionModel] = Field(default_factory=list)
    measures: list[MeasureModel] = Field(default_factory=list)
    operations: list[dict[str, Any]] = Field(default_factory=list)


class FilterSubqueryOperationModel(RestrictedDslBase):
    op: Literal["filter_subquery"]
    source: str
    predicate: PredicateModel


OperationModel = Annotated[
    TraverseEdgeOperationModel
    | VariablePathOperationModel
    | UsePathPatternOperationModel
    | MetricAggregateOperationModel
    | AggregateOperationModel
    | SortOperationModel
    | LimitOperationModel
    | SubqueryOperationModel
    | FilterSubqueryOperationModel,
    Field(discriminator="op"),
]


class RestrictedQueryDslModel(RestrictedDslBase):
    schema_version: Literal["restricted_query_dsl_v1"]
    query_id: str
    query_shape: QueryShape
    source_question: str
    bindings: dict[str, BindingModel]
    operations: list[OperationModel]
    projection: ProjectionModel
    filters: list[FilterModel] = Field(default_factory=list)
    order_by: list[SortItemModel] = Field(default_factory=list)
    limit: PositiveInt | None = None
    assumptions: list[Any] = Field(default_factory=list)
