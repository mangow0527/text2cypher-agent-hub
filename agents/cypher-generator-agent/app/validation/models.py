from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


IssueSeverity = Literal["error", "warning"]
IssueRecoverability = Literal["repairable", "non_repairable", "warning_only"]
IssueAction = Literal[
    "repair_binding",
    "ask_user",
    "generation_failed",
    "unsupported_query_shape",
    "continue_with_assumption",
]


class SemanticValidationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SemanticValidationIssue(SemanticValidationBase):
    code: str
    message: str
    severity: IssueSeverity
    recoverability: IssueRecoverability
    action: IssueAction
    details: dict[str, Any] = Field(default_factory=dict)


class SemanticValidationResult(SemanticValidationBase):
    is_valid: bool = True
    errors: list[SemanticValidationIssue] = Field(default_factory=list)
    warnings: list[SemanticValidationIssue] = Field(default_factory=list)
    assumptions: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def sync_validity(self) -> "SemanticValidationResult":
        self.is_valid = not self.errors
        return self
