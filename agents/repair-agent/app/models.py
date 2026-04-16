from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


Difficulty = Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]
DimensionStatus = Literal["pass", "fail"]
Verdict = Literal["pass", "fail", "partial_fail"]
RootCauseType = Literal[
    "generator_logic_issue",
    "knowledge_gap_issue",
    "qa_question_issue",
    "mixed_issue",
    "unknown",
]
ActionTarget = Literal["query_generator_service", "knowledge_ops_service", "qa_generation_service"]
ActionType = Literal["prompt_adjustment", "knowledge_enrichment", "question_rewrite", "manual_review"]
GenerationProcessingStatus = Literal[
    "received",
    "prompt_fetch_failed",
    "prompt_ready",
    "generated",
    "model_invocation_failed",
    "output_parsing_failed",
    "guardrail_rejected",
    "submitted_to_testing",
    "failed",
]
EvaluationState = Literal[
    "received_golden_only",
    "received_submission_only",
    "waiting_for_golden",
    "ready_to_evaluate",
    "repair_pending",
    "repair_submission_failed",
    "issue_ticket_created",
    "passed",
]
RepairPlanState = Literal[
    "received_ticket",
    "analyzing",
    "counterfactual_checking",
    "repair_plan_created",
    "dispatched",
]
DispatchStatus = Literal["sent", "stored_for_later"]
KnowledgeType = Literal["cypher_syntax", "few_shot", "system_prompt", "business_knowledge"]
ImprovementStatus = Literal["first_run", "improved", "regressed", "unchanged", "not_comparable"]
ImprovementDimensionStatus = Literal["improved", "regressed", "unchanged", "not_comparable"]


class QAQuestionRequest(BaseModel):
    id: str = Field(..., description="Globally unique identifier for the QA item.")
    question: str


class QAGoldenRequest(BaseModel):
    id: str
    cypher: str
    answer: Any
    difficulty: Difficulty


# Legacy repair analysis context kept for issue tickets and counterfactual experiments.
class KnowledgeContext(BaseModel):
    package_id: str
    version: str
    graph_name: str
    summary: str
    loaded_knowledge_tags: List[str] = Field(default_factory=list)


# Repair-only experimental knowledge package contract.
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


# Repair-only legacy generation context. Not part of the Cypher Generation Service main contract.
class GenerationContext(BaseModel):
    id: str
    question: str
    schema_hint: Optional[str] = None
    attempt: int = Field(default=1, ge=1)
    prior_feedback: List[str] = Field(default_factory=list)
    knowledge_context: Optional[KnowledgeContext] = None


# Repair-only counterfactual generation request.
class CypherGenerationRequest(BaseModel):
    context: GenerationContext


# Repair-only generated result shape used by counterfactual experiments.
class GeneratedCypher(BaseModel):
    cypher: str
    model: str
    reasoning_summary: str
    prompt_version: str = "v1"


class TuGraphExecutionResult(BaseModel):
    success: bool
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    error_message: Optional[str] = None
    elapsed_ms: int = 0


# Current runtime contract between Cypher Generation Service and Testing Service.
class EvaluationSubmissionRequest(BaseModel):
    id: str
    question: str
    generation_run_id: str
    attempt_no: int = Field(default=1, ge=1)
    generated_cypher: str
    parse_summary: str
    guardrail_summary: str
    raw_output_snapshot: str
    input_prompt_snapshot: str


class EvaluationDimensions(BaseModel):
    syntax_validity: DimensionStatus
    schema_alignment: DimensionStatus
    result_correctness: DimensionStatus
    question_alignment: DimensionStatus


class EvaluationSummary(BaseModel):
    verdict: Verdict
    dimensions: EvaluationDimensions
    symptom: str
    evidence: List[str] = Field(default_factory=list)


class ExpectedAnswer(BaseModel):
    cypher: str
    answer: Any


class ActualAnswer(BaseModel):
    generated_cypher: str
    execution: TuGraphExecutionResult


