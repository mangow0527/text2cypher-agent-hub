from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.retrieval.index import build_semantic_index
from services.cypher_generator_agent.app.retrieval.retriever import CandidateRetriever, _extract_search_terms
from services.cypher_generator_agent.app.semantic_model.loader import load_graph_semantic_model


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "network_topology_graph_model.yaml"
)
TUGRAPH_ARTIFACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "semantic_model"
    / "artifacts"
    / "tugraph_network_semantic_model.yaml"
)


@pytest.fixture
def retriever() -> CandidateRetriever:
    return CandidateRetriever(load_graph_semantic_model(FIXTURE_PATH).registry)


def test_service_synonym_recalls_service_vertex(retriever: CandidateRetriever) -> None:
    result = retriever.retrieve(_decomposition("查询服务", "服务"))

    candidate = _candidate(result.candidates, "vertex", "Service")
    assert candidate.semantic_name == "Service"
    assert candidate.match_type == "synonym"
    assert candidate.score > 0.8
    assert candidate.evidence[0].source == "ai_context.synonyms"
    assert candidate.evidence[0].matched_text == "服务"


def test_tunnel_synonym_recalls_tunnel_vertex(retriever: CandidateRetriever) -> None:
    result = retriever.retrieve(_decomposition("查询隧道", "隧道"))

    candidate = _candidate(result.candidates, "vertex", "Tunnel")
    assert candidate.match_type == "synonym"
    assert candidate.evidence[0].matched_text == "隧道"


def test_question_decomposition_v1_terms_drive_candidate_retrieval(
    retriever: CandidateRetriever,
) -> None:
    result = retriever.retrieve(
        {
            "schema_version": "question_decomposition_v1",
            "original_question": "隧道 tun-mpls-001 经过哪些设备",
            "literal_candidates": ["tun-mpls-001"],
            "substantive_terms": [
                {"text": "隧道", "slot": "path"},
                {"text": "经过", "slot": "path"},
                {"text": "设备", "slot": "projection"},
            ],
        }
    )

    assert _candidate(result.candidates, "vertex", "Tunnel")
    assert _candidate(result.candidates, "edge", "PATH_THROUGH")
    assert _candidate(result.candidates, "path_pattern", "tunnel_full_path")


