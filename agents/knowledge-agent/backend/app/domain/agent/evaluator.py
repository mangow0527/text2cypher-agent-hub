from __future__ import annotations

from app.domain.agent.models import GapDiagnosis, ValidationSummary


class RepairEvaluator:
    def classify_gap(self, markdown_hits: list[dict], rag_hits: list[dict], validation_errors: list[str]) -> GapDiagnosis:
        if markdown_hits and not rag_hits:
            return GapDiagnosis(
                gap_type="retrieval_miss",
                reason="Markdown knowledge exists but RAG returned no hits.",
                suggested_action="Improve metadata/index/rerank.",
            )
        if not markdown_hits and not rag_hits:
            return GapDiagnosis(
                gap_type="knowledge_missing",
                reason="No markdown or RAG knowledge covers the failure.",
                suggested_action="Propose a knowledge patch.",
            )
        if "conflict" in validation_errors:
            return GapDiagnosis(
                gap_type="knowledge_conflict",
                reason="Validation reported conflict.",
                suggested_action="Request human review.",
            )
        return GapDiagnosis(
            gap_type="generator_noncompliance",
            reason="Knowledge appears available but generation did not comply.",
            suggested_action="Strengthen few-shot, anti-pattern, or validator.",
        )

    def evaluate_prompt_delta(self, before_prompt: str, after_prompt: str, expected_terms: list[str]) -> ValidationSummary:
        improved = bool(after_prompt) and after_prompt != before_prompt and all(term in after_prompt for term in expected_terms)
        return ValidationSummary(
            prompt_package_built=bool(after_prompt),
            before_after_improved=improved,
            remaining_risks=[] if improved else ["expected terms missing"],
        )
