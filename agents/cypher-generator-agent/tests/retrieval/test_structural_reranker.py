from __future__ import annotations

from services.cypher_generator_agent.app.retrieval.models import CandidateRetrievalResult, SemanticCandidate
from services.cypher_generator_agent.app.retrieval.structural_reranker import StructuralReranker


def test_property_candidates_owned_by_vertex_context_are_kept_ahead_of_unrelated_properties() -> None:
    result = StructuralReranker().rerank(
        CandidateRetrievalResult(
            candidates=[
                _candidate("property", "Fiber.name", 0.99, owner="Fiber"),
                _candidate("property", "Protocol.name", 0.98, owner="Protocol"),
                _candidate("property", "Port.name", 0.97, owner="Port"),
                _candidate("property", "NetworkElement.name", 0.80, owner="NetworkElement"),
                _candidate("vertex", "Service", 0.79),
                _candidate("vertex", "Tunnel", 0.78),
                _candidate("vertex", "NetworkElement", 0.77),
            ]
        ),
        structural_requirements={"schema_version": "mir_006_structural_requirements_v1"},
    )

    ids = [candidate.semantic_id for candidate in result.candidates]
    assert ids.index("NetworkElement.name") < ids.index("Fiber.name")
    assert ids.index("NetworkElement.name") < ids.index("Protocol.name")
    assert ids.index("NetworkElement.name") < ids.index("Port.name")

    by_id = {trace.semantic_id: trace for trace in result.trace}
    assert by_id["NetworkElement.name"].decision == "kept"
    assert by_id["NetworkElement.name"].vertex_context == ["NetworkElement", "Service", "Tunnel"]
    assert by_id["Fiber.name"].decision == "demoted"
    assert by_id["Protocol.name"].decision == "demoted"
    assert by_id["Port.name"].decision == "demoted"


def test_edges_touching_vertex_context_are_all_kept_without_endpoint_arbitration() -> None:
    result = StructuralReranker().rerank(
        [
            _candidate(
                "edge",
                "SERVICE_USES_TUNNEL",
                0.70,
                metadata={"from": "Service", "to": "Tunnel"},
            ),
            _candidate(
                "edge",
                "PATH_THROUGH",
                0.69,
                metadata={"from_vertex": "Tunnel", "to_vertex": "NetworkElement"},
            ),
            _candidate(
                "edge",
                "TUNNEL_SRC",
                0.68,
                metadata={"from": "Tunnel", "to": "NetworkElement"},
            ),
            _candidate(
                "edge",
                "TUNNEL_DST",
                0.67,
                metadata={"from": "Tunnel", "to": "NetworkElement"},
            ),
            _candidate("vertex", "Service", 0.66),
            _candidate("vertex", "Tunnel", 0.65),
            _candidate("vertex", "NetworkElement", 0.64),
        ],
        structural_requirements={"schema_version": "mir_006_structural_requirements_v1"},
    )

    ids = [candidate.semantic_id for candidate in result.candidates]
    assert {"SERVICE_USES_TUNNEL", "PATH_THROUGH", "TUNNEL_SRC", "TUNNEL_DST"} <= set(ids)

    by_id = {trace.semantic_id: trace for trace in result.trace}
    for edge_id in ("SERVICE_USES_TUNNEL", "PATH_THROUGH", "TUNNEL_SRC", "TUNNEL_DST"):
        assert by_id[edge_id].decision == "kept"
        assert by_id[edge_id].adjusted_score == by_id[edge_id].original_score
        assert "edge_touches_vertex_context" in by_id[edge_id].reason


def test_missing_vertex_context_returns_original_candidates_with_no_vertex_context_trace() -> None:
    candidates = [
        _candidate("property", "Fiber.name", 0.91, owner="Fiber"),
        _candidate("edge", "UNRELATED_EDGE", 0.90, metadata={"from": "Fiber", "to": "Protocol"}),
    ]

    result = StructuralReranker().rerank(
        CandidateRetrievalResult(candidates=candidates),
        structural_requirements={},
    )

    assert result.candidates == candidates
    assert [candidate.score for candidate in result.candidates] == [0.91, 0.90]
    assert [trace.decision for trace in result.trace] == ["kept", "kept"]
    assert {trace.reason for trace in result.trace} == {"no_vertex_context"}


def _candidate(
    semantic_type: str,
    semantic_id: str,
    score: float,
    *,
    owner: str | None = None,
    metadata: dict[str, str] | None = None,
) -> SemanticCandidate:
    return SemanticCandidate(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
        semantic_name=semantic_id,
        score=score,
        match_type="text",
        evidence=[{"term": semantic_id, "source": "test", "matched_text": semantic_id}],
        owner=owner,
        metadata=metadata or {},
    )
