from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.binding.models import BindingPlan, CandidateBinding, VertexBinding
from services.cypher_generator_agent.app.semantic_model.loader import load_graph_semantic_model
from services.cypher_generator_agent.app.validation.semantic_validator import SemanticValidator


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "network_topology_graph_model.yaml"
)


@pytest.fixture
def validator() -> SemanticValidator:
    return SemanticValidator(load_graph_semantic_model(FIXTURE_PATH).registry)


def test_unsupported_shortest_path_shape_returns_unsupported_query_shape(
    validator: SemanticValidator,
) -> None:
    plan = BindingPlan(
        query_shape="shortest_path",
        vertex_bindings=[
            VertexBinding(name="NetworkElement", candidate=_candidate("vertex", "NetworkElement")),
        ],
    )

    result = validator.validate(plan)

    assert result.is_valid is False
    assert [(issue.code, issue.severity, issue.recoverability, issue.action) for issue in result.errors] == [
        ("unsupported_query_shape", "error", "non_repairable", "unsupported_query_shape")
    ]
    assert "shortest_path" in result.errors[0].message


def test_supported_vertex_lookup_shape_does_not_raise_dsl_support_error(
    validator: SemanticValidator,
) -> None:
    plan = BindingPlan(
        query_shape="vertex_lookup",
        vertex_bindings=[
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
        ],
    )

    result = validator.validate(plan)

    assert result.is_valid is True
    assert result.errors == []


def _candidate(semantic_type: str, semantic_id: str) -> CandidateBinding:
    return CandidateBinding(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
        semantic_name=semantic_id,
        score=1.0,
        match_type="exact",
    )
