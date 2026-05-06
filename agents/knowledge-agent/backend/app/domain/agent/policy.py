from __future__ import annotations

from app.domain.agent.models import AgentRun, CandidateChange, ValidationSummary
from app.errors import AppError


class PolicyGuard:
    def assert_can_auto_apply(self, run: AgentRun, changes: list[CandidateChange], validation: ValidationSummary) -> None:
        if not run.constraints.auto_apply:
            raise AppError("AGENT_HUMAN_REVIEW_REQUIRED", "auto_apply is disabled for this run.")
        self._assert_common_apply_requirements(changes, validation)
        for change in changes:
            if self.requires_review(change):
                raise AppError("AGENT_HUMAN_REVIEW_REQUIRED", f"{change.doc_type} change requires human review.")

    def assert_can_apply_after_human_approval(
        self,
        run: AgentRun,
        changes: list[CandidateChange],
        validation: ValidationSummary,
    ) -> None:
        self._assert_common_apply_requirements(changes, validation)

    def requires_review(self, change: CandidateChange) -> bool:
        return change.doc_type == "system_prompt" or (change.doc_type == "cypher_syntax" and change.risk in {"medium", "high"})

    def _assert_common_apply_requirements(self, changes: list[CandidateChange], validation: ValidationSummary) -> None:
        if not changes:
            raise AppError("AGENT_NO_CANDIDATE_PATCH", "No candidate changes are available to apply.")
        if not validation.before_after_improved:
            raise AppError("AGENT_VALIDATION_NOT_IMPROVED", "Candidate patch did not improve validation signals.")
        for change in changes:
            if not change.duplicate_checked:
                raise AppError("AGENT_DUPLICATE_CHECK_REQUIRED", "Candidate patch must pass duplicate check.")
            if not change.conflict_checked:
                raise AppError("AGENT_CONFLICT_CHECK_REQUIRED", "Candidate patch must pass conflict check.")
            if change.confidence < 0.8:
                raise AppError("AGENT_CONFIDENCE_TOO_LOW", "Candidate patch confidence is below threshold.")
