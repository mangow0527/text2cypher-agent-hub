from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from .models import EvaluationDimensions, EvaluationSummary, TuGraphExecutionResult, Verdict
from .schema_profile import NETWORK_SCHEMA_V10_CONTEXT

LABEL_PATTERN = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
REL_PATTERN = re.compile(r"\[:([A-Za-z_][A-Za-z0-9_]*)")
VALID_LABELS = {"NetworkElement", "Protocol", "Tunnel", "Service", "Port", "Fiber", "Link"}
VALID_RELATIONS = {
    "HAS_PORT",
    "FIBER_SRC",
    "FIBER_DST",
    "LINK_SRC",
    "LINK_DST",
    "TUNNEL_SRC",
    "TUNNEL_DST",
    "TUNNEL_PROTO",
    "PATH_THROUGH",
    "SERVICE_USES_TUNNEL",
}
AMBIGUOUS_TOKENS = ["随便", "看看", "情况", "这个", "那个", "帮我看看"]


def normalize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: normalize_json(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [normalize_json(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(normalize_json(value), ensure_ascii=False, sort_keys=True)


def extract_labels(cypher: str) -> List[str]:
    return sorted(set(LABEL_PATTERN.findall(cypher or "")))


def extract_relations(cypher: str) -> List[str]:
    return sorted(set(REL_PATTERN.findall(cypher or "")))


def compare_answer(expected_answer: Any, execution: TuGraphExecutionResult) -> Tuple[bool, str]:
    actual_rows = execution.rows
    if isinstance(expected_answer, list):
        expected_canonical = _canonical_json(expected_answer)
        actual_canonical = _canonical_json(actual_rows)
        return expected_canonical == actual_canonical, f"expected_rows={expected_canonical}; actual_rows={actual_canonical}"

    if isinstance(expected_answer, dict):
        actual_payload = {"rows": actual_rows, "row_count": execution.row_count}
        expected_canonical = _canonical_json(expected_answer)
        actual_canonical = _canonical_json(actual_payload)
        if expected_canonical == actual_canonical:
            return True, f"expected={expected_canonical}; actual={actual_canonical}"
        actual_only_rows = _canonical_json(actual_rows)
        if expected_canonical == actual_only_rows:
            return True, f"expected={expected_canonical}; actual_rows={actual_only_rows}"
        return False, f"expected={expected_canonical}; actual={actual_canonical}"

    expected_canonical = _canonical_json(expected_answer)
    actual_canonical = _canonical_json(actual_rows)
    return expected_canonical == actual_canonical, f"expected={expected_canonical}; actual_rows={actual_canonical}"


def evaluate_submission(
    question: str,
    expected_cypher: str,
    expected_answer: Any,
    actual_cypher: str,
    execution: TuGraphExecutionResult,
    loaded_knowledge_tags: List[str],
) -> EvaluationSummary:
    evidence: List[str] = []
    actual_labels = extract_labels(actual_cypher)
    actual_relations = extract_relations(actual_cypher)
    expected_labels = extract_labels(expected_cypher)
    expected_relations = extract_relations(expected_cypher)

    syntax_validity = "pass" if execution.success and not _contains_syntax_error(execution.error_message) else "fail"
    if syntax_validity == "fail":
        evidence.append(f"Execution failed or syntax invalid: {execution.error_message or 'unknown syntax issue'}")

    schema_alignment = "pass"
    if any(label not in VALID_LABELS for label in actual_labels) or any(rel not in VALID_RELATIONS for rel in actual_relations):
        schema_alignment = "fail"
        evidence.append(
            "Actual Cypher contains labels or relations outside network_schema_v10: "
            f"labels={actual_labels}, relations={actual_relations}"
        )
    elif _contains_schema_error(execution.error_message):
        schema_alignment = "fail"
        evidence.append(f"Execution reported schema error: {execution.error_message}")

    result_correctness, result_detail = compare_answer(expected_answer, execution)
    result_status = "pass" if result_correctness else "fail"
    if result_status == "fail":
        evidence.append(f"Result mismatch: {result_detail}")

    question_alignment = "pass"
    label_overlap = _overlap_ratio(actual_labels, expected_labels)
    relation_overlap = _overlap_ratio(actual_relations, expected_relations)
    if any(token in question.lower() for token in AMBIGUOUS_TOKENS):
        question_alignment = "fail"
        evidence.append("Question contains ambiguous wording and lacks clear entity constraints.")
    elif label_overlap < 0.5 and expected_labels:
        question_alignment = "fail"
        evidence.append(
            f"Actual vs expected label overlap too low: actual={actual_labels}, expected={expected_labels}"
        )
    elif relation_overlap < 0.5 and expected_relations:
        question_alignment = "fail"
        evidence.append(
            f"Actual vs expected relation overlap too low: actual={actual_relations}, expected={expected_relations}"
        )

    dimensions = EvaluationDimensions(
        syntax_validity=syntax_validity,
        schema_alignment=schema_alignment,
        result_correctness=result_status,
        question_alignment=question_alignment,
    )

    failures = [
        dimensions.syntax_validity,
        dimensions.schema_alignment,
        dimensions.result_correctness,
        dimensions.question_alignment,
    ].count("fail")

    verdict: Verdict
    if failures == 0:
        verdict = "pass"
    elif failures == 4 or dimensions.syntax_validity == "fail":
        verdict = "fail"
    else:
        verdict = "partial_fail"

    symptom = _build_symptom(verdict, dimensions, loaded_knowledge_tags)
    if not evidence:
        evidence.append(f"Schema context used: {NETWORK_SCHEMA_V10_CONTEXT}")

    return EvaluationSummary(
        verdict=verdict,
        dimensions=dimensions,
        symptom=symptom,
        evidence=evidence,
    )


def _contains_syntax_error(error_message: str | None) -> bool:
    return bool(error_message and "syntax" in error_message.lower())


def _contains_schema_error(error_message: str | None) -> bool:
    lowered = (error_message or "").lower()
    return "schema" in lowered or "label" in lowered or "property" in lowered


def _overlap_ratio(left: List[str], right: List[str]) -> float:
    if not right:
        return 1.0
    left_set = set(left)
    right_set = set(right)
    return len(left_set & right_set) / max(1, len(right_set))


def _build_symptom(verdict: Verdict, dimensions: EvaluationDimensions, loaded_tags: List[str]) -> str:
    if verdict == "pass":
        return "Generated query is structurally aligned with the golden reference and returned the expected data."
    if dimensions.syntax_validity == "fail":
        return "Generated Cypher is not executable or has syntax issues."
    if dimensions.schema_alignment == "fail":
        return "Generated Cypher is not aligned with the graph schema."
    if dimensions.result_correctness == "fail" and dimensions.question_alignment == "pass":
        return "Generated Cypher is plausible but returned data inconsistent with the golden answer."
    if dimensions.question_alignment == "fail":
        return (
            "Question, generated Cypher, and golden intent are not semantically aligned; "
            f"loaded knowledge tags were {loaded_tags}."
        )
    return "Multiple quality dimensions failed and require deeper diagnosis."
