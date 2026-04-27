"""Compatibility exports for cross-service contract tests.

Production service code should use service-local models.
"""

from services.cypher_generator_agent.app.models import (
    GeneratedCypherSubmissionRequest,
    GenerationRunResult,
    PreflightCheck,
    QAQuestionRequest,
)
from services.repair_agent.app.models import KnowledgeRepairSuggestionRequest, KnowledgeType, RepairAnalysisRecord, RepairIssueTicketResponse
from services.testing_agent.app.models import (
    Difficulty,
    EvaluationState,
    EvaluationSummary,
    GenerationEvidence,
    GeneratedCypherSubmissionRequest,
    ImprovementAssessment,
    IssueTicket,
    QAGoldenRequest,
    QAGoldenResponse,
    ExecutionResult,
    Verdict,
)
__all__ = [
    "Difficulty",
    "EvaluationState",
    "EvaluationSummary",
    "GenerationEvidence",
    "ImprovementAssessment",
    "IssueTicket",
    "KnowledgeRepairSuggestionRequest",
    "KnowledgeType",
    "GeneratedCypherSubmissionRequest",
    "GenerationRunResult",
    "PreflightCheck",
    "QAGoldenRequest",
    "QAGoldenResponse",
    "QAQuestionRequest",
    "RepairAnalysisRecord",
    "RepairIssueTicketResponse",
    "ExecutionResult",
    "Verdict",
]
