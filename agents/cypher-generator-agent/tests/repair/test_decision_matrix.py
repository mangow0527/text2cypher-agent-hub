from __future__ import annotations

from typing import Any

from services.cypher_generator_agent.app.core.errors import GenerationFailureReason
from services.cypher_generator_agent.app.core.result import ClarificationRequest, GenerationFailure
from services.cypher_generator_agent.app.cypher_validation.models import validation_error
from services.cypher_generator_agent.app.repair.controller import RepairController
from services.cypher_generator_agent.app.validation.models import SemanticValidationIssue


def test_edge_endpoint_mismatch_concedes_without_second_repair_llm() -> None:
    issue = SemanticValidationIssue(
        code="edge_endpoint_mismatch",
        message="SERVICE_USES_TUNNEL cannot connect Service to NetworkElement",
        severity="error",
        recoverability="repairable",
        action="repair_binding",
        details={"expected_to": "Tunnel", "actual_to": "NetworkElement"},
    )

    decision = RepairController().decide(
        _controller_input(
            selected_bindings=_binding_plan("SERVICE_USES_TUNNEL"),
            validator_errors=[issue.model_dump(mode="json")],
        )
    )

    assert decision.decision == "generation_failed"
    assert decision.reason_code == "edge_endpoint_mismatch"
    assert decision.stop_reason == "edge_endpoint_mismatch"
    assert decision.repair_prompt_delta == {}
    assert decision.clarification is None


def test_history_no_longer_drives_binding_oscillation_stop() -> None:
    plan_a = _binding_plan("SERVICE_USES_TUNNEL")
    plan_b = _binding_plan("DEVICE_HAS_PORT")

    decision = RepairController().decide(
        _controller_input(
            attempt_no=3,
            selected_bindings=plan_a,
            validator_errors=[
                {
                    "code": "edge_endpoint_mismatch",
                    "message": "same repairable endpoint mismatch returned after A -> B -> A",
                    "severity": "error",
                    "repairable": True,
                }
            ],
            history=[
                {"attempt_no": 1, "fingerprint": "plan-a"},
                {"attempt_no": 2, "fingerprint": "plan-b"},
            ],
        )
    )

    assert decision.decision == "generation_failed"
    assert decision.reason_code == "edge_endpoint_mismatch"
    assert decision.stop_reason == "edge_endpoint_mismatch"


def test_alternating_missing_requirements_no_longer_drive_repair_loop_stop() -> None:
    plan_a = _binding_plan("SERVICE_USES_TUNNEL")
    plan_b = _binding_plan("DEVICE_HAS_PORT")
    plan_c = _binding_plan("PATH_THROUGH")

    decision = RepairController().decide(
        _controller_input(
            attempt_no=3,
            selected_bindings=plan_c,
            validator_errors=[
                _structural_missing_issue(["aggregate_required"])
            ],
            history=[
                {
                    "attempt_no": 1,
                    "fingerprint": "plan-a",
                    "error_code": "structural_coverage_missing",
                    "missing_requirements": ["aggregate_required"],
                },
                {
                    "attempt_no": 2,
                    "fingerprint": "plan-b",
                    "error_code": "structural_coverage_missing",
                    "missing_requirements": ["path_hops_insufficient"],
                },
            ],
        )
    )

    assert decision.decision == "generation_failed"
    assert decision.reason_code == "structural_coverage_missing"
    assert decision.stop_reason == "structural_coverage_missing"


def test_attempt_count_no_longer_controls_single_shot_fallback() -> None:
    decision = RepairController().decide(
        _controller_input(
            attempt_no=4,
            selected_bindings=_binding_plan("SERVICE_USES_TUNNEL"),
            validator_errors=[
                {
                    "code": "edge_endpoint_mismatch",
                    "message": "still invalid after max repair attempts",
                    "severity": "error",
                    "repairable": True,
                }
            ],
        )
    )

    assert decision.decision == "generation_failed"
    assert decision.reason_code == "edge_endpoint_mismatch"
    assert decision.stop_reason == "edge_endpoint_mismatch"
    GenerationFailure(reason="max_repair_attempts_exceeded")
    assert "max_repair_attempts_exceeded" in GenerationFailureReason.__args__


