from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


AgentKnowledgeType = Literal["cypher_syntax", "few_shot", "system_prompt", "business_knowledge"]
AgentActionKind = Literal["tool_call", "request_human_review", "final"]
FinalStatus = Literal["ready_for_review", "rejected"]


class AgentRunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    APPLIED = "applied"
    REDISPATCHED = "redispatched"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class RootCause(BaseModel):
    type: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    suggested_fix: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)


class AgentConstraints(BaseModel):
    auto_apply: bool = False
    max_steps: int = Field(default=12, ge=1, le=50)
    allowed_tools: list[str] = Field(
        default_factory=lambda: [
            "inspect_qa_case",
            "retrieve_knowledge",
            "rag_retrieve",
            "read_repair_memory",
            "classify_gap",
            "propose_patch",
            "check_duplicate",
            "check_conflict",
            "build_prompt_overlay",
            "evaluate_before_after",
        ]
    )
    verification_required: bool = True


class AgentAction(BaseModel):
    action: AgentActionKind
    tool_name: Optional[str] = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: Optional[FinalStatus] = None
    reason_summary: str = Field(min_length=1)
    summary: str = ""

    @model_validator(mode="after")
    def validate_action_shape(self) -> "AgentAction":
        if self.action == "tool_call" and not self.tool_name:
            raise ValueError("tool_name is required for tool_call actions")
        if self.action != "tool_call" and self.tool_name:
            raise ValueError("tool_name is only allowed for tool_call actions")
        if self.action == "final" and not self.status:
            raise ValueError("status is required for final actions")
        return self


class AgentTraceEntry(BaseModel):
    step: int = Field(ge=1)
    action: AgentAction
    observation: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class CandidateChange(BaseModel):
    operation: Literal["add", "modify", "delete"] = "add"
    doc_type: AgentKnowledgeType
    section: str = Field(min_length=1)
    target_key: str = Field(min_length=1)
    new_content: str = Field(min_length=1)
    rationale: str = ""
    risk: Literal["low", "medium", "high"] = "medium"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    duplicate_checked: bool = False
    conflict_checked: bool = False


class GapDiagnosis(BaseModel):
    gap_type: Literal[
        "knowledge_missing",
        "retrieval_miss",
        "prompt_orchestration_gap",
        "generator_noncompliance",
        "knowledge_conflict",
        "unknown",
    ] = "unknown"
    reason: str = ""
    suggested_action: str = ""


class ValidationSummary(BaseModel):
    prompt_package_built: bool = False
    before_after_improved: bool = False
    redispatch_status: str = ""
    remaining_risks: list[str] = Field(default_factory=list)


class AgentDecision(BaseModel):
    action: Literal["continue", "human_review", "apply", "reject", "complete"] = "continue"
    reason: str = ""


class CreateRepairAgentRunRequest(BaseModel):
    qa_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    root_cause: RootCause
    constraints: AgentConstraints = Field(default_factory=AgentConstraints)


class AgentRun(BaseModel):
    run_id: str = Field(default_factory=lambda: f"krun_{uuid4().hex[:12]}")
    qa_id: str
    goal: str
    root_cause: RootCause
    constraints: AgentConstraints = Field(default_factory=AgentConstraints)
    status: AgentRunStatus = AgentRunStatus.CREATED
    trace: list[AgentTraceEntry] = Field(default_factory=list)
    memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    gap_diagnosis: GapDiagnosis = Field(default_factory=GapDiagnosis)
    candidate_changes: list[CandidateChange] = Field(default_factory=list)
    validation: ValidationSummary = Field(default_factory=ValidationSummary)
    decision: Optional[AgentDecision] = None
    errors: list[str] = Field(default_factory=list)
