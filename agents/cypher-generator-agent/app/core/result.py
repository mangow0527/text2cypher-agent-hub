from __future__ import annotations

from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import GenerationFailureReason, GenerationFinalStatus, ServiceFailureReason


class ClarificationRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    question: str = Field(..., description="Question to ask the user before generation can continue.")


class GenerationFailure(BaseModel):
    model_config = ConfigDict(extra="allow")

    reason: Union[GenerationFailureReason, ServiceFailureReason]
    message: Optional[str] = None
    suggested_rewrites: list[str] = Field(default_factory=list)


class GenerationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: GenerationFinalStatus
    cypher: Optional[str] = None
    dsl: Optional[dict[str, Any]] = None
    trace: dict[str, Any]
    clarification: Optional[ClarificationRequest] = None
    failure: Optional[GenerationFailure] = None
    user_visible_notices: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "GenerationOutput":
        if self.status == "generated":
            if not self.cypher or not self.cypher.strip():
                raise ValueError("generated requires non-empty cypher")
            if not self.dsl:
                raise ValueError("generated requires dsl")
            if self.failure is not None:
                raise ValueError("generated must not include failure")
            if self.clarification is not None:
                raise ValueError("generated must not include clarification")
            return self

        if self.cypher is not None:
            raise ValueError("non-generated outputs must not include cypher")
        if self.dsl is not None:
            raise ValueError("non-generated outputs must not include dsl")

        if self.status == "clarification_required":
            if self.clarification is None:
                raise ValueError("clarification_required requires clarification")
            if self.failure is not None:
                raise ValueError("clarification_required must not include failure")
            return self

        if self.status == "unsupported_query_shape":
            if self.failure is None:
                raise ValueError("unsupported_query_shape requires failure")
            if self.failure.reason != "unsupported_query_shape":
                raise ValueError("unsupported_query_shape requires unsupported_query_shape reason")
            return self

        if self.failure is None:
            raise ValueError(f"{self.status} requires failure")
        if self.status == "generation_failed" and self.failure.reason in set(ServiceFailureReason.__args__):
            raise ValueError("generation_failed requires GenerationFailure reason")
        if self.status == "service_failed" and self.failure.reason not in set(ServiceFailureReason.__args__):
            raise ValueError("service_failed requires ServiceFailure reason")
        return self