def test_ambiguous_top2_gap_below_threshold_asks_one_question_with_three_options() -> None:
    decision = RepairController().decide(
        _controller_input(
            validator_errors=[
                {
                    "code": "ambiguous_vertex_binding",
                    "message": "端口 has close candidates",
                    "severity": "error",
                    "repairable": False,
                    "details": {
                        "term": "端口",
                        "candidates": [
                            {
                                "id": "Socket",
                                "label": "套接字",
                                "vertex_name": "Socket",
                                "confidence": 0.30,
                            },
                            {
                                "id": "Port",
                                "label": "设备端口",
                                "vertex_name": "Port",
                                "confidence": 0.77,
                            },
                            {
                                "id": "ServicePort",
                                "label": "服务端口",
                                "vertex_name": "ServicePort",
                                "confidence": 0.72,
                            },
                            {
                                "id": "Interface",
                                "label": "接口",
                                "vertex_name": "Interface",
                                "confidence": 0.52,
                            },
                        ],
                    },
                }
            ]
        )
    )

    assert decision.decision == "ask_user"
    assert decision.reason_code == "ambiguous_vertex_binding"
    assert decision.clarification is not None
    assert decision.clarification.expected_answer_type == "single_choice"
    ClarificationRequest(question=decision.clarification.question)
    assert "端口" in decision.clarification.question_zh
    assert [option.id for option in decision.clarification.options] == [
        "Port",
        "ServicePort",
        "Interface",
    ]


def test_literal_resolver_alternatives_render_as_clarification_options() -> None:
    decision = RepairController().decide(
        _controller_input(
            validator_errors=[
                {
                    "code": "literal_ambiguous",
                    "message": "literal has close alternatives",
                    "severity": "error",
                    "details": {
                        "literal": "tun-mpls-001",
                        "alternatives": [
                            {
                                "value": "MPLS-TE",
                                "display": "MPLS-TE",
                                "confidence": 0.5263,
                                "source": "property.valid_values",
                                "why": "closest local literal candidate",
                            }
                        ],
                    },
                }
            ]
        )
    )

    assert decision.decision == "ask_user"
    assert decision.clarification is not None
    assert decision.clarification.options[0].id == "MPLS-TE"
    assert decision.clarification.options[0].label == "MPLS-TE"


def test_literal_unresolved_asks_user_instead_of_silently_continuing() -> None:
    decision = RepairController().decide(
        _controller_input(
            validator_errors=[
                {
                    "code": "literal_unresolved",
                    "message": "literal could not be resolved",
                    "severity": "error",
                    "repairable": False,
                    "details": {
                        "literal": "核心防火墙",
                        "property": "NetworkElement.name",
                        "alternatives": [
                            {"id": "fw-001", "label": "FW-001"},
                            {"id": "fw-002", "label": "FW-002"},
                        ],
                    },
                }
            ]
        )
    )

    assert decision.decision == "ask_user"
    assert decision.reason_code == "literal_unresolved"
    assert decision.clarification is not None
    ClarificationRequest(question=decision.clarification.question)
    assert [option.id for option in decision.clarification.options] == ["fw-001", "fw-002"]


def test_coverage_failure_requires_user_clarification() -> None:
    decision = RepairController().decide(
        _controller_input(
            validator_errors=[
                {
                    "code": "coverage_failure",
                    "message": "substantive term not covered",
                    "severity": "error",
                    "recoverability": "non_repairable",
                    "action": "ask_user",
                    "details": {"substantive_uncovered": ["增长"]},
                }
            ]
        )
    )

    assert decision.decision == "ask_user"
    assert decision.reason_code == "coverage_failure"
    assert decision.clarification is not None
    assert "增长" in decision.clarification.question


