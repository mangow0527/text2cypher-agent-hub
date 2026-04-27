from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, cast

from .models import IssueTicket, KnowledgeRepairSuggestionRequest, KnowledgeType


DiagnosisContext = Dict[str, Any]


class RepairDiagnosisClient(Protocol):
    async def diagnose(self, context: DiagnosisContext) -> Dict[str, Any]:
        ...


ExperimentRunner = Callable[[IssueTicket, DiagnosisContext, KnowledgeType, Dict[str, Any]], Awaitable[Dict[str, Any]]]

_DEFAULT_KNOWLEDGE_TYPES: List[KnowledgeType] = ["system_prompt"]
_ALLOWED_KNOWLEDGE_TYPES = frozenset({"cypher_syntax", "few_shot", "system_prompt", "business_knowledge"})


@dataclass(slots=True)
class RepairAnalysisResult:
    id: str
    suggestion: str
    knowledge_types: List[KnowledgeType]
    confidence: float
    rationale: str = ""
    used_experiments: bool = False
    primary_knowledge_type: KnowledgeType = "system_prompt"
    secondary_knowledge_types: List[KnowledgeType] = field(default_factory=list)
    candidate_patch_types: List[KnowledgeType] = field(default_factory=list)
    validation_mode: str = "disabled"
    validation_result: Dict[str, Any] = field(default_factory=dict)
    diagnosis_context_summary: Dict[str, Any] = field(default_factory=dict)

    def to_request(self) -> KnowledgeRepairSuggestionRequest:
        return KnowledgeRepairSuggestionRequest(
            id=self.id,
            suggestion=self.suggestion,
            knowledge_types=self.knowledge_types or list(_DEFAULT_KNOWLEDGE_TYPES),
        )


def build_diagnosis_context(
    ticket: IssueTicket,
    prompt_snapshot: str,
    *,
    recent_applied_repairs: Optional[List[Dict[str, Any]]] = None,
) -> DiagnosisContext:
    return {
        "ticket_id": ticket.ticket_id,
        "id": ticket.id,
        "question": ticket.question,
        "difficulty": ticket.difficulty,
        "sql_pair": {
            "expected_cypher": ticket.expected.cypher,
            "actual_cypher": ticket.actual.generated_cypher,
        },
        "evaluation_summary": {
            "verdict": ticket.evaluation.verdict,
            "primary_metrics": ticket.evaluation.primary_metrics.model_dump(mode="json"),
            "secondary_signals": ticket.evaluation.secondary_signals.model_dump(mode="json"),
        },
        "failure_diff": _build_failure_diff(ticket),
        "prompt_evidence": _build_prompt_evidence(prompt_snapshot),
        "generation_evidence": _build_generation_evidence(ticket, prompt_snapshot),
        "relevant_prompt_fragments": _extract_relevant_prompt_fragments(prompt_snapshot),
        "recent_applied_repairs": recent_applied_repairs or [],
    }


