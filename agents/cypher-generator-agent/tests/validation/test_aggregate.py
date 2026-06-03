from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.cypher_generator_agent.app.binding.models import (
    BindingPlan,
    CandidateBinding,
    FilterBinding,
    MetricBinding,
)
from services.cypher_generator_agent.app.semantic_model.loader import load_graph_semantic_model
from services.cypher_generator_agent.app.validation.semantic_validator import SemanticValidator


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "network_topology_graph_model.yaml"


@pytest.fixture
def validator() -> SemanticValidator:
    return SemanticValidator(load_graph_semantic_model(FIXTURE_PATH).registry)


def test_ad_hoc_avg_on_string_property_fails_type_compatibility(
    validator: SemanticValidator,
) -> None:
    plan = BindingPlan(
        query_shape="ad_hoc_aggregate",
        measures=[
            {
                "alias": "avg_status",
                "function": "avg",
                "target": "port",
                "property": {"owner": "Port", "name": "status"},
            }
        ],
    )

    result = validator.validate(plan)

    assert result.is_valid is False
    assert result.errors[0].code == "invalid_aggregate_property_type"
    assert result.errors[0].recoverability == "repairable"
    assert result.errors[0].action == "repair_binding"
    assert "Port.status" in result.errors[0].message


def test_metric_filter_dimension_must_be_declared_valid_dimension(
    validator: SemanticValidator,
) -> None:
    plan = BindingPlan(
        query_shape="metric_aggregate",
        metric_bindings=[MetricBinding(name="device_count", candidate=_candidate("metric", "device_count"))],
        filters=[
            FilterBinding(
                owner="NetworkElement",
                property="id",
                operator="eq",
                raw_literal="ne-0001",
                value="ne-0001",
            )
        ],
    )

    result = validator.validate(plan)

    assert result.is_valid is False
    assert result.errors[0].code == "metric_dimension_invalid"
    assert result.errors[0].recoverability == "repairable"
    assert "ne.id" in result.errors[0].message


def test_top_n_metric_group_by_dimension_must_be_declared_valid_dimension(
    validator: SemanticValidator,
) -> None:
    plan = BindingPlan(
        query_shape="top_n",
        metric_bindings=[MetricBinding(name="device_count", candidate=_candidate("metric", "device_count"))],
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
    assert "ne.id" in result.errors[0].message


def test_metric_group_by_dimension_accepts_target_alias_normalization(
    validator: SemanticValidator,
) -> None:
    plan = BindingPlan(
        query_shape="top_n",
        metric_bindings=[MetricBinding(name="device_count", candidate=_candidate("metric", "device_count"))],
        group_by=[
            {
                "alias": "network_element_location",
                "target": "network_element",
                "property": {"owner": "NetworkElement", "name": "location"},
            }
        ],
    )

    result = validator.validate(plan)

    assert result.is_valid is True


def _candidate(semantic_type: str, semantic_id: str, *, metadata: dict[str, Any] | None = None) -> CandidateBinding:
    return CandidateBinding(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
        semantic_name=semantic_id,
        score=1.0,
        match_type="exact",
        metadata=metadata or {},
    )
