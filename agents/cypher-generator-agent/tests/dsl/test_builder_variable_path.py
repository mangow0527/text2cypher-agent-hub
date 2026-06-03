from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.binding.models import (
    BindingPlan,
    CandidateBinding,
    EdgeBinding,
    VertexBinding,
)
from services.cypher_generator_agent.app.dsl.builder import RestrictedDslBuilder
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry, load_graph_semantic_model


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "network_topology_graph_model.yaml"


@pytest.fixture(scope="module")
def registry() -> GraphSemanticRegistry:
    return load_graph_semantic_model(FIXTURE_PATH).registry


def test_variable_path_builder_rejects_multiple_edge_bindings(registry: GraphSemanticRegistry) -> None:
    plan = BindingPlan(
        query_shape="variable_path_traversal",
        vertex_bindings=[
            VertexBinding(name="Tunnel", candidate=_candidate("vertex", "Tunnel")),
            VertexBinding(name="NetworkElement", candidate=_candidate("vertex", "NetworkElement")),
        ],
        edge_bindings=[
            EdgeBinding(name="PATH_THROUGH", candidate=_candidate("edge", "PATH_THROUGH")),
            EdgeBinding(name="SERVICE_USES_TUNNEL", candidate=_candidate("edge", "SERVICE_USES_TUNNEL")),
        ],
        projection=[{"semantic_type": "vertex", "name": "Tunnel"}],
    )

    with pytest.raises(ValueError, match="exactly one edge"):
        RestrictedDslBuilder(registry).build(
            plan,
            source_question="找出所有经过设备 ne-0001 的隧道",
            query_id="q-variable-path",
        )


def _candidate(semantic_type: str, name: str) -> CandidateBinding:
    return CandidateBinding(
        semantic_type=semantic_type,
        semantic_id=name,
        semantic_name=name,
        score=1.0,
        match_type="exact",
    )
