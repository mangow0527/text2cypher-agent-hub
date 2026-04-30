from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


Difficulty = Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]
Verdict = Literal["pass", "fail"]
EvaluationState = Literal[
    "received_golden_only",
    "received_submission_only",
    "ready_to_evaluate",
    "tugraph_execution_failed",
    "semantic_review_invalid",
    "repair_pending",
    "repair_submission_failed",
    "issue_ticket_created",
    "passed",
]
StrictCheckStatus = Literal["pass", "fail", "not_run"]
SemanticCheckStatus = Literal["pass", "fail", "not_run"]
ExecutionAccuracyReason = Literal[
    "strict_equal",
    "semantic_equivalent",
    "grammar_failed",
    "execution_failed",
    "not_equivalent",
]
ImprovementStatus = Literal["improved", "regressed", "unchanged"]
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
GenerationReportStatus = Literal["generation_failed", "service_failed"]
SubmissionGenerationStatus = Literal["generated", "generation_failed"]


class QAGoldenRequest(BaseModel):
    id: str
    cypher: str
    answer: Any
    difficulty: Difficulty


class QAGoldenResponse(BaseModel):
    id: str
    status: EvaluationState
    verdict: Optional[Verdict] = None
    issue_ticket_id: Optional[str] = None


class GeneratedCypherSubmissionRequest(BaseModel):
    id: str
    question: str
    generation_run_id: str
    generated_cypher: str
    input_prompt_snapshot: str
    last_llm_raw_output: str
    generation_retry_count: int = Field(default=0, ge=0)
    generation_failure_reasons: List[GenerationFailureReason] = Field(default_factory=list)


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
    generation_failure_reasons: List[GenerationFailureReason] = Field(default_factory=list)
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


class SubmissionReceipt(BaseModel):
    accepted: bool


class RepairAgentKnowledgeRequest(BaseModel):
    id: str
    suggestion: str
    knowledge_types: List[str]


class RepairAgentResponse(BaseModel):
    status: str
    analysis_id: str
    id: str
    knowledge_repair_request: Optional[RepairAgentKnowledgeRequest] = None
    knowledge_agent_response: Optional[Dict[str, Any]] = None
    applied: bool = True


class ExecutionResult(BaseModel):
    success: bool
    rows: Optional[List[Dict[str, Any]]] = None
    row_count: Optional[int] = None
    error_message: Optional[str] = None
    elapsed_ms: int = 0


class GrammarMetric(BaseModel):
    score: int = Field(ge=0, le=1)
    parser_error: Optional[str] = None
    message: Optional[str] = None


class StrictDiff(BaseModel):
    missing_rows: List[Any] = Field(default_factory=list)
    unexpected_rows: List[Any] = Field(default_factory=list)
    order_mismatch: Optional[bool] = None


class StrictEvidence(BaseModel):
    golden_answer: List[Any]
    actual_answer: List[Any]
    diff: StrictDiff


class StrictCheck(BaseModel):
    status: StrictCheckStatus
    message: Optional[str] = None
    order_sensitive: bool = False
    expected_row_count: int = 0
    actual_row_count: int = 0
    evidence: Optional[StrictEvidence] = None


class SemanticCheck(BaseModel):
    status: SemanticCheckStatus
    message: Optional[str] = None
    raw_output: Optional[Dict[str, Any]] = None


class SemanticReviewArtifact(BaseModel):
    status: Literal["accepted", "invalid"]
    raw_text: str
    payload: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None
    model: Optional[str] = None
    prompt_snapshot: Optional[str] = None
    normalized_judgement: Optional[Literal["pass", "fail"]] = None
    reasoning: Optional[str] = None
    message: Optional[str] = None


class ExecutionAccuracy(BaseModel):
    score: int = Field(ge=0, le=1)
    reason: ExecutionAccuracyReason
    strict_check: StrictCheck
    semantic_check: SemanticCheck


class GLEUSignal(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    tokenizer: str
    min_n: int
    max_n: int


class JaroWinklerSimilaritySignal(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    normalization: str
    library: str


class SecondarySignals(BaseModel):
    gleu: GLEUSignal
    jaro_winkler_similarity: JaroWinklerSimilaritySignal


class PrimaryMetrics(BaseModel):
    grammar: GrammarMetric
    execution_accuracy: ExecutionAccuracy


class EvaluationSummary(BaseModel):
    verdict: Verdict
    primary_metrics: PrimaryMetrics
    secondary_signals: SecondarySignals


class ExpectedPayload(BaseModel):
    cypher: str
    answer: Any


class ActualPayload(BaseModel):
    generated_cypher: str
    execution: Optional[ExecutionResult] = None


class GenerationEvidence(BaseModel):
    generation_run_id: str
    attempt_no: int = Field(ge=1)
    input_prompt_snapshot: str
    last_llm_raw_output: str = ""
    generation_status: SubmissionGenerationStatus = "generated"
    failure_reason: Optional[GenerationFailureReason | ServiceFailureReason] = None
    generation_retry_count: int = Field(default=0, ge=0)
    generation_failure_reasons: List[GenerationFailureReason] = Field(default_factory=list)


class IssueTicket(BaseModel):
    ticket_id: str
    id: str
    difficulty: Difficulty
    question: str
    expected: ExpectedPayload
    actual: ActualPayload
    evaluation: EvaluationSummary
    generation_evidence: GenerationEvidence


class ImprovementMetricChange(BaseModel):
    previous: Any
    current: Any
    status: ImprovementStatus


class ImprovementMetrics(BaseModel):
    grammar_score: ImprovementMetricChange
    execution_accuracy_score: ImprovementMetricChange
    gleu_score: ImprovementMetricChange
    jaro_winkler_similarity_score: ImprovementMetricChange


class ImprovementAssessment(BaseModel):
    qa_id: str
    current_attempt_no: int = Field(ge=1)
    previous_attempt_no: Optional[int] = Field(default=None, ge=1)
    summary_zh: str
    metrics: ImprovementMetrics
    highlights: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)


class SubmissionRecord(BaseModel):
    id: str
    attempt_no: int
    question: str
    generation_run_id: str
    generated_cypher: str
    input_prompt_snapshot: str
    last_llm_raw_output: str = ""
    generation_status: SubmissionGenerationStatus = "generated"
    failure_reason: Optional[GenerationFailureReason | ServiceFailureReason] = None
    generation_retry_count: int = Field(default=0, ge=0)
    generation_failure_reasons: List[GenerationFailureReason] = Field(default_factory=list)
    state: EvaluationState
    execution: Optional[ExecutionResult] = None
    evaluation: Optional[EvaluationSummary] = None
    semantic_review: Optional[SemanticReviewArtifact] = None
    issue_ticket_id: Optional[str] = None
    repair_response: Optional[Dict[str, Any]] = None
    improvement_assessment: Optional[ImprovementAssessment] = None
    received_at: str
    updated_at: str


class SaveSubmissionResult(BaseModel):
    created: bool
    attempt_no: int
    record: SubmissionRecord


class EvaluationStatusResponse(BaseModel):
    id: str
    golden: Optional[Dict[str, Any]] = None
    submission: Optional[Dict[str, Any]] = None
    attempts: List[Dict[str, Any]] = Field(default_factory=list)
    generation_failures: List[Dict[str, Any]] = Field(default_factory=list)
    issue_ticket: Optional[Dict[str, Any]] = None
