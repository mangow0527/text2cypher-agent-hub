from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from services.cypher_generator_agent.app.literals.models import (
    LiteralAlternative,
    LiteralEvidence,
)


BINDING_PLAN_SCHEMA_VERSION = "binding_plan_v1"


class BindingBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateBinding(BindingBase):
    semantic_type: str
    semantic_id: str
    semantic_name: str
    score: float = Field(ge=0.0, le=1.0)
    match_type: str
    owner: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VertexBinding(BindingBase):
    name: str
    candidate: CandidateBinding


class EdgeBinding(BindingBase):
    name: str
    candidate: CandidateBinding
    direction: Literal["forward", "backward"] = "forward"


class PropertyBinding(BindingBase):
    owner: str
    name: str
    candidate: CandidateBinding

    @property
    def qualified_name(self) -> str:
        return f"{self.owner}.{self.name}"


class MetricBinding(BindingBase):
    name: str
    candidate: CandidateBinding


class PathPatternBinding(BindingBase):
    name: str
    candidate: CandidateBinding


class LiteralBinding(BindingBase):
    raw_literal: str
    resolved: bool
    value: Any | None = None
    normalized_value: Any | None = None
    match_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    owner: str | None = None
    property: str
    evidence: list[LiteralEvidence] = Field(default_factory=list)
    alternatives: list[LiteralAlternative] = Field(default_factory=list)
    requires_user_choice: bool = False
    value_index_miss: bool = False
    error_code: str | None = None


class FilterBinding(BindingBase):
    owner: str
    property: str
    operator: str = "="
    raw_literal: str | None = None
    value: Any | None = None
    literal: LiteralBinding | None = None


class BindingPlan(BindingBase):
    schema_version: Literal["binding_plan_v1"] = BINDING_PLAN_SCHEMA_VERSION
    query_shape: str
    vertex_bindings: list[VertexBinding] = Field(default_factory=list)
    edge_bindings: list[EdgeBinding] = Field(default_factory=list)
    property_bindings: list[PropertyBinding] = Field(default_factory=list)
    literal_bindings: list[LiteralBinding] = Field(default_factory=list)
    metric_bindings: list[MetricBinding] = Field(default_factory=list)
    path_pattern_bindings: list[PathPatternBinding] = Field(default_factory=list)
    filters: list[FilterBinding] = Field(default_factory=list)
    group_by: list[dict[str, Any]] = Field(default_factory=list)
    measures: list[dict[str, Any]] = Field(default_factory=list)
    projection: list[dict[str, Any]] = Field(default_factory=list)
    sort: list[dict[str, Any]] = Field(default_factory=list)
    limit: int | None = None
    assumptions: list[dict[str, Any]] = Field(default_factory=list)