class RepairAnalyzer:
    def __init__(
        self,
        diagnosis_client: RepairDiagnosisClient,
        *,
        min_confidence_for_direct_return: float = 0.8,
        experiment_runner: Optional[ExperimentRunner] = None,
    ) -> None:
        self.diagnosis_client = diagnosis_client
        self.min_confidence_for_direct_return = min_confidence_for_direct_return
        self.experiment_runner = experiment_runner

    async def analyze(self, ticket: IssueTicket, prompt_snapshot: str) -> RepairAnalysisResult:
        context = build_diagnosis_context(ticket, prompt_snapshot)
        diagnosis = await self.diagnosis_client.diagnose(context)

        primary_knowledge_type = self._coerce_primary_knowledge_type(diagnosis.get("primary_knowledge_type"))
        secondary_knowledge_types = self._coerce_knowledge_types(diagnosis.get("secondary_knowledge_types"))
        suggestion = str(diagnosis.get("suggestion") or diagnosis.get("rationale") or "Review and repair the missing knowledge.")
        rationale = str(diagnosis.get("rationale") or "")
        confidence = self._coerce_confidence(diagnosis.get("confidence"))
        candidate_patch_types = self._coerce_knowledge_types(diagnosis.get("candidate_patch_types"))
        need_validation = bool(diagnosis.get("need_validation"))

        if not need_validation or not candidate_patch_types or self.experiment_runner is None:
            return RepairAnalysisResult(
                id=ticket.id,
                suggestion=suggestion,
                knowledge_types=[primary_knowledge_type],
                confidence=confidence,
                rationale=rationale,
                used_experiments=False,
                primary_knowledge_type=primary_knowledge_type,
                secondary_knowledge_types=secondary_knowledge_types,
                candidate_patch_types=candidate_patch_types,
                validation_mode="disabled",
                validation_result={
                    "validated_patch_types": [],
                    "rejected_patch_types": candidate_patch_types,
                    "validation_reasoning": ["validation disabled"],
                },
                diagnosis_context_summary=self._context_summary(context),
            )

        validated_patch_types: List[KnowledgeType] = []
        rejected_patch_types: List[KnowledgeType] = []
        validation_reasoning: List[str] = []
        best_patch_types: List[KnowledgeType] = []
        best_metric: Optional[float] = None
        best_confidence = confidence
        best_suggestion = suggestion

        for patch_type in candidate_patch_types[:2]:
            experiment_result = await self.experiment_runner(ticket, context, patch_type, diagnosis)
            if self._is_improved(experiment_result):
                validated_patch_types.append(patch_type)
                validation_reasoning.append(str(experiment_result.get("reason") or f"{patch_type} improved the diagnosis"))
                patch_metric = self._coerce_patch_metric(experiment_result, fallback=confidence)
                if best_metric is None or patch_metric > best_metric:
                    best_patch_types = [patch_type]
                    best_metric = patch_metric
                elif patch_metric == best_metric:
                    best_patch_types.append(patch_type)
                best_confidence = max(best_confidence, self._coerce_confidence(experiment_result.get("confidence"), fallback=best_confidence))
                if experiment_result.get("suggestion"):
                    best_suggestion = str(experiment_result["suggestion"])
            else:
                rejected_patch_types.append(patch_type)
                validation_reasoning.append(str(experiment_result.get("reason") or f"{patch_type} did not improve the diagnosis"))

        selected_knowledge_types = best_patch_types or [primary_knowledge_type]
        return RepairAnalysisResult(
            id=ticket.id,
            suggestion=best_suggestion,
            knowledge_types=selected_knowledge_types,
            confidence=best_confidence,
            rationale=rationale,
            used_experiments=True,
            primary_knowledge_type=primary_knowledge_type,
            secondary_knowledge_types=secondary_knowledge_types,
            candidate_patch_types=candidate_patch_types,
            validation_mode="lightweight",
            validation_result={
                "validated_patch_types": validated_patch_types,
                "rejected_patch_types": rejected_patch_types,
                "validation_reasoning": validation_reasoning,
            },
            diagnosis_context_summary=self._context_summary(context),
        )

    def _coerce_primary_knowledge_type(self, raw_value: Any) -> KnowledgeType:
        if isinstance(raw_value, str) and raw_value in _ALLOWED_KNOWLEDGE_TYPES:
            return cast(KnowledgeType, raw_value)
        return "system_prompt"

    def _coerce_knowledge_types(self, raw_value: Any) -> List[KnowledgeType]:
        if not isinstance(raw_value, list):
            return []
        knowledge_types: List[KnowledgeType] = []
        for item in raw_value:
            if isinstance(item, str) and item in _ALLOWED_KNOWLEDGE_TYPES and item not in knowledge_types:
                knowledge_types.append(cast(KnowledgeType, item))
        return knowledge_types

    def _coerce_confidence(self, raw_value: Any, *, fallback: float = 0.0) -> float:
        try:
            confidence = float(raw_value)
        except (TypeError, ValueError):
            return fallback
        if not math.isfinite(confidence):
            return fallback
        return min(1.0, max(0.0, confidence))

    def _coerce_patch_metric(self, result: Dict[str, Any], *, fallback: float) -> float:
        confidence = self._coerce_confidence(result.get("confidence"), fallback=-1.0)
        if confidence >= 0.0:
            return confidence
        try:
            score = float(result.get("score"))
        except (TypeError, ValueError):
            return fallback
        if not math.isfinite(score):
            return fallback
        return min(1.0, max(0.0, score))

    def _is_improved(self, result: Dict[str, Any]) -> bool:
        improved = result.get("improved")
        if isinstance(improved, bool):
            return improved
        score_delta = result.get("score_delta")
        try:
            return float(score_delta) > 0.0
        except (TypeError, ValueError):
            return False

    def _context_summary(self, context: DiagnosisContext) -> Dict[str, Any]:
        return {
            "failure_diff": context["failure_diff"],
            "recent_applied_repairs_count": len(context["recent_applied_repairs"]),
            "fragment_lengths": {
                key: len(value)
                for key, value in context["relevant_prompt_fragments"].items()
                if isinstance(value, str)
            },
            "prompt_evidence_length": len(str(context.get("prompt_evidence") or "")),
        }