def test_unsupported_query_shape_has_no_raw_cypher_fallback_field() -> None:
    decision = RepairController().decide(
        _controller_input(
            validator_errors=[
                {
                    "code": "unsupported_query_shape",
                    "message": "recursive shortest path is unsupported in v1 DSL",
                    "severity": "error",
                    "repairable": False,
                    "details": {"query_shape": "recursive_shortest_path"},
                }
            ]
        )
    )

    payload = decision.model_dump(exclude_none=True)

    assert decision.decision == "unsupported"
    assert decision.reason_code == "unsupported_query_shape"
    assert "cypher" not in payload
    assert "raw_cypher" not in payload
    assert "fallback_cypher" not in payload


def test_cypher_static_validation_failures_stop_without_auto_retry() -> None:
    failure_codes = [
        "cypher_syntax_invalid",
        "cypher_readonly_violation",
        "cypher_schema_reference_invalid",
        "compiler_shape_mismatch",
        "target_dialect_static_error",
    ]
    for code in failure_codes:
        issue = {
            "code": code,
            "message": f"{code} raised by self validator",
            "severity": "error",
        }
        if code == "cypher_syntax_invalid":
            issue = validation_error(
                code="cypher_syntax_invalid",
                message="parse failed",
                check="syntax",
                location="$",
            ).model_dump(mode="json")

        decision = RepairController().decide(
            _controller_input(cypher_validation_errors=[issue])
        )

        assert decision.decision == "generation_failed"
        assert decision.reason_code == code
        assert decision.repair_prompt_delta == {}


def test_compiler_shape_mismatch_fails_without_auto_retry() -> None:
    decision = RepairController().decide(
        _controller_input(
            cypher_validation_errors=[
                {
                    "code": "compiler_shape_mismatch",
                    "message": "RETURN columns do not match DSL projection",
                    "severity": "error",
                    "repairable": False,
                }
            ]
        )
    )

    assert decision.decision == "generation_failed"
    assert decision.reason_code == "compiler_shape_mismatch"
    assert decision.repair_prompt_delta == {}


def _controller_input(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "repair_controller_input_v1",
        "trace_id": "q-20260527-001",
        "question": "Gold 级别的服务都用了哪些 MPLS-TE 隧道",
        "attempt_no": 1,
        "selected_bindings": _binding_plan("SERVICE_USES_TUNNEL"),
        "normalized_dsl": None,
        "validator_errors": [],
        "cypher_validation_errors": [],
        "history": [],
        "assumptions": [],
    }
    payload.update(overrides)
    return payload


def _binding_plan(edge_name: str) -> dict[str, Any]:
    return {
        "schema_version": "binding_plan_v1",
        "query_shape": "single_hop_traversal",
        "vertex_bindings": [
            {
                "name": "Service",
                "candidate": {
                    "semantic_type": "vertex",
                    "semantic_id": "Service",
                    "semantic_name": "Service",
                    "score": 0.91,
                    "match_type": "exact",
                },
            },
            {
                "name": "Tunnel",
                "candidate": {
                    "semantic_type": "vertex",
                    "semantic_id": "Tunnel",
                    "semantic_name": "Tunnel",
                    "score": 0.88,
                    "match_type": "exact",
                },
            },
        ],
        "edge_bindings": [
            {
                "name": edge_name,
                "direction": "forward",
                "candidate": {
                    "semantic_type": "edge",
                    "semantic_id": edge_name,
                    "semantic_name": edge_name,
                    "score": 0.82,
                    "match_type": "exact",
                },
            }
        ],
    }


def _structural_missing_issue(codes: list[str]) -> dict[str, Any]:
    return {
        "code": "structural_coverage_missing",
        "message": "DSL does not cover all structural requirements.",
        "severity": "error",
        "repairable": True,
        "action": "repair_binding",
        "details": {
            "missing": [
                {
                    "code": code,
                    "message": f"{code} missing",
                }
                for code in codes
            ]
        },
    }
