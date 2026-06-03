from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


REPAIR_CONTROLLER_INPUT_SCHEMA_VERSION = "repair_controller_input_v1"
REPAIR_CONTROLLER_DECISION_SCHEMA_VERSION = "repair_controller_decision_v1"

RepairDecisionKind = Literal[
    "repair_with_llm",
    "ask_user",
    "unsupported",
    "generation_failed",
    "continue_with_assumption",
]


class RepairBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepairIssue(RepairBaseModel):
    model_config = ConfigDict(extra="allow")

    code: str
    message: str = ""
    severity: str = "error"
    repairable: bool | None = None
    action: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RepairHistoryItem(RepairBaseModel):
    attempt_no: int | None = None
    fingerprint: str
    error_code: str | None = None
    missing_requirements: list[str] = Field(default_factory=list)


class RepairControllerInput(RepairBaseModel):
    schema_version: Literal["repair_controller_input_v1"] = REPAIR_CONTROLLER_INPUT_SCHEMA_VERSION
    trace_id: str
    question: str
    attempt_no: int = Field(ge=1)
    selected_bindings: dict[str, Any] = Field(default_factory=dict)
    normalized_dsl: dict[str, Any] | None = None
    validator_errors: list[RepairIssue] = Field(default_factory=list)
    cypher_validation_errors: list[RepairIssue] = Field(default_factory=list)
    history: list[RepairHistoryItem] = Field(default_factory=list)
    assumptions: list[dict[str, Any]] = Field(default_factory=list)


class ClarificationOption(RepairBaseModel):
    id: str
    label: str
    vertex_name: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    value: Any | None = None

    @field_validator("id", "label")
    @classmethod
    def require_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("clarification option text must not be empty")
        return text


class ClarificationQuestion(RepairBaseModel):
    source_stage: str
    reason_code: str
    question: str | None = None
    question_zh: str | None = None
    expected_answer_type: Literal["single_choice", "free_text"] = "single_choice"
    options: list[ClarificationOption] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def sync_question_fields(self) -> "ClarificationQuestion":
        if self.question is None and self.question_zh is not None:
            self.question = self.question_zh
        if self.question_zh is None and self.question is not None:
            self.question_zh = self.question
        if not self.question or not self.question.strip():
            raise ValueError("clarification question must not be empty")
        self.question = self.question.strip()
        self.question_zh = self.question_zh.strip() if self.question_zh else self.question
        return self


class RepairAssumption(RepairBaseModel):
    kind: str | None = None
    type: str | None = None
    raw: str | None = None
    raw_literal: str | None = None
    assumed_as: Any | None = None
    value: Any | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    property: str | None = None
    term: str | None = None
    message: str | None = None

    @model_validator(mode="after")
    def normalize_aliases(self) -> "RepairAssumption":
        if self.kind is None and self.type is not None:
            self.kind = self.type
        if self.raw is None and self.raw_literal is not None:
            self.raw = self.raw_literal
        if self.assumed_as is None and self.value is not None:
            self.assumed_as = self.value
        if self.kind is None:
            raise ValueError("repair assumption kind is required")
        return self


class RepairDecision(RepairBaseModel):
    schema_version: Literal["repair_controller_decision_v1"] = REPAIR_CONTROLLER_DECISION_SCHEMA_VERSION
    decision: RepairDecisionKind
    reason_code: str
    repair_prompt_delta: dict[str, Any] = Field(default_factory=dict)
    clarification: ClarificationQuestion | None = None
    assumptions: list[RepairAssumption] = Field(default_factory=list)
    stop_reason: str | None = None
    derived_user_visible_notices: list[str] = Field(default_factory=list)
    suggested_rewrites: list[str] = Field(default_factory=list)
