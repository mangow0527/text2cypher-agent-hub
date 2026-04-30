from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_serializer, model_validator


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
    "generation_retry_exhausted",
]

ServiceFailureReason = Literal[
    "knowledge_context_unavailable",
    "model_invocation_failed",
    "testing_agent_submission_failed",
]

GenerationStatus = Literal["submitted_to_testing", "generation_failed", "service_failed"]
GenerationReportStatus = Literal["generation_failed", "service_failed"]


class QAQuestionRequest(BaseModel):
    id: str = Field(..., description="QA sample identifier provided by qa-agent.")
    question: str = Field(..., description="Natural language question to generate Cypher for.")


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
    def serialize(self) -> dict[str, bool | GenerationFailureReason]:
        payload: dict[str, bool | GenerationFailureReason] = {"accepted": self.accepted}
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


class GeneratedCypherSubmissionRequest(BaseModel):
    id: str
    question: str
    generation_run_id: str
    generated_cypher: str
    input_prompt_snapshot: str
    last_llm_raw_output: str
    generation_retry_count: int = Field(default=0, ge=0)
    generation_failure_reasons: list[GenerationFailureReason] = Field(default_factory=list)


class GenerationRunFailureReport(BaseModel):
    id: str
    question: str
    generation_run_id: str
    input_prompt_snapshot: str
    last_llm_raw_output: str = ""
    generation_status: GenerationReportStatus
    failure_reason: GenerationFailureReason | ServiceFailureReason
    last_generation_failure_reason: Optional[GenerationFailureReason] = None
    generation_retry_count: int = Field(default=0, ge=0)
    generation_failure_reasons: list[GenerationFailureReason] = Field(default_factory=list)
    parsed_cypher: Optional[str] = None
    gate_passed: bool = False

    @model_validator(mode="after")
    def validate_failure_reason_matches_status(self) -> "GenerationRunFailureReport":
        generation_reasons = set(GenerationFailureReason.__args__)
        service_reasons = set(ServiceFailureReason.__args__)
        if self.generation_status == "generation_failed":
            if self.failure_reason not in generation_reasons:
                raise ValueError("generation_failed requires GenerationFailure reason")
            if self.failure_reason == "generation_retry_exhausted" and self.last_generation_failure_reason is None:
                raise ValueError("generation_retry_exhausted requires last_generation_failure_reason")
            return self
        if self.failure_reason not in service_reasons:
            raise ValueError("service_failed requires ServiceFailure reason")
        if self.last_generation_failure_reason is not None:
            raise ValueError("service_failed must not include last_generation_failure_reason")
        return self


class GenerationRunResult(BaseModel):
    generation_run_id: str
    generation_status: GenerationStatus
    reason: Optional[GenerationFailureReason | ServiceFailureReason] = None
    last_reason: Optional[GenerationFailureReason] = None

    @model_validator(mode="after")
    def validate_reason_matches_status(self) -> "GenerationRunResult":
        if self.generation_status == "submitted_to_testing":
            if self.reason is not None or self.last_reason is not None:
                raise ValueError("submitted_to_testing must not include failure reason")
            return self

        if self.generation_status == "generation_failed":
            generation_reasons = set(GenerationFailureReason.__args__)
            if self.reason not in generation_reasons:
                raise ValueError("generation_failed requires GenerationFailure reason")
            if self.reason == "generation_retry_exhausted" and self.last_reason is None:
                raise ValueError("generation_retry_exhausted requires last_reason")
            return self

        service_reasons = set(ServiceFailureReason.__args__)
        if self.reason not in service_reasons:
            raise ValueError("service_failed requires ServiceFailure reason")
        if self.last_reason is not None:
            raise ValueError("service_failed must not include last_reason")
        return self
