from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

from services.cypher_generator_agent.app.literals.models import LiteralResolverResult
from services.cypher_generator_agent.app.retrieval.models import SemanticType
from services.cypher_generator_agent.app.validation.coverage import CoverageReport


GROUNDED_UNDERSTANDING_SCHEMA_VERSION = "grounded_understanding_v1"


class UnderstandingBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


BindingDirection = Literal["forward", "backward"]
GroundedStatus = Literal["grounded", "clarification_required", "unsupported_query_shape", "failed"]
GroundedQueryShape = Literal[
    "vertex_lookup",
    "single_hop_traversal",
    "single_hop",
    "named_path_pattern",
    "variable_path_traversal",
    "variable_path",
    "metric_aggregate",
    "ad_hoc_aggregate",
    "top_n",
    "two_step_aggregate",
    "lookup",
    "unsupported",
]


class GroundedBinding(UnderstandingBaseModel):
    role: str = Field(min_length=1)
    semantic_type: SemanticType
    candidate_id: str = Field(min_length=1)
    semantic_id: str = Field(min_length=1)
    semantic_name: str = Field(min_length=1)
    owner: str | None = None
    direction: BindingDirection | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str | None = None

    @field_validator("role", "candidate_id", "semantic_id", "semantic_name")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("grounded binding text fields must not be empty")
        return text

    @field_validator("owner")
    @classmethod
    def strip_optional_owner(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None

    def to_binder_item(self) -> dict[str, Any]:
        if self.semantic_type == "property":
            owner, name = self._property_owner_name()
            return {"owner": owner, "name": name, "semantic_id": self.semantic_id}

        item: dict[str, Any] = {
            "name": self.semantic_id,
            "semantic_id": self.semantic_id,
        }
        if self.semantic_type == "edge" and self.direction is not None:
            item["direction"] = self.direction
        return item

    def _property_owner_name(self) -> tuple[str, str]:
        if self.owner is not None:
            return self.owner, self.semantic_name
        if "." in self.semantic_id:
            owner, name = self.semantic_id.split(".", 1)
            return owner, name
        raise ValueError(f"property binding is missing owner: {self.semantic_id}")


class CompactGroundedBinding(UnderstandingBaseModel):
    model_config = ConfigDict(extra="ignore")

    candidate_id: str = Field(min_length=1)
    role: str | None = None
    direction: BindingDirection | None = None

    @field_validator("candidate_id")
    @classmethod
    def strip_candidate_id(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("compact binding candidate_id must not be empty")
        return text

    @field_validator("role")
    @classmethod
    def strip_optional_role(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class GroundedAmbiguity(UnderstandingBaseModel):
    role: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    candidate_ids: list[str] = Field(min_length=2)

    @field_validator("role", "reason")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("ambiguity text fields must not be empty")
        return text

    @field_validator("candidate_ids", mode="after")
    @classmethod
    def strip_candidate_ids(cls, value: list[str]) -> list[str]:
        candidate_ids = [candidate_id.strip() for candidate_id in value]
        if any(not candidate_id for candidate_id in candidate_ids):
            raise ValueError("ambiguity candidate_ids must not be empty")
        return candidate_ids


class GroundedUnsupported(UnderstandingBaseModel):
    reason_code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    suggested_rewrites: list[str] = Field(default_factory=list)

    @field_validator("reason_code", "message")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("unsupported text fields must not be empty")
        return text


class CompactGroundedUnderstanding(UnderstandingBaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["grounded_understanding_v1"] = GROUNDED_UNDERSTANDING_SCHEMA_VERSION
    status: GroundedStatus
    query_shape: GroundedQueryShape
    selected_bindings: list[CompactGroundedBinding]
    selected_literal_ids: list[str] = Field(default_factory=list)
    filters: list[Any] = Field(default_factory=list)
    projection: list[Any] = Field(default_factory=list)
    group_by: list[Any] = Field(default_factory=list)
    measures: list[Any] = Field(default_factory=list)
    sort: list[Any] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1)
    assumptions: list[Any] = Field(default_factory=list)
    ambiguities: list[GroundedAmbiguity] = Field(default_factory=list)
    unsupported: GroundedUnsupported | None = None

    @field_validator(
        "filters",
        "projection",
        "group_by",
        "measures",
        "sort",
        "assumptions",
        "ambiguities",
        mode="before",
    )
    @classmethod
    def empty_list_for_null_sequences(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("query_shape")
    @classmethod
    def strip_query_shape(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("query_shape must not be empty")
        return text

    @field_validator("selected_literal_ids", mode="after")
    @classmethod
    def strip_selected_literal_ids(cls, value: list[str]) -> list[str]:
        literal_ids = [literal_id.strip() for literal_id in value]
        if any(not literal_id for literal_id in literal_ids):
            raise ValueError("selected_literal_ids must not contain empty literal ids")
        return literal_ids

    @model_validator(mode="after")
    def validate_status_payload(self) -> "CompactGroundedUnderstanding":
        if self.status == "unsupported_query_shape":
            if self.unsupported is None:
                raise ValueError("unsupported_query_shape requires unsupported payload")
            if self.query_shape != "unsupported":
                raise ValueError("unsupported_query_shape requires query_shape=unsupported")
            return self
        if self.unsupported is not None:
            raise ValueError(f"{self.status} output must not include unsupported payload")
        return self


class GroundedUnderstanding(UnderstandingBaseModel):
    schema_version: Literal["grounded_understanding_v1"] = GROUNDED_UNDERSTANDING_SCHEMA_VERSION
    status: GroundedStatus
    query_shape: GroundedQueryShape
    selected_bindings: list[GroundedBinding]
    selected_literals: list[LiteralResolverResult] = Field(default_factory=list)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    projection: list[dict[str, Any]] = Field(default_factory=list)
    group_by: list[dict[str, Any]] = Field(default_factory=list)
    measures: list[dict[str, Any]] = Field(default_factory=list)
    sort: list[dict[str, Any]] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1)
    assumptions: list[dict[str, Any]] = Field(default_factory=list)
    ambiguities: list[GroundedAmbiguity] = Field(default_factory=list)
    coverage: CoverageReport
    unsupported: GroundedUnsupported | None
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("query_shape")
    @classmethod
    def strip_query_shape(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("query_shape must not be empty")
        return text

    @field_validator("projection", mode="after")
    @classmethod
    def validate_projection_contract(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for item in value:
            semantic_type = item.get("semantic_type")
            if semantic_type is None:
                if "source" in item:
                    continue
                if _looks_like_projection_property(item):
                    continue
                raise ValueError("projection item requires source, semantic_type=property, or semantic_type=vertex_full")
            if semantic_type == "vertex":
                raise ValueError(
                    "ambiguous bare vertex projection is not allowed; "
                    "use semantic_type=property for requested fields or semantic_type=vertex_full "
                    "for an explicit whole-node projection"
                )
            if semantic_type not in {"property", "vertex_full"}:
                raise ValueError("projection semantic_type must be property or vertex_full")
            if semantic_type == "property" and not _looks_like_projection_property(item):
                raise ValueError("property projection requires owner/name, owner/property, or semantic_id")
            if semantic_type == "vertex_full" and not (
                item.get("semantic_id") or item.get("name") or item.get("vertex")
            ):
                raise ValueError("vertex_full projection requires semantic_id, name, or vertex")
        return value

    @model_validator(mode="after")
    def validate_status_payload(self) -> "GroundedUnderstanding":
        if self.status == "unsupported_query_shape":
            if self.unsupported is None:
                raise ValueError("unsupported_query_shape requires unsupported payload")
            if self.query_shape != "unsupported":
                raise ValueError("unsupported_query_shape requires query_shape=unsupported")
            return self
        if self.status == "grounded":
            if self.unsupported is not None:
                raise ValueError("grounded output must not include unsupported payload")
            if self.coverage.substantive_terms.uncovered:
                raise ValueError("grounded output must not include uncovered substantive terms")
            return self
        if self.unsupported is not None:
            raise ValueError(f"{self.status} output must not include unsupported payload")
        return self

    def to_binder_payload(self) -> dict[str, Any]:
        if self.status != "grounded":
            raise ValueError(f"cannot build binder payload for {self.status}")
        projection = self.projection or self._projection_items_from_selected_bindings()
        return {
            "query_shape": self.query_shape,
            "selected_vertices": self._binder_items("vertex"),
            "selected_edges": self._binder_items("edge"),
            "selected_properties": self._binder_items("property"),
            "selected_metrics": self._binder_items("metric"),
            "selected_path_patterns": self._binder_items("path_pattern"),
            "selected_literals": [literal.model_dump() for literal in self.selected_literals],
            "filters": self.filters,
            "projection": projection,
            "group_by": self.group_by,
            "measures": self.measures,
            "sort": self.sort,
            "limit": self.limit,
            "assumptions": self.assumptions,
        }

    def _binder_items(self, semantic_type: SemanticType) -> list[dict[str, Any]]:
        return [
            binding.to_binder_item()
            for binding in self.selected_bindings
            if binding.semantic_type == semantic_type
        ]

    def _projection_items_from_selected_bindings(self) -> list[dict[str, Any]]:
        projection: list[dict[str, Any]] = []
        for binding in self.selected_bindings:
            role = binding.role.casefold()
            if not any(token in role for token in ("projection", "return", "field")):
                continue
            if binding.semantic_type == "vertex":
                item = {
                    "semantic_type": "vertex_full",
                    "name": binding.semantic_id,
                    "alias": _snake_case(binding.semantic_id),
                }
                if item not in projection:
                    projection.append(item)
                continue
            if binding.semantic_type != "property":
                continue
            owner, name = binding._property_owner_name()
            item = {"semantic_type": "property", "owner": owner, "name": name}
            if item not in projection:
                projection.append(item)
        return projection


class GroundedUnderstandingAttemptError(UnderstandingBaseModel):
    attempt: int = Field(ge=1)
    error_type: str
    message: str


class GroundedUnderstandingFailure(UnderstandingBaseModel):
    status: Literal["generation_failed", "service_failed"]
    reason: Literal[
        "grounded_understanding_schema_invalid",
        "semantic_match_rejected",
        "model_invocation_failed",
    ]
    message: str
    provider: str
    error_type: str
    attempts: int = Field(ge=1)
    retry_count: int = Field(ge=0)
    errors: list[GroundedUnderstandingAttemptError] = Field(default_factory=list)


GroundedUnderstandingOutcome: TypeAlias = GroundedUnderstanding | GroundedUnderstandingFailure

GROUNDED_UNDERSTANDING_RESPONSE_ADAPTER = TypeAdapter(GroundedUnderstanding)
COMPACT_GROUNDED_UNDERSTANDING_RESPONSE_ADAPTER = TypeAdapter(CompactGroundedUnderstanding)


def _looks_like_projection_property(item: dict[str, Any]) -> bool:
    if isinstance(item.get("property"), dict):
        prop = item["property"]
        return bool(prop.get("owner") and (prop.get("name") or prop.get("property_name")))
    if item.get("owner") and (item.get("name") or item.get("property") or item.get("property_name")):
        return True
    semantic_id = item.get("semantic_id")
    return isinstance(semantic_id, str) and "." in semantic_id


def parse_grounded_understanding_response(payload: Any) -> CompactGroundedUnderstanding | GroundedUnderstanding:
    _reject_compact_binding_details(payload)
    try:
        return GROUNDED_UNDERSTANDING_RESPONSE_ADAPTER.validate_python(payload)
    except ValidationError as full_exc:
        try:
            return COMPACT_GROUNDED_UNDERSTANDING_RESPONSE_ADAPTER.validate_python(payload)
        except ValidationError as compact_exc:
            if _looks_like_legacy_full_grounding_payload(payload):
                raise full_exc
            raise compact_exc


def grounded_understanding_json_schema() -> dict[str, Any]:
    return COMPACT_GROUNDED_UNDERSTANDING_RESPONSE_ADAPTER.json_schema()


def _reject_compact_binding_details(payload: Any) -> None:
    if _has_legacy_full_top_level_payload(payload):
        return
    if not isinstance(payload, Mapping):
        return
    selected_bindings = payload.get("selected_bindings")
    if not isinstance(selected_bindings, list | tuple):
        return
    forbidden_keys = {"semantic_type", "semantic_id", "semantic_name", "owner", "confidence", "rationale"}
    for index, binding in enumerate(selected_bindings):
        if not isinstance(binding, Mapping) or "candidate_id" not in binding:
            continue
        extra_keys = sorted(forbidden_keys.intersection(binding))
        if extra_keys:
            raise ValueError(
                "compact selected_bindings must only include candidate_id, role, and direction; "
                f"binding[{index}] included {extra_keys}"
            )


def _has_legacy_full_top_level_payload(payload: Any) -> bool:
    return isinstance(payload, Mapping) and any(
        key in payload for key in ("coverage", "selected_literals", "confidence")
    )


def _looks_like_legacy_full_grounding_payload(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if any(key in payload for key in ("coverage", "selected_literals", "confidence")):
        return True
    selected_bindings = payload.get("selected_bindings")
    if not isinstance(selected_bindings, list | tuple):
        return False
    return any(
        isinstance(binding, Mapping)
        and any(key in binding for key in ("semantic_type", "semantic_id", "semantic_name", "owner"))
        for binding in selected_bindings
    )


def _snake_case(value: str) -> str:
    output = []
    previous_lower = False
    for char in value:
        if char.isupper() and previous_lower:
            output.append("_")
        output.append(char.lower())
        previous_lower = char.islower() or char.isdigit()
    return "".join(output).replace("__", "_")