def test_question_decomposition_v1_structured_literal_candidates_drive_retrieval(
    retriever: CandidateRetriever,
) -> None:
    result = retriever.retrieve(
        {
            "schema_version": "question_decomposition_v1",
            "intent_type": "list",
            "original_question": "Gold 服务使用了哪些隧道",
            "literal_candidates": [
                {"text": "Gold", "kind_hint": "enum_or_name", "attached_to": "服务"}
            ],
            "substantive_terms": [
                {"text": "Gold", "slot": "filter", "attached_to": "服务"},
                {"text": "服务", "slot": "path"},
                {"text": "使用", "slot": "path"},
                {"text": "隧道", "slot": "projection"},
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "output_shape": "rows",
        }
    )

    assert _candidate(result.candidates, "vertex", "Service")
    assert _candidate(result.candidates, "edge", "SERVICE_USES_TUNNEL")
    assert _candidate(result.candidates, "property", "Service.quality_of_service")


def test_used_phrase_recalls_service_uses_tunnel_edge(retriever: CandidateRetriever) -> None:
    result = retriever.retrieve(_decomposition("Gold 服务用了哪些隧道", "用了"))

    candidate = _candidate(result.candidates, "edge", "SERVICE_USES_TUNNEL")
    assert candidate.match_type == "text"
    assert candidate.score > 0.6
    assert candidate.evidence[0].source == "ai_context.examples"
    assert "使用了" in candidate.evidence[0].matched_text


def test_aspect_particle_phrase_recalls_service_uses_tunnel_edge_from_packaged_artifact() -> None:
    retriever = CandidateRetriever(load_graph_semantic_model(TUGRAPH_ARTIFACT_PATH).registry)

    result = retriever.retrieve(
        {
            "schema_version": "question_decomposition_v1",
            "original_question": "Gold 服务使用了哪些隧道",
            "literal_candidates": [
                {"text": "Gold", "kind_hint": "enum_or_name", "attached_to": "服务"}
            ],
            "substantive_terms": [
                {"text": "Gold", "slot": "filter", "attached_to": "服务"},
                {"text": "服务", "slot": "path"},
                {"text": "使用", "slot": "path"},
                {"text": "隧道", "slot": "projection"},
            ],
        }
    )

    candidate = _candidate(result.candidates, "edge", "SERVICE_USES_TUNNEL")
    assert candidate.score > 0.6
    assert candidate.evidence[0].matched_text == "使用隧道"


def test_retrieved_vertices_include_id_property_candidates_for_literal_binding() -> None:
    retriever = CandidateRetriever(load_graph_semantic_model(TUGRAPH_ARTIFACT_PATH).registry)

    result = retriever.retrieve(
        {
            "schema_version": "question_decomposition_v1",
            "original_question": "隧道 tun-mpls-001 经过哪些设备",
            "literal_candidates": [
                {"text": "tun-mpls-001", "kind_hint": "id", "attached_to": "隧道"}
            ],
            "substantive_terms": [
                {"text": "隧道", "slot": "path"},
                {"text": "tun-mpls-001", "slot": "filter", "attached_to": "隧道"},
                {"text": "经过", "slot": "path"},
                {"text": "设备", "slot": "projection"},
            ],
        }
    )

    assert _candidate(result.candidates, "vertex", "Tunnel")
    id_property = _candidate(result.candidates, "property", "Tunnel.id")
    assert id_property.owner == "Tunnel"
    assert id_property.evidence[0].source == "vertex.id_property"


def test_through_phrase_recalls_edge_and_named_path_pattern_with_distinct_evidence(
    retriever: CandidateRetriever,
) -> None:
    result = retriever.retrieve(_decomposition("隧道 tun-mpls-001 经过哪些设备", "经过"))

    edge = _candidate(result.candidates, "edge", "PATH_THROUGH")
    path_pattern = _candidate(result.candidates, "path_pattern", "tunnel_full_path")

    assert edge.match_type == "synonym"
    assert edge.evidence[0].source == "ai_context.synonyms"
    assert edge.evidence[0].matched_text == "经过"
    assert edge.metadata["from_vertex"] == "Tunnel"
    assert edge.metadata["to_vertex"] == "NetworkElement"
    assert edge.metadata["property_names"] == ["hop_order"]

    assert path_pattern.match_type == "text"
    assert path_pattern.evidence[0].source == "ai_context.examples"
    assert "经过哪些设备" in path_pattern.evidence[0].matched_text
    assert path_pattern.metadata["parameters"] == [
        {"name": "tunnel_id", "type": "string", "description": "Target Tunnel.id."}
    ]
    assert edge.evidence != path_pattern.evidence


def test_close_scoring_returns_sorted_candidates_without_selection_semantics(
    retriever: CandidateRetriever,
) -> None:
    result = retriever.retrieve(_decomposition("隧道 tun-mpls-001 经过哪些设备", "经过"))

    edge = _candidate(result.candidates, "edge", "PATH_THROUGH")
    path_pattern = _candidate(result.candidates, "path_pattern", "tunnel_full_path")
    assert abs(edge.score - path_pattern.score) <= 0.25

    scores = [candidate.score for candidate in result.candidates]
    assert scores == sorted(scores, reverse=True)

    serialized = result.model_dump()
    forbidden_fields = {"selected", "selection", "best", "final", "binding"}
    assert forbidden_fields.isdisjoint(serialized)
    assert all(forbidden_fields.isdisjoint(candidate) for candidate in serialized["candidates"])


def test_retriever_supports_property_and_metric_candidates(retriever: CandidateRetriever) -> None:
    result = retriever.retrieve(
        _decomposition(
            "按服务等级统计业务数量",
            "服务等级",
            "业务数量",
        )
    )

    property_candidate = _candidate(result.candidates, "property", "Service.quality_of_service")
    metric_candidate = _candidate(result.candidates, "metric", "service_count")

    assert property_candidate.semantic_name == "quality_of_service"
    assert property_candidate.owner == "Service"
    assert property_candidate.match_type == "synonym"
    assert property_candidate.metadata["property_type"] == "string"
    assert property_candidate.metadata["valid_values"] == ["GOLD", "SILVER", "BRONZE"]
    assert metric_candidate.match_type == "synonym"
    assert metric_candidate.metadata["valid_dimensions"] == ["svc.quality_of_service", "svc.service_type"]


def test_search_terms_are_unique_by_text() -> None:
    terms = _extract_search_terms(
        {
            "schema_version": "question_decomposition_v1",
            "original_question": "",
            "literal_candidates": [],
            "substantive_terms": [
                {"text": "服务", "slot": "projection"},
                {"text": "服务", "slot": "path"},
            ],
        }
    )

    assert terms == ["服务"]


def test_semantic_index_includes_all_supported_semantic_types() -> None:
    registry = load_graph_semantic_model(FIXTURE_PATH).registry

    semantic_types = {document.semantic_type for document in build_semantic_index(registry)}

    assert semantic_types == {"vertex", "edge", "property", "metric", "path_pattern"}


def test_exact_name_and_description_text_match_are_supported(retriever: CandidateRetriever) -> None:
    exact_result = retriever.retrieve(_decomposition("Service", "Service"))
    exact_candidate = _candidate(exact_result.candidates, "vertex", "Service")
    assert exact_candidate.match_type == "exact"
    assert exact_candidate.score == 1.0

    text_result = retriever.retrieve(_decomposition("Customer-facing services", "Customer-facing"))
    text_candidate = _candidate(text_result.candidates, "vertex", "Service")
    assert text_candidate.match_type == "text"
    assert text_candidate.evidence[0].source == "description"


def _decomposition(question: str, *terms: str) -> dict[str, object]:
    return {
        "question": question,
        "terms": [{"text": term} for term in terms],
    }


def _candidate(candidates, semantic_type: str, semantic_id: str):
    for candidate in candidates:
        if candidate.semantic_type == semantic_type and candidate.semantic_id == semantic_id:
            return candidate
    raise AssertionError(f"missing candidate {semantic_type}:{semantic_id}; got {candidates!r}")