def _build_failure_diff(ticket: IssueTicket) -> Dict[str, Any]:
    expected = ticket.expected.cypher.lower()
    actual = ticket.actual.generated_cypher.lower()
    question = ticket.question.lower()
    grammar = ticket.evaluation.primary_metrics.grammar
    execution_accuracy = ticket.evaluation.primary_metrics.execution_accuracy
    semantic_check = execution_accuracy.semantic_check
    strict_check = execution_accuracy.strict_check
    similarity = ticket.evaluation.secondary_signals.jaro_winkler_similarity.score
    execution = ticket.actual.execution

    ordering_problem = ("order by" in expected) and ("order by" not in actual)
    limit_problem = ("limit" in expected) and ("limit" not in actual or _extract_limit(expected) != _extract_limit(actual))
    return_shape_problem = (" return " in expected and " return " in actual) and _return_shape(expected) != _return_shape(actual)
    syntax_problem = grammar.score == 0 or bool(grammar.parser_error)
    execution_problem = bool(execution and ((execution.success is False) or execution.error_message))
    entity_or_relation_problem = (
        execution_accuracy.score == 0
        and (
            strict_check.status == "fail"
            or semantic_check.status == "fail"
            or similarity < 0.9
            or _mentions_relation(question, expected, actual)
        )
    )

    missing_or_wrong_clauses: List[str] = []
    if ordering_problem:
        missing_or_wrong_clauses.append("ordering")
    if limit_problem:
        missing_or_wrong_clauses.append("limit")
    if return_shape_problem:
        missing_or_wrong_clauses.append("return_shape")
    if entity_or_relation_problem:
        missing_or_wrong_clauses.append("entity_or_relation")
    if syntax_problem:
        missing_or_wrong_clauses.append("syntax")
    if execution_problem:
        missing_or_wrong_clauses.append("execution")

    semantic_mismatch_summary = (
        strict_check.message
        or semantic_check.message
        or execution_accuracy.reason
    )
    return {
        "ordering_problem": ordering_problem,
        "limit_problem": limit_problem,
        "return_shape_problem": return_shape_problem,
        "entity_or_relation_problem": entity_or_relation_problem,
        "execution_problem": execution_problem,
        "syntax_problem": syntax_problem,
        "missing_or_wrong_clauses": missing_or_wrong_clauses,
        "semantic_mismatch_summary": semantic_mismatch_summary,
    }


def _extract_relevant_prompt_fragments(prompt_snapshot: str) -> Dict[str, str]:
    fragments = {
        "system_rules_fragment": "",
        "business_knowledge_fragment": "",
        "few_shot_fragment": "",
        "recent_repair_fragment": "",
    }
    for raw_line in prompt_snapshot.splitlines():
        line = raw_line.strip()
        lower_line = line.lower()
        if not line or "appendix:" in lower_line:
            continue
        if "system" in lower_line and not fragments["system_rules_fragment"]:
            fragments["system_rules_fragment"] = _append_fragment(fragments["system_rules_fragment"], line, 300)
        elif "business" in lower_line and not fragments["business_knowledge_fragment"]:
            fragments["business_knowledge_fragment"] = _append_fragment(fragments["business_knowledge_fragment"], line, 300)
        elif "few-shot" in lower_line or "few_shot" in lower_line:
            fragments["few_shot_fragment"] = _append_fragment(fragments["few_shot_fragment"], line, 450)
        elif "repair" in lower_line:
            fragments["recent_repair_fragment"] = _append_fragment(fragments["recent_repair_fragment"], line, 300)
    return fragments


def _build_prompt_evidence(prompt_snapshot: str, max_chars: int = 1200) -> str:
    compact_lines: List[str] = []
    seen_lines: set[str] = set()
    for raw_line in prompt_snapshot.strip().splitlines():
        line = raw_line.rstrip()
        normalized = line.strip()
        if not normalized or "appendix:" in normalized.lower():
            continue
        if normalized in seen_lines:
            continue
        seen_lines.add(normalized)
        compact_lines.append(line)
    compacted = "\n".join(compact_lines)
    if len(compacted) <= max_chars:
        return compacted
    marker = "\n...[prompt truncated]...\n"
    head_budget = max_chars // 2
    tail_budget = max_chars - head_budget - len(marker)
    return compacted[:head_budget].rstrip() + marker + compacted[-tail_budget:].lstrip()


def _build_generation_evidence(ticket: IssueTicket, prompt_snapshot: str) -> Dict[str, Any]:
    payload = ticket.generation_evidence.model_dump(mode="json")
    payload["input_prompt_snapshot"] = _build_prompt_evidence(prompt_snapshot, max_chars=600)
    return payload


def _append_fragment(existing: str, line: str, max_chars: int) -> str:
    if not existing:
        return line[:max_chars]
    candidate = f"{existing}\n{line}"
    return candidate[:max_chars]


def _extract_limit(cypher: str) -> Optional[str]:
    parts = cypher.split("limit", 1)
    if len(parts) < 2:
        return None
    return parts[1].strip().split()[0]


def _return_shape(cypher: str) -> str:
    parts = cypher.split(" return ", 1)
    if len(parts) < 2:
        return ""
    clause = parts[1].split(" order by ", 1)[0].split(" limit ", 1)[0].strip()
    return clause


def _mentions_relation(question: str, expected: str, actual: str) -> bool:
    tokens = {"service", "tunnel", "protocol", "link", "fiber"}
    mentioned = [token for token in tokens if token in question or f":{token}" in expected]
    return any(token in expected and token not in actual for token in mentioned)
