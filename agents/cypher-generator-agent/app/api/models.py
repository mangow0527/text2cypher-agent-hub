from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.cypher_generator_agent.app.core.errors import (
    GenerationFailureReason,
    GenerationFinalStatus,
    GenerationSubmissionStatus,
    ServiceFailureReason,
)

GenerationReportStatus = Literal[
    "generation_failed",
    "clarification_required",
    "unsupported_query_shape",
    "service_failed",
]


class QAQuestionRequest(BaseModel):
    id: str = Field(..., description="QA sample identifier provided by qa-agent.")
    question: str = Field(..., description="Natural language question to generate Cypher for.")


class IntentRecognitionRequest(BaseModel):
    question: str = Field(..., description="Natural language question to recognize intent for.")


class SemanticParseRequest(BaseModel):
    id: Optional[str] = Field(default=None, description="Optional QA sample identifier for traceability.")
    question: str = Field(..., description="Natural language question to parse into semantic plan and Cypher.")
    generation_run_id: Optional[str] = Field(default=None, description="Optional generation run identifier for traceability.")


class GeneratedCypherSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    generation_run_id: str
    generation_status: Literal["generated"] = "generated"
    generated_cypher: str
    input_prompt_snapshot: str


class CgaQuestionReceivedReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    generation_run_id: str
    generation_status: Literal["generation_pending"] = "generation_pending"


class CgaGenerationNonSuccessReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    generation_run_id: str
    generation_status: GenerationReportStatus
    input_prompt_snapshot: str
    failure_reason: Optional[Union[GenerationFailureReason, ServiceFailureReason]] = None
    clarification: Optional[dict[str, Any]] = None
    parsed_cypher: Optional[str] = None
    gate_passed: bool = False

    @model_validator(mode="after")
    def validate_non_success_status(self) -> "CgaGenerationNonSuccessReport":
        generation_reasons = set(GenerationFailureReason.__args__)
        service_reasons = set(ServiceFailureReason.__args__)
        if self.gate_passed:
            raise ValueError(f"{self.generation_status} reports must not set gate_passed=true")
        if self.generation_status == "generation_failed":
            if self.failure_reason not in generation_reasons:
                raise ValueError("generation_failed requires GenerationFailure reason")
            if self.clarification is not None:
                raise ValueError("generation_failed must not include clarification")
            return self
        if self.generation_status == "clarification_required":
            if self.clarification is None:
                raise ValueError("clarification_required requires clarification")
            if self.parsed_cypher is not None:
                raise ValueError("clarification_required must not include parsed_cypher")
            return self
        if self.generation_status == "unsupported_query_shape":
            if self.failure_reason != "unsupported_query_shape":
                raise ValueError("unsupported_query_shape requires unsupported_query_shape failure reason")
            if self.parsed_cypher is not None:
                raise ValueError("unsupported_query_shape must not include parsed_cypher")
            return self
        if self.failure_reason not in service_reasons:
            raise ValueError("service_failed requires ServiceFailure reason")
        if self.clarification is not None:
            raise ValueError("service_failed must not include clarification")
        if self.parsed_cypher is not None:
            raise ValueError("service_failed must not include parsed_cypher")
        return self


class GenerationRunResult(BaseModel):
    generation_run_id: str
    submission_status: Optional[GenerationSubmissionStatus] = None
    generation_status: Optional[GenerationFinalStatus] = None
    reason: Optional[Union[GenerationFailureReason, ServiceFailureReason]] = None
    last_reason: Optional[GenerationFailureReason] = None

    @model_validator(mode="after")
    def validate_reason_matches_status(self) -> "GenerationRunResult":
        if self.generation_status is None:
            if self.reason is not None or self.last_reason is not None:
                raise ValueError("submission-only result must not include failure reason")
            return self
        if self.generation_status == "generated":
            if self.reason is not None or self.last_reason is not None:
                raise ValueError("generated must not include failure reason")
            return self
        if self.generation_status == "clarification_required":
            if self.last_reason is not None:
                raise ValueError("clarification_required must not include last_reason")
            return self
        if self.generation_status == "unsupported_query_shape":
            if self.reason != "unsupported_query_shape":
                raise ValueError("unsupported_query_shape requires unsupported_query_shape reason")
            if self.last_reason is not None:
                raise ValueError("unsupported_query_shape must not include last_reason")
            return self

        if self.generation_status == "generation_failed":
            generation_reasons = set(GenerationFailureReason.__args__)
            if self.reason not in generation_reasons:
                raise ValueError("generation_failed requires GenerationFailure reason")
            return self

        service_reasons = set(ServiceFailureReason.__args__)
        if self.reason not in service_reasons:
            raise ValueError("service_failed requires ServiceFailure reason")
        if self.last_reason is not None:
            raise ValueError("service_failed must not include last_reason")
        return self