class IssueTicket(BaseModel):
    ticket_id: str = Field(default_factory=lambda: str(uuid4()))
    id: str
    difficulty: Difficulty
    question: str
    expected: ExpectedAnswer
    actual: ActualAnswer
    knowledge_context: Optional[KnowledgeContext] = None
    evaluation: EvaluationSummary
    input_prompt_snapshot: str = ""


class RepairAction(BaseModel):
    target_service: ActionTarget
    action_type: ActionType
    instruction: str
    evidence: List[str] = Field(default_factory=list)
    dispatch_status: Optional[DispatchStatus] = None


class RepairPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    ticket_id: str
    id: str
    root_cause: RootCauseType
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    actions: List[RepairAction] = Field(default_factory=list)
    state: RepairPlanState = "repair_plan_created"
    analysis_summary: str = ""
    counterfactuals: List[Dict[str, Any]] = Field(default_factory=list)


class QueryQuestionResponse(BaseModel):
    id: str
    generation_run_id: str
    attempt_no: int = Field(default=1, ge=1)
    generation_status: GenerationProcessingStatus
    generated_cypher: str = ""
    parse_summary: str = ""
    guardrail_summary: str = ""
    raw_output_snapshot: str = ""
    failure_stage: Optional[str] = None
    failure_reason_summary: Optional[str] = None
    input_prompt_snapshot: str = ""


class PromptFetchRequest(BaseModel):
    id: str
    question: str


class PromptSnapshotResponse(BaseModel):
    id: str
    attempt_no: int = Field(default=1, ge=1)
    input_prompt_snapshot: str


class ImprovementDimensions(BaseModel):
    verdict_change: ImprovementDimensionStatus = "not_comparable"
    execution_change: ImprovementDimensionStatus = "not_comparable"
    syntax_change: ImprovementDimensionStatus = "not_comparable"
    semantic_change: ImprovementDimensionStatus = "not_comparable"
    repair_effectiveness: ImprovementDimensionStatus = "not_comparable"


class ImprovementAssessment(BaseModel):
    qa_id: str
    current_attempt_no: int = Field(ge=1)
    previous_attempt_no: Optional[int] = Field(default=None, ge=1)
    status: ImprovementStatus
    summary_zh: str
    dimensions: ImprovementDimensions = Field(default_factory=ImprovementDimensions)
    highlights: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)


class KnowledgeRepairSuggestionRequest(BaseModel):
    id: str
    suggestion: str
    knowledge_types: List[KnowledgeType]


class KRSSIssueTicketResponse(BaseModel):
    status: Literal["applied"] = "applied"
    analysis_id: str
    id: str
    knowledge_repair_request: KnowledgeRepairSuggestionRequest
    knowledge_ops_response: Optional[Dict[str, Any]] = None
    applied: bool = True


class KRSSAnalysisRecord(BaseModel):
    analysis_id: str
    ticket_id: str
    id: str
    status: Literal["applied"] = "applied"
    prompt_snapshot: str
    knowledge_repair_request: KnowledgeRepairSuggestionRequest
    knowledge_ops_response: Optional[Dict[str, Any]] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""
    used_experiments: bool = False
    primary_knowledge_type: Optional[KnowledgeType] = None
    secondary_knowledge_types: List[KnowledgeType] = Field(default_factory=list)
    candidate_patch_types: List[KnowledgeType] = Field(default_factory=list)
    validation_mode: Literal["lightweight", "disabled"] = "disabled"
    validation_result: Dict[str, Any] = Field(default_factory=dict)
    diagnosis_context_summary: Dict[str, Any] = Field(default_factory=dict)
    applied: bool = True
    created_at: str
    applied_at: str


class QAGoldenResponse(BaseModel):
    id: str
    status: EvaluationState
    issue_ticket_id: Optional[str] = None
    verdict: Optional[Verdict] = None


class EvaluationSubmissionResponse(BaseModel):
    id: str
    status: EvaluationState
    issue_ticket_id: Optional[str] = None
    verdict: Optional[Verdict] = None


class RepairPlanEnvelope(BaseModel):
    status: str
    plan: RepairPlan


class QueryGeneratorRepairReceipt(BaseModel):
    status: str
    plan_id: str
    id: str
