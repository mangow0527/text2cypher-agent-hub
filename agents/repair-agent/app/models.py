from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from services.testing_agent.app.models import (
    ActualPayload,
    Difficulty,
    EvaluationSummary,
    ExecutionResult,
    ExpectedPayload,
    GenerationEvidence,
    IssueTicket,
    Verdict,
)


KnowledgeType = Literal["cypher_syntax", "few_shot", "system_prompt", "business_knowledge"]
RepairStatus = Literal["analysis_pending", "apply_failed", "applied", "not_repairable", "repair_apply_paused"]


class KnowledgeRepairSuggestionRequest(BaseModel):
    id: str
    suggestion: str
    knowledge_types: List[KnowledgeType]


class RepairIssueTicketResponse(BaseModel):
    status: RepairStatus = "applied"
    analysis_id: str
    id: str
    knowledge_repair_request: Optional[KnowledgeRepairSuggestionRequest] = None
    knowledge_agent_response: Optional[Dict[str, Any]] = None
    applied: bool = True


class RepairAnalysisRecord(BaseModel):
    analysis_id: str
    ticket_id: str
    id: str
    status: RepairStatus = "applied"
    prompt_snapshot: str
    system_prompt_snapshot: Optional[str] = None
    user_prompt_snapshot: Optional[str] = None
    raw_output: str = ""
    knowledge_repair_request: Optional[KnowledgeRepairSuggestionRequest] = None
    knowledge_agent_response: Optional[Dict[str, Any]] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""
    primary_knowledge_type: Optional[KnowledgeType] = None
    secondary_knowledge_types: List[KnowledgeType] = Field(default_factory=list)
    diagnosis_context_summary: Dict[str, Any] = Field(default_factory=dict)
    non_repairable_reason: str = ""
    applied: bool = True
    created_at: str
    applied_at: str


__all__ = [
    "ActualPayload",
    "Difficulty",
    "EvaluationSummary",
    "ExecutionResult",
    "ExpectedPayload",
    "GenerationEvidence",
    "IssueTicket",
    "KnowledgeRepairSuggestionRequest",
    "KnowledgeType",
    "RepairAnalysisRecord",
    "RepairIssueTicketResponse",
    "RepairStatus",
    "Verdict",
]
