from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.binding.models import BindingPlan, CandidateBinding, VertexBinding
from services.cypher_generator_agent.app.semantic_model.loader import load_graph_semantic_model
from services.cypher_generator_agent.app.validation.coverage import build_coverage_report
from services.cypher_generator_agent.app.validation.semantic_validator import SemanticValidator


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "network_topology_graph_model.yaml"
)


@pytest.fixture
def validator() -> SemanticValidator:
    return SemanticValidator(load_graph_semantic_model(FIXTURE_PATH).registry)


def test_uncovered_substantive_term_returns_non_repairable_coverage_error(
    validator: SemanticValidator,
) -> None:
    coverage = build_coverage_report(
        {
            "substantive_terms": {"total": 2, "covered": 1, "uncovered": ["增长"]},
            "time_terms": {"covered": [], "unresolved": []},
            "unparsed_terms": {"unresolved": []},
            "modality_terms": {"warning_only": []},
        }
    )

    result = validator.validate(_vertex_lookup_plan(), coverage=coverage)

    assert result.is_valid is False
    assert [(issue.code, issue.severity, issue.recoverability, issue.action) for issue in result.errors] == [
        ("coverage_failure", "error", "non_repairable", "ask_user")
    ]
    assert "增长" in result.errors[0].message


@pytest.mark.parametrize(
    ("coverage_payload", "expected_term"),
    [
        (
            {
                "substantive_terms": {"total": 1, "covered": 1, "uncovered": []},
                "time_terms": {"covered": [], "unresolved": ["最近"]},
                "unparsed_terms": {"unresolved": []},
                "modality_terms": {"warning_only": []},
            },
            "最近",
        ),
        (
            {
                "substantive_terms": {"total": 1, "covered": 1, "uncovered": []},
                "time_terms": {"covered": [], "unresolved": []},
                "unparsed_terms": {"unresolved": ["异常高"]},
                "modality_terms": {"warning_only": []},
            },
            "异常高",
        ),
    ],
)
def test_unresolved_time_or_unparsed_terms_fail_coverage(
    validator: SemanticValidator,
    coverage_payload: dict[str, object],
    expected_term: str,
) -> None:
    result = validator.validate(_vertex_lookup_plan(), coverage=build_coverage_report(coverage_payload))

    assert result.is_valid is False
    assert result.errors[0].code == "coverage_failure"
    assert result.errors[0].recoverability == "non_repairable"
    assert result.errors[0].action == "ask_user"
    assert expected_term in result.errors[0].message


def test_modality_should_is_warning_only_and_becomes_assumption(
    validator: SemanticValidator,
) -> None:
    coverage = build_coverage_report(
        {
            "substantive_terms": {"total": 2, "covered": 2, "uncovered": []},
            "time_terms": {"covered": [], "unresolved": []},
            "unparsed_terms": {"unresolved": []},
            "modality_terms": {"warning_only": ["应该"]},
        }
    )

    result = validator.validate(_vertex_lookup_plan(), coverage=coverage)

    assert result.is_valid is True
    assert result.errors == []
    assert [(issue.code, issue.severity, issue.action) for issue in result.warnings] == [
        ("modality_warning", "warning", "continue_with_assumption")
    ]
    assert result.assumptions == [
        {
            "type": "modality_warning",
            "term": "应该",
            "message": "问题中的“应该”没有被解释为查询约束。",
        }
    ]


def test_missing_projection_slot_term_is_repairable_coverage_error(
    validator: SemanticValidator,
) -> None:
    coverage = build_coverage_report(
        {
            "substantive_terms": {"total": 4, "covered": 4, "uncovered": []},
            "time_terms": {"covered": [], "unresolved": []},
            "unparsed_terms": {"unresolved": []},
            "modality_terms": {"warning_only": []},
            "projection_terms": {
                "required": ["ID", "服务质量等级"],
                "covered": ["ID"],
                "uncovered": ["服务质量等级"],
            },
        }
    )
    plan = BindingPlan(
        query_shape="vertex_lookup",
        vertex_bindings=[
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
        ],
        projection=[
            {
                "semantic_type": "property",
                "owner": "Service",
                "name": "id",
                "alias": "service_id",
                "projection_terms": ["ID"],
            }
        ],
    )

    result = validator.validate(plan, coverage=coverage)

    assert result.is_valid is False
    assert [(issue.code, issue.recoverability, issue.action) for issue in result.errors] == [
        ("projection_coverage_missing", "repairable", "repair_binding")
    ]
    assert result.errors[0].details == {
        "required": ["ID", "服务质量等级"],
        "covered": ["ID"],
        "uncovered": ["服务质量等级"],
    }


def _vertex_lookup_plan() -> BindingPlan:
    return BindingPlan(
        query_shape="vertex_lookup",
        vertex_bindings=[
            VertexBinding(
                name="Service",
                candidate=_candidate("vertex", "Service"),
            )
        ],
    )


def _candidate(semantic_type: str, semantic_id: str) -> CandidateBinding:
    return CandidateBinding(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
        semantic_name=semantic_id,
        score=1.0,
        match_type="exact",
    )
