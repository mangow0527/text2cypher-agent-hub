from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class SemanticModelError(Exception):
    """Base class for semantic model loading and lookup errors."""


class SemanticModelBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PropertyDefinition(SemanticModelBase):
    name: str
    type: str
    required: bool = False
    description: Optional[str] = None
    ai_context: dict[str, Any] = Field(default_factory=dict)
    valid_values: list[str] = Field(default_factory=list)
    value_synonyms: dict[str, list[str]] = Field(default_factory=dict)
    cypher_expression: Optional[str] = None


class VertexDefinition(SemanticModelBase):
    name: str
    id_property: str
    description: Optional[str] = None
    ai_context: dict[str, Any] = Field(default_factory=dict)
    properties: list[PropertyDefinition] = Field(default_factory=list)


class EdgeDefinition(SemanticModelBase):
    name: str
    from_vertex: str = Field(alias="from")
    to_vertex: str = Field(alias="to")
    cardinality: str
    direction_semantics: Optional[str] = None
    anti_patterns: list[str] = Field(default_factory=list)
    description: Optional[str] = None
    ai_context: dict[str, Any] = Field(default_factory=dict)
    properties: list[PropertyDefinition] = Field(default_factory=list)


class ParameterDefinition(SemanticModelBase):
    name: str
    type: str
    description: Optional[str] = None


class PathPatternDefinition(SemanticModelBase):
    name: str
    description: Optional[str] = None
    parameters: list[ParameterDefinition] = Field(default_factory=list)
    cypher: str
    ai_context: dict[str, Any] = Field(default_factory=dict)


class MetricDefinition(SemanticModelBase):
    name: str
    description: Optional[str] = None
    pattern: Optional[str] = None
    expression: Optional[str] = None
    full_cypher: Optional[str] = None
    valid_dimensions: list[str] = Field(default_factory=list)
    ai_context: dict[str, Any] = Field(default_factory=dict)


class GraphSemanticModel(SemanticModelBase):
    name: str
    description: Optional[str] = None
    ai_context: dict[str, Any] = Field(default_factory=dict)
    vertices: list[VertexDefinition]
    edges: list[EdgeDefinition] = Field(default_factory=list)
    path_patterns: list[PathPatternDefinition] = Field(default_factory=list)
    metrics: list[MetricDefinition] = Field(default_factory=list)
