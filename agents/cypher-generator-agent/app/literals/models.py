from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


REQUEST_SCHEMA_VERSION = "literal_resolver_request_v1"
RESULT_SCHEMA_VERSION = "literal_resolver_result_v1"

LiteralKindHint = Literal[
    "enum",
    "enum_or_name",
    "id",
    "name",
    "time",
    "numeric",
    "unknown",
]
MatchType = Literal[
    "exact",
    "value_synonym",
    "typed_parse",
    "fuzzy_text",
    "embedding",
    "value_index_exact",
    "literal_passthrough",
    "distinct_candidate",
    "unresolved",
]
LiteralErrorCode = Literal[
    "literal_unresolved",
    "literal_ambiguous",
    "literal_property_mismatch",
    "literal_value_index_miss",
    "literal_cache_stale_suspected",
]


class LiteralResolverBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LiteralEvidence(LiteralResolverBase):
    source: str
    matched: Any
    target: Any


class LiteralAlternative(LiteralResolverBase):
    value: Any
    display: str
    confidence: float = Field(ge=0.0, le=1.0)
    source: str
    why: Optional[str] = None


class LiteralResolverRequest(LiteralResolverBase):
    schema_version: Literal["literal_resolver_request_v1"] = REQUEST_SCHEMA_VERSION
    raw_literal: str = Field(min_length=1)
    expected_vertex: Optional[str] = None
    expected_edge: Optional[str] = None
    expected_property: str = Field(min_length=1)
    literal_kind_hint: Optional[LiteralKindHint] = None
    question_context: Optional[str] = None
    trace_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_owner(self) -> "LiteralResolverRequest":
        if (self.expected_vertex is None) == (self.expected_edge is None):
            raise ValueError("exactly one of expected_vertex or expected_edge is required")
        return self

    @property
    def owner(self) -> str:
        if self.expected_vertex is not None:
            return self.expected_vertex
        if self.expected_edge is not None:
            return self.expected_edge
        raise ValueError("literal request owner is not set")


class LiteralResolverResult(LiteralResolverBase):
    schema_version: Literal["literal_resolver_result_v1"] = RESULT_SCHEMA_VERSION
    raw_literal: str
    resolved: bool
    resolved_value: Any | None = None
    normalized_value: Any | None = None
    match_type: MatchType
    confidence: float = Field(ge=0.0, le=1.0)
    expected_vertex: Optional[str] = None
    expected_edge: Optional[str] = None
    expected_property: str
    evidence: list[LiteralEvidence] = Field(default_factory=list)
    alternatives: list[LiteralAlternative] = Field(default_factory=list, max_length=3)
    requires_user_choice: bool = False
    value_index_miss: bool = False
    error_code: Optional[LiteralErrorCode] = None
