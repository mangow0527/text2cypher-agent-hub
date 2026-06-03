from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from services.cypher_generator_agent.app.core.pipeline import run_pipeline
from services.cypher_generator_agent.app.dsl.parser import parse_restricted_query_dsl
from services.cypher_generator_agent.app.repair.controller import RepairController
from services.cypher_generator_agent.app.semantic_model.loader import load_graph_semantic_model


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN_QUESTIONS_PATH = FIXTURE_DIR / "golden_questions.yaml"
MODEL_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "semantic_model"
    / "artifacts"
    / "tugraph_network_semantic_model.yaml"
)

FULL_REGRESSION_QUERY_SHAPES = {
    "vertex_lookup",
    "single_hop_traversal",
    "named_path_pattern",
    "variable_path_traversal",
    "metric_aggregate",
    "ad_hoc_aggregate",
    "top_n",
    "two_step_aggregate",
}
REGRESSION_SCOPES = {"smoke", "full"}
PROJECTION_REGRESSION_CASE_IDS = {"gq-031", "gq-032", "gq-033"}


def _all_cases() -> list[dict[str, Any]]:
    return list(_load_yaml(GOLDEN_QUESTIONS_PATH)["golden_questions"])


def _regression_cases() -> list[dict[str, Any]]:
    return [
        case
        for case in _all_cases()
        if case.get("regression_scope") in REGRESSION_SCOPES
    ]


def _smoke_regression_cases() -> list[dict[str, Any]]:
    return [
        case
        for case in _all_cases()
        if case.get("regression_scope") == "smoke"
    ]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _read_text_fixture(path: Path) -> str:
    return path.read_text(encoding="utf-8").rstrip("\n")


def test_full_golden_regression_matrix_covers_generated_query_shapes() -> None:
    cases = _regression_cases()
    generated_cases = [case for case in cases if case["expected_status"] == "generated"]
    non_success_cases = [case for case in cases if case["expected_status"] != "generated"]

    assert {case["query_shape"] for case in generated_cases} == FULL_REGRESSION_QUERY_SHAPES
    assert {case["expected_reason_code"] for case in non_success_cases} >= {
        "coverage_failure",
        "literal_unresolved",
    }
    assert {case["id"] for case in _smoke_regression_cases()}
    for case in generated_cases:
        assert case["expected_dsl_fixture"]
        assert case["expected_cypher_fixture"]
        assert (FIXTURE_DIR / case["expected_dsl_fixture"]).is_file()
        assert (FIXTURE_DIR / case["expected_cypher_fixture"]).is_file()


def test_golden_matrix_tracks_every_declared_question() -> None:
    cases = _all_cases()

    assert len(cases) == 34
    assert [case["id"] for case in cases] == [f"gq-{index:03d}" for index in range(1, 35)]
    assert {case["id"] for case in _regression_cases()} >= {
        "gq-001",
        "gq-002",
        "gq-003",
        "gq-006",
        "gq-007",
        "gq-008",
        "gq-009",
        "gq-010",
        "gq-012",
        "gq-017",
        "gq-019",
        "gq-029",
        "gq-030",
        "gq-031",
        "gq-032",
        "gq-033",
    }


def test_projection_regression_slice_is_declared_in_golden_matrix() -> None:
    cases_by_id = {case["id"]: case for case in _all_cases()}

    assert PROJECTION_REGRESSION_CASE_IDS <= set(cases_by_id)
    for case_id in sorted(PROJECTION_REGRESSION_CASE_IDS):
        case = cases_by_id[case_id]
        assert case["expected_status"] == "generated"
        assert case["coverage"] == "projection_slot"
        assert case["regression_scope"] in REGRESSION_SCOPES
        assert case["expected_dsl_fixture"]
        assert case["expected_cypher_fixture"]


def test_golden_regression_has_ci_workflow_entrypoint() -> None:
    workflow_path = Path(__file__).resolve().parents[4] / ".github" / "workflows" / "cypher-generator-agent.yml"

    assert workflow_path.is_file()
    workflow_text = workflow_path.read_text(encoding="utf-8")
    assert "workflow_dispatch" in workflow_text
    assert "test_golden_questions.py" in workflow_text
    assert "services/cypher_generator_agent/tests" in workflow_text
    assert "PYTHONPATH: ." in workflow_text


@pytest.mark.parametrize("case", _regression_cases(), ids=lambda case: case["id"])
def test_full_golden_regression_case_matches_expected_dsl_and_cypher(
    case: dict[str, Any],
) -> None:
    _assert_golden_case(case)


@pytest.mark.parametrize("case", _smoke_regression_cases(), ids=lambda case: case["id"])
def test_smoke_golden_regression_case_matches_expected_contract(
    case: dict[str, Any],
) -> None:
    assert case["ci_smoke"] is True
    _assert_golden_case(case)


@pytest.mark.parametrize(
    "case",
    [
        case
        for case in _all_cases()
        if case["expected_status"] != "generated" and not case.get("regression_scope")
    ],
    ids=lambda case: case["id"],
)
def test_non_runtime_golden_failure_case_is_backed_by_repair_contract(
    case: dict[str, Any],
) -> None:
    issue = _synthetic_repair_issue(case)
    decision = RepairController().decide(
        {
            "schema_version": "repair_controller_input_v1",
            "trace_id": f"golden-contract-{case['id']}",
            "question": case["question"],
            "attempt_no": 1,
            "selected_bindings": _selected_bindings_for_contract(case),
            "validator_errors": [issue] if issue["source"] == "validator" else [],
            "cypher_validation_errors": [issue] if issue["source"] == "cypher_self_validation" else [],
            "history": _history_for_contract(case),
        }
    )

    expected_status = case["expected_status"]
    expected_reason = case["expected_reason_code"]
    if expected_status == "clarification_required":
        assert decision.decision == "ask_user"
        assert decision.reason_code == expected_reason
        assert decision.clarification is not None
        return
    if expected_status == "unsupported_query_shape":
        assert decision.decision == "unsupported"
        assert decision.reason_code == "unsupported_query_shape"
        assert issue["details"]["reason_code"] == expected_reason
        return
    assert expected_status == "generation_failed"
    assert decision.decision == "generation_failed"
    assert decision.reason_code == expected_reason


