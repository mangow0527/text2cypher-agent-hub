from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.cypher_generator_agent.app.binding.models import (
    BindingPlan,
    CandidateBinding,
    EdgeBinding,
    MetricBinding,
    PropertyBinding,
    VertexBinding,
)
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


def test_edge_endpoint_mismatch_returns_repairable_error(
    validator: SemanticValidator,
) -> None:
    plan = BindingPlan(
        query_shape="single_hop_traversal",
        vertex_bindings=[
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
            VertexBinding(name="NetworkElement", candidate=_candidate("vertex", "NetworkElement")),
        ],
        edge_bindings=[
            EdgeBinding(name="SERVICE_USES_TUNNEL", candidate=_candidate("edge", "SERVICE_USES_TUNNEL")),
        ],
    )

    result = validator.validate(plan)

    assert result.is_valid is False
    assert [(issue.code, issue.severity, issue.recoverability, issue.action) for issue in result.errors] == [
        ("edge_endpoint_mismatch", "error", "repairable", "repair_binding")
    ]
    assert "SERVICE_USES_TUNNEL" in result.errors[0].message
    assert "Service -> NetworkElement" in result.errors[0].message


def test_single_hop_missing_edge_returns_repairable_incomplete_plan_error(
    validator: SemanticValidator,
) -> None:
    plan = BindingPlan(
        query_shape="single_hop_traversal",
        vertex_bindings=[
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
            VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel")),
        ],
    )

    result = validator.validate(plan)

    assert result.is_valid is False
    assert result.errors[0].code == "binding_plan_incomplete"
    assert result.errors[0].recoverability == "repairable"
    assert result.errors[0].action == "repair_binding"
    assert result.errors[0].details["missing"] == ["edge_bindings"]


def test_single_hop_missing_endpoint_returns_repairable_incomplete_plan_error(
    validator: SemanticValidator,
) -> None:
    plan = BindingPlan(
        query_shape="single_hop_traversal",
        vertex_bindings=[
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
        ],
        edge_bindings=[
            EdgeBinding(name="SERVICE_USES_TUNNEL", candidate=_candidate("edge", "SERVICE_USES_TUNNEL")),
        ],
    )

    result = validator.validate(plan)

    assert result.is_valid is False
    assert result.errors[0].code == "binding_plan_incomplete"
    assert result.errors[0].recoverability == "repairable"
    assert result.errors[0].details["missing"] == ["to_vertex"]


def test_backward_single_hop_direction_allows_reversed_vertex_order(
    validator: SemanticValidator,
) -> None:
    plan = BindingPlan(
        query_shape="single_hop_traversal",
        vertex_bindings=[
            VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel")),
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
        ],
        edge_bindings=[
            EdgeBinding(
                name="SERVICE_USES_TUNNEL",
                candidate=_candidate("edge", "SERVICE_USES_TUNNEL"),
                direction="backward",
            ),
        ],
    )

    result = validator.validate(plan)

    assert result.is_valid is True
    assert result.errors == []


def test_traversal_chain_validates_each_edge_against_adjacent_vertices(
    validator: SemanticValidator,
) -> None:
    plan = BindingPlan(
        query_shape="single_hop_traversal",
        vertex_bindings=[
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
            VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel")),
            VertexBinding(name="NetworkElement", candidate=_candidate("vertex", "NetworkElement")),
        ],
        edge_bindings=[
            EdgeBinding(name="SERVICE_USES_TUNNEL", candidate=_candidate("edge", "SERVICE_USES_TUNNEL")),
            EdgeBinding(name="PATH_THROUGH", candidate=_candidate("edge", "PATH_THROUGH")),
        ],
    )

    result = validator.validate(plan)

    assert result.is_valid is True
    assert result.errors == []


def test_traversal_chain_reports_mismatched_later_edge_endpoint(
    validator: SemanticValidator,
) -> None:
    plan = BindingPlan(
        query_shape="single_hop_traversal",
        vertex_bindings=[
            VertexBinding(name="Service", candidate=_candidate("vertex", "Service")),
            VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel")),
            VertexBinding(name="Port", candidate=_candidate("vertex", "Port")),
        ],
        edge_bindings=[
            EdgeBinding(name="SERVICE_USES_TUNNEL", candidate=_candidate("edge", "SERVICE_USES_TUNNEL")),
            EdgeBinding(name="PATH_THROUGH", candidate=_candidate("edge", "PATH_THROUGH")),
        ],
    )

    result = validator.validate(plan)

    assert result.is_valid is False
    assert result.errors[0].code == "edge_endpoint_mismatch"
    assert result.errors[0].details["edge_index"] == 1
    assert result.errors[0].details["actual_from"] == "Tunnel"
    assert result.errors[0].details["actual_to"] == "Port"


def test_property_owner_mismatch_returns_repairable_error(
    validator: SemanticValidator,
) -> None:
    plan = BindingPlan(
        query_shape="vertex_lookup",
        vertex_bindings=[
            VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel")),
        ],
        property_bindings=[
            PropertyBinding(
                owner="Tunnel",
                name="quality_of_service",
                candidate=_candidate(
                    "property",
                    "Service.quality_of_service",
                    semantic_name="quality_of_service",
                    owner="Service",
                ),
            )
        ],
    )

    result = validator.validate(plan)

    assert result.is_valid is False
    assert result.errors[0].code == "property_owner_mismatch"
    assert result.errors[0].recoverability == "repairable"
    assert result.errors[0].action == "repair_binding"
    assert "Tunnel.quality_of_service" in result.errors[0].message


def test_metric_group_by_dimension_must_be_declared_valid_dimension(
    validator: SemanticValidator,
) -> None:
    plan = BindingPlan(
        query_shape="metric_aggregate",
        metric_bindings=[
            MetricBinding(name="device_count", candidate=_candidate("metric", "device_count")),
        ],
        group_by=[
            {
                "alias": "device_id",
                "target": "ne",
                "property": {"owner": "NetworkElement", "name": "id"},
            }
        ],
    )

    result = validator.validate(plan)

    assert result.is_valid is False
    assert result.errors[0].code == "metric_dimension_invalid"
    assert result.errors[0].recoverability == "repairable"
    assert result.errors[0].action == "repair_binding"
    assert "ne.id" in result.errors[0].message
    assert "device_count" in result.errors[0].message


def test_metric_group_by_dimension_shape_must_be_dsl_compatible(
    validator: SemanticValidator,
) -> None:
    plan = BindingPlan(
        query_shape="metric_aggregate",
        metric_bindings=[
            MetricBinding(name="device_count", candidate=_candidate("metric", "device_count")),
        ],
        group_by=[
            {
                "dimension": "ne.elem_type",
            }
        ],
    )

    result = validator.validate(plan)

    assert result.is_valid is False
    assert result.errors[0].code == "metric_group_by_invalid"
    assert result.errors[0].recoverability == "repairable"
    assert result.errors[0].action == "repair_binding"


def _candidate(
    semantic_type: str,
    semantic_id: str,
    *,
    semantic_name: str | None = None,
    owner: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> CandidateBinding:
    return CandidateBinding(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
        semantic_name=semantic_name or semantic_id,
        score=1.0,
        match_type="exact",
        owner=owner,
        metadata=metadata or {},
    )
