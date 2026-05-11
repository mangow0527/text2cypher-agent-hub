from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


GenerationFailureReason = Literal[
    "empty_output",
    "no_cypher_found",
    "wrapped_in_markdown",
    "wrapped_in_json",
    "contains_explanation",
    "multiple_statements",
    "unbalanced_brackets",
    "unclosed_string",
    "write_operation",
    "unsupported_call",
    "unsupported_start_clause",
    "unauthorized_schema_reference",
    "logical_plan_mismatch",
    "semantic_match_rejected",
    "path_planning_failed",
    "cypher_fallback_cannot_generate",
]

ServiceFailureReason = Literal[
    "knowledge_context_unavailable",
    "semantic_contract_unaligned",
    "model_invocation_failed",
    "testing_agent_submission_failed",
]

GenerationStatus = Literal["submitted_to_testing", "clarification_required", "generation_failed", "service_failed"]
GenerationReportStatus = Literal["generation_failed", "clarification_required", "service_failed"]


class QAQuestionRequest(BaseModel):
    id: str = Field(..., description="QA sample identifier provided by qa-agent.")
    question: str = Field(..., description="Natural language question to generate Cypher for.")


class IntentRecognitionRequest(BaseModel):
    question: str = Field(..., description="Natural language question to recognize intent for.")


class SemanticParseRequest(BaseModel):
    id: Optional[str] = Field(default=None, description="Optional QA sample identifier for traceability.")
    question: str = Field(..., description="Natural language question to parse into semantic plan and Cypher.")
    generation_run_id: Optional[str] = Field(default=None, description="Optional generation run identifier for traceability.")


class PreflightCheck(BaseModel):
    accepted: bool
    reason: Optional[GenerationFailureReason] = None

    @model_validator(mode="after")
    def validate_reason_matches_acceptance(self) -> "PreflightCheck":
        if self.accepted and self.reason is not None:
            raise ValueError("accepted preflight_check must not include reason")
        if not self.accepted and self.reason is None:
            raise ValueError("rejected preflight_check must include reason")
        return self

    @model_serializer
    def serialize(self) -> dict[str, object]:
        payload: dict[str, object] = {"accepted": self.accepted}
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


class GeneratedCypherSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    generation_run_id: str
    generation_status: Literal["generated"] = "generated"
    generated_cypher: str
    input_prompt_snapshot: str


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
        if self.failure_reason not in service_reasons:
            raise ValueError("service_failed requires ServiceFailure reason")
        if self.clarification is not None:
            raise ValueError("service_failed must not include clarification")
        if self.parsed_cypher is not None:
            raise ValueError("service_failed must not include parsed_cypher")
        return self


class GenerationRunResult(BaseModel):
    generation_run_id: str
    generation_status: GenerationStatus
    reason: Optional[Union[GenerationFailureReason, ServiceFailureReason]] = None
    last_reason: Optional[GenerationFailureReason] = None

    @model_validator(mode="after")
    def validate_reason_matches_status(self) -> "GenerationRunResult":
        if self.generation_status == "submitted_to_testing":
            if self.reason is not None or self.last_reason is not None:
                raise ValueError("submitted_to_testing must not include failure reason")
            return self
        if self.generation_status == "clarification_required":
            if self.last_reason is not None:
                raise ValueError("clarification_required must not include last_reason")
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
