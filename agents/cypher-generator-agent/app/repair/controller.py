from __future__ import annotations

from typing import Any

from .models import (
    ClarificationOption,
    ClarificationQuestion,
    RepairAssumption,
    RepairControllerInput,
    RepairDecision,
    RepairIssue,
)
from .notices import render_user_visible_notices


REPAIRABLE_CODES = frozenset(
    {
        "edge_endpoint_mismatch",
        "edge_direction_mismatch",
        "property_owner_mismatch",
        "metric_dimension_invalid",
        "metric_group_by_invalid",
        "binding_plan_incomplete",
    }
)
STATIC_FAILURE_CODES = frozenset(
    {
        "cypher_syntax_invalid",
        "cypher_readonly_violation",
        "cypher_schema_reference_invalid",
        "compiler_shape_mismatch",
        "target_dialect_static_error",
    }
)


class RepairController:
    def __init__(
        self,
        *,
        max_repair_attempts: int = 3,
        ambiguous_top2_gap_threshold: float = 0.10,
        max_clarification_options: int = 3,
    ) -> None:
        self.max_repair_attempts = max_repair_attempts
        self.ambiguous_top2_gap_threshold = ambiguous_top2_gap_threshold
        self.max_clarification_options = max_clarification_options

    def decide(self, payload: RepairControllerInput | dict[str, Any]) -> RepairDecision:
        controller_input = (
            payload if isinstance(payload, RepairControllerInput) else RepairControllerInput.model_validate(payload)
        )
        issue = _first_issue(controller_input)
        if issue is None:
            return self._continue_with_input_assumptions(controller_input)

        if issue.code in STATIC_FAILURE_CODES:
            return _failure(issue.code)

        if issue.code == "unsupported_query_shape" or issue.action == "unsupported_query_shape":
            return RepairDecision(decision="unsupported", reason_code="unsupported_query_shape")

        if issue.code in {"high_confidence_fuzzy_literal", "modality_warning"} or issue.action == "continue_with_assumption":
            assumption = _assumption_from_issue(issue)
            assumptions = [assumption] if assumption is not None else [*controller_input.assumptions]
            return _continue_with_assumption(issue.code, assumptions)

        if _is_ambiguous(issue, self.ambiguous_top2_gap_threshold):
            return _ask_user(issue, self.max_clarification_options)

        if issue.code in {"coverage_failure", "literal_unresolved", "literal_ambiguous"} or issue.action == "ask_user":
            return _ask_user(issue, self.max_clarification_options)

        if _is_repairable(issue):
            return _failure(issue.code, stop_reason=issue.code)

        return _failure(issue.code)

    def _continue_with_input_assumptions(self, controller_input: RepairControllerInput) -> RepairDecision:
        if not controller_input.assumptions:
            return RepairDecision(decision="continue_with_assumption", reason_code="no_errors")
        return _continue_with_assumption("input_assumptions", controller_input.assumptions)


def _first_issue(controller_input: RepairControllerInput) -> RepairIssue | None:
    if controller_input.validator_errors:
        return controller_input.validator_errors[0]
    if controller_input.cypher_validation_errors:
        return controller_input.cypher_validation_errors[0]
    return None


def _is_repairable(issue: RepairIssue) -> bool:
    if issue.repairable is True:
        return True
    if issue.action == "repair_binding":
        return True
    return issue.code in REPAIRABLE_CODES


def _is_ambiguous(issue: RepairIssue, threshold: float) -> bool:
    candidates = _candidate_options(issue)
    if len(candidates) < 2:
        return issue.code.startswith("ambiguous")
    first = _confidence(candidates[0])
    second = _confidence(candidates[1])
    return issue.code.startswith("ambiguous") or abs(first - second) < threshold


def _ask_user(issue: RepairIssue, max_options: int) -> RepairDecision:
    options = [
        ClarificationOption.model_validate(option)
        for option in _candidate_options(issue)[:max_options]
    ]
    question = _clarification_question(issue)
    return RepairDecision(
        decision="ask_user",
        reason_code=issue.code,
        clarification=ClarificationQuestion(
            source_stage="semantic_validator",
            reason_code=issue.code,
            question=question,
            question_zh=question,
            expected_answer_type="single_choice" if options else "free_text",
            options=options,
        ),
    )


def _clarification_question(issue: RepairIssue) -> str:
    term = issue.details.get("term") or issue.details.get("literal") or "这个表达"
    if issue.code.startswith("ambiguous"):
        return f"你说的“{term}”具体指哪一个？"
    if issue.code == "coverage_failure":
        terms = issue.details.get("substantive_uncovered") or issue.details.get("terms") or [term]
        return f"问题中的“{terms[0]}”没有在当前语义模型中找到对应含义，请补充或改写。"
    if issue.code.startswith("literal"):
        return f"我没有确定“{term}”对应的值，请选择或补充。"
    return issue.message or "请补充一个澄清信息。"


def _candidate_options(issue: RepairIssue) -> list[dict[str, Any]]:
    candidates = issue.details.get("candidates") or issue.details.get("alternatives") or []
    options = [_clarification_option_payload(candidate) for candidate in candidates if isinstance(candidate, dict)]
    return sorted(options, key=_confidence, reverse=True)


def _clarification_option_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    if "id" in candidate and "label" in candidate:
        return {
            key: value
            for key, value in candidate.items()
            if key in {"id", "label", "vertex_name", "confidence", "value"}
        }
    value = candidate.get("value")
    display = candidate.get("display") or value
    return {
        "id": str(value if value is not None else display),
        "label": str(display),
        "value": value,
        "confidence": candidate.get("confidence"),
    }


def _confidence(candidate: dict[str, Any]) -> float:
    value = candidate.get("confidence", 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _assumption_from_issue(issue: RepairIssue) -> RepairAssumption | None:
    payload = issue.details.get("assumption")
    if isinstance(payload, RepairAssumption):
        return payload
    if isinstance(payload, dict):
        return RepairAssumption.model_validate(payload)
    if issue.code == "modality_warning":
        return RepairAssumption(kind="modality_warning", term=issue.details.get("term"))
    return None


def _continue_with_assumption(reason_code: str, assumptions: list[RepairAssumption | dict]) -> RepairDecision:
    models = [
        assumption if isinstance(assumption, RepairAssumption) else RepairAssumption.model_validate(assumption)
        for assumption in assumptions
    ]
    return RepairDecision(
        decision="continue_with_assumption",
        reason_code=reason_code,
        assumptions=models,
        derived_user_visible_notices=render_user_visible_notices(models),
    )


def _failure(reason_code: str, *, stop_reason: str | None = None) -> RepairDecision:
    return RepairDecision(
        decision="generation_failed",
        reason_code=reason_code,
        stop_reason=stop_reason,
    )
