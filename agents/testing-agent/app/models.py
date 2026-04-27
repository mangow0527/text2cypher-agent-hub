from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


Difficulty = Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]
Verdict = Literal["pass", "fail"]
EvaluationState = Literal[
    "received_golden_only",
    "received_submission_only",
    "ready_to_evaluate",
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


class SubmissionReceipt(BaseModel):
    accepted: bool


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
    state: EvaluationState
    execution: Optional[ExecutionResult] = None
    evaluation: Optional[EvaluationSummary] = None
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
    issue_ticket: Optional[Dict[str, Any]] = None
