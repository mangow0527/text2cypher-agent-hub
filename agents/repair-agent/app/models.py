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
ValidationMode = Literal["disabled", "lightweight"]
RepairStatus = Literal["applied"]


# Local knowledge package helpers still used by repair knowledge scaffolding.
class KnowledgeContext(BaseModel):
    package_id: str
    version: str
    graph_name: str
    summary: str
    loaded_knowledge_tags: List[str] = Field(default_factory=list)


class KnowledgePackage(BaseModel):
    package_id: str
    version: str
    graph_name: str
    summary: str
    schema_facts: Dict[str, Any]
    business_terms: Dict[str, List[str]]
    query_patterns: Dict[str, str]
    constraints: Dict[str, List[str]]
    knowledge_tags: List[str]


class KnowledgeRepairSuggestionRequest(BaseModel):
    id: str
    suggestion: str
    knowledge_types: List[KnowledgeType]


class RepairIssueTicketResponse(BaseModel):
    status: RepairStatus = "applied"
    analysis_id: str
    id: str
    knowledge_repair_request: KnowledgeRepairSuggestionRequest
    knowledge_ops_response: Optional[Dict[str, Any]] = None
    applied: bool = True


class RepairAnalysisRecord(BaseModel):
    analysis_id: str
    ticket_id: str
    id: str
    status: RepairStatus = "applied"
    prompt_snapshot: str
    knowledge_repair_request: KnowledgeRepairSuggestionRequest
    knowledge_ops_response: Optional[Dict[str, Any]] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""
    used_experiments: bool = False
    primary_knowledge_type: Optional[KnowledgeType] = None
    secondary_knowledge_types: List[KnowledgeType] = Field(default_factory=list)
    candidate_patch_types: List[KnowledgeType] = Field(default_factory=list)
    validation_mode: ValidationMode = "disabled"
    validation_result: Dict[str, Any] = Field(default_factory=dict)
    diagnosis_context_summary: Dict[str, Any] = Field(default_factory=dict)
    applied: bool = True
    created_at: str
    applied_at: str


ActualAnswer = ActualPayload
ExpectedAnswer = ExpectedPayload
TuGraphExecutionResult = ExecutionResult


__all__ = [
    "ActualAnswer",
    "ActualPayload",
    "Difficulty",
    "EvaluationSummary",
    "ExecutionResult",
    "ExpectedAnswer",
    "ExpectedPayload",
    "GenerationEvidence",
    "IssueTicket",
    "KnowledgeContext",
    "KnowledgePackage",
    "KnowledgeRepairSuggestionRequest",
    "KnowledgeType",
    "RepairAnalysisRecord",
    "RepairIssueTicketResponse",
    "RepairStatus",
    "TuGraphExecutionResult",
    "ValidationMode",
    "Verdict",
]