def _assert_golden_case(case: dict[str, Any]) -> None:
    generation_run_id = f"golden-{case['id']}"
    output = run_pipeline(
        question=case["question"],
        qa_id=case["id"],
        generation_run_id=generation_run_id,
    )

    assert output.status == case["expected_status"]
    assert output.trace["question_id"] == case["id"]
    assert output.trace["generation_run_id"] == generation_run_id
    assert output.trace["final_status"] == output.status

    if case["expected_status"] != "generated":
        assert output.cypher is None
        assert output.dsl is None
        if case["expected_status"] == "clarification_required":
            assert output.failure is None
            assert output.clarification is not None
            assert output.trace["final_outputs"]["clarification"]["question"] == output.clarification.question
            assert _last_repair_reason(output.trace) == case["expected_reason_code"]
            return
        assert output.failure is not None
        assert output.failure.reason == case["expected_reason_code"]
        assert output.trace["final_outputs"]["failure"]["reason"] == case["expected_reason_code"]
        return

    assert output.failure is None
    expected_dsl = _load_json(FIXTURE_DIR / case["expected_dsl_fixture"])
    expected_cypher = _read_text_fixture(FIXTURE_DIR / case["expected_cypher_fixture"])
    assert output.dsl == expected_dsl
    assert output.cypher == expected_cypher

    registry = load_graph_semantic_model(MODEL_PATH).registry
    ast = parse_restricted_query_dsl(output.dsl, registry)
    assert ast.query_shape.value == case["query_shape"]
    assert output.trace["final_status"] == "generated"
    assert output.trace["final_outputs"]["dsl"] == output.dsl
    assert output.trace["final_outputs"]["cypher"] == output.cypher


def _last_repair_reason(trace: dict[str, object]) -> str:
    for stage in reversed(trace["stages"]):
        if stage["stage"] != "repair_controller":
            continue
        return stage["output_ref"]["value"]["reason_code"]
    raise AssertionError("missing repair_controller stage")


def _synthetic_repair_issue(case: dict[str, Any]) -> dict[str, Any]:
    reason = case["expected_reason_code"]
    issue_by_reason = {
        "ambiguous_port_intent": {
            "source": "validator",
            "code": "ambiguous_port_intent",
            "action": "ask_user",
            "message": "Port intent is ambiguous.",
            "details": {
                "term": "端口情况",
                "candidates": [
                    {"id": "port_list", "label": "列出端口", "confidence": 0.51},
                    {"id": "port_count", "label": "统计端口数量", "confidence": 0.49},
                ],
            },
        },
        "unsupported_shortest_path": _unsupported_issue(reason),
        "unsupported_optional_match": _unsupported_issue(reason),
        "unsupported_graph_algorithm": _unsupported_issue(reason),
        "cypher_readonly_violation": {
            "source": "cypher_self_validation",
            "code": "cypher_readonly_violation",
            "message": "Write clause is not allowed.",
            "details": {},
        },
        "edge_endpoint_mismatch": {
            "source": "validator",
            "code": "edge_endpoint_mismatch",
            "message": "Binding selected illegal edge endpoints.",
            "repairable": True,
            "details": {},
        },
        "compiler_shape_mismatch": {
            "source": "cypher_self_validation",
            "code": "compiler_shape_mismatch",
            "message": "Expected projection aliases do not match RETURN aliases.",
            "details": {},
        },
        "missing_required_path_pattern_parameter": {
            "source": "validator",
            "code": "missing_required_path_pattern_parameter",
            "action": "ask_user",
            "message": "Path pattern requires a tunnel id.",
            "details": {"term": "这条隧道"},
        },
        "duplicate_synonym_literal_ambiguity": {
            "source": "validator",
            "code": "duplicate_synonym_literal_ambiguity",
            "action": "ask_user",
            "message": "Literal synonyms resolve to the same enum value.",
            "details": {
                "term": "Gold 和金牌",
                "candidates": [
                    {"id": "gold", "label": "Gold", "value": "Gold", "confidence": 1.0},
                    {"id": "gold_synonym", "label": "金牌", "value": "Gold", "confidence": 1.0},
                ],
            },
        },
    }
    return issue_by_reason[reason]


def _unsupported_issue(reason: str) -> dict[str, Any]:
    return {
        "source": "validator",
        "code": "unsupported_query_shape",
        "action": "unsupported_query_shape",
        "message": reason,
        "details": {"reason_code": reason},
    }


def _selected_bindings_for_contract(case: dict[str, Any]) -> dict[str, Any]:
    if case["expected_reason_code"] != "edge_endpoint_mismatch":
        return {}
    return {
        "query_shape": "single_hop_traversal",
        "vertices": ["Service", "Port"],
        "edges": ["SERVICE_USES_TUNNEL"],
    }


def _history_for_contract(case: dict[str, Any]) -> list[dict[str, Any]]:
    return []
