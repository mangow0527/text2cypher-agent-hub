from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.cypher_generator_agent.app.decomposition.models import QuestionDecomposition
from services.cypher_generator_agent.app.literals.models import LiteralEvidence, LiteralResolverResult
from services.cypher_generator_agent.app.retrieval.models import (
    CandidateEvidence,
    CandidateRetrievalResult,
    SemanticCandidate,
)
from services.cypher_generator_agent.app.understanding import (
    GroundedUnderstanding,
    GroundedUnderstandingFailure,
    GroundedUnderstandingSelector,
)


class FakeGroundedLLMClient:
    provider = "fake-grounded-llm"

    def __init__(self, response: Mapping[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def generate_structured(
        self,
        *,
        prompt: str,
        schema_name: str,
        schema: Mapping[str, Any],
        attempt: int,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "prompt": prompt,
                "schema_name": schema_name,
                "schema": schema,
                "attempt": attempt,
            }
        )
        return self.response


def test_invented_service_tunnel_short_edge_name_is_rejected() -> None:
    client = FakeGroundedLLMClient(
        {
            "schema_version": "grounded_understanding_v1",
            "status": "grounded",
            "query_shape": "single_hop",
            "selected_bindings": [
                _binding("source", "vertex", "Service"),
                _binding("target", "vertex", "Tunnel"),
                _binding("relation", "edge", "USES_TUNNEL"),
            ],
            "selected_literals": [],
            "filters": [],
            "projection": [],
            "coverage": _coverage(["Gold", "服务", "使用", "隧道"]),
            "unsupported": None,
            "confidence": 0.8,
        }
    )

    result = GroundedUnderstandingSelector(client).select(
        question_decomposition=_decomposition(),
        candidates=_candidates(
            _candidate("vertex", "Service"),
            _candidate("vertex", "Tunnel"),
            _candidate("edge", "SERVICE_USES_TUNNEL"),
        ),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstandingFailure)
    assert result.status == "generation_failed"
    assert result.reason == "semantic_match_rejected"
    assert result.error_type == "CandidateBoundaryError"
    assert "edge:USES_TUNNEL" in result.message
    assert len(client.calls) == 1


def test_candidate_id_with_mismatched_registry_name_is_rejected() -> None:
    client = FakeGroundedLLMClient(
        {
            "schema_version": "grounded_understanding_v1",
            "status": "grounded",
            "query_shape": "single_hop",
            "selected_bindings": [
                {
                    "role": "relation",
                    "semantic_type": "edge",
                    "candidate_id": "edge:SERVICE_USES_TUNNEL",
                    "semantic_id": "SERVICE_USES_TUNNEL",
                    "semantic_name": "USES_TUNNEL",
                }
            ],
            "selected_literals": [],
            "filters": [],
            "projection": [],
            "coverage": _coverage(["服务", "使用", "隧道"]),
            "unsupported": None,
            "confidence": 0.8,
        }
    )

    result = GroundedUnderstandingSelector(client).select(
        question_decomposition=_decomposition(),
        candidates=_candidates(_candidate("edge", "SERVICE_USES_TUNNEL")),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstandingFailure)
    assert result.reason == "semantic_match_rejected"
    assert "semantic_name" in result.message
    assert "SERVICE_USES_TUNNEL" in result.message


def test_registry_name_existing_outside_candidate_set_is_rejected() -> None:
    client = FakeGroundedLLMClient(
        {
            "schema_version": "grounded_understanding_v1",
            "status": "grounded",
            "query_shape": "lookup",
            "selected_bindings": [_binding("target", "vertex", "Tunnel")],
            "selected_literals": [],
            "filters": [],
            "projection": [],
            "coverage": _coverage(["隧道"]),
            "unsupported": None,
            "confidence": 0.8,
        }
    )

    result = GroundedUnderstandingSelector(client).select(
        question_decomposition=_decomposition(),
        candidates=_candidates(_candidate("vertex", "Service")),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstandingFailure)
    assert result.reason == "semantic_match_rejected"
    assert "vertex:Tunnel" in result.message
    assert "candidate set" in result.message


def test_compact_candidate_id_outside_candidate_set_is_rejected() -> None:
    client = FakeGroundedLLMClient(
        {
            "schema_version": "grounded_understanding_v1",
            "status": "grounded",
            "query_shape": "lookup",
            "selected_bindings": [{"candidate_id": "vertex:Tunnel"}],
        }
    )

    result = GroundedUnderstandingSelector(client).select(
        question_decomposition=_decomposition(),
        candidates=_candidates(_candidate("vertex", "Service")),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstandingFailure)
    assert result.status == "generation_failed"
    assert result.reason == "semantic_match_rejected"
    assert result.error_type == "CandidateBoundaryError"
    assert "vertex:Tunnel" in result.message
    assert "candidate set" in result.message


def test_compact_binding_with_extra_semantic_fields_is_schema_invalid() -> None:
    client = FakeGroundedLLMClient(
        {
            "schema_version": "grounded_understanding_v1",
            "status": "grounded",
            "query_shape": "lookup",
            "selected_bindings": [
                {
                    "candidate_id": "vertex:Service",
                    "semantic_type": "metric",
                    "semantic_id": "device_count",
                }
            ],
        }
    )

    result = GroundedUnderstandingSelector(client, max_schema_retries=0).select(
        question_decomposition=_decomposition(),
        candidates=_candidates(_candidate("vertex", "Service")),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstandingFailure)
    assert result.status == "generation_failed"
    assert result.reason == "grounded_understanding_schema_invalid"
    assert "compact selected_bindings" in result.errors[-1].message


def test_close_candidates_are_preserved_as_ambiguity_without_forced_selection() -> None:
    client = FakeGroundedLLMClient(
        {
            "schema_version": "grounded_understanding_v1",
            "status": "grounded",
            "query_shape": "variable_path",
            "selected_bindings": [_binding("anchor", "vertex", "Tunnel")],
            "selected_literals": [],
            "filters": [],
            "projection": [],
            "ambiguities": [
                {
                    "role": "path_semantics",
                    "reason": "Both direct PATH_THROUGH and tunnel_full_path are plausible.",
                    "candidate_ids": ["edge:PATH_THROUGH", "path_pattern:tunnel_full_path"],
                }
            ],
            "coverage": _coverage(["隧道", "经过", "设备"]),
            "unsupported": None,
            "confidence": 0.72,
        }
    )

    result = GroundedUnderstandingSelector(client).select(
        question_decomposition=_path_decomposition(),
        candidates=_candidates(
            _candidate("vertex", "Tunnel"),
            _candidate("edge", "PATH_THROUGH", score=0.86),
            _candidate("path_pattern", "tunnel_full_path", score=0.82),
        ),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstanding)
    assert result.ambiguities[0].candidate_ids == ["edge:PATH_THROUGH", "path_pattern:tunnel_full_path"]

    binder_payload = result.to_binder_payload()
    assert binder_payload["selected_vertices"] == [{"name": "Tunnel", "semantic_id": "Tunnel"}]
    assert binder_payload["selected_edges"] == []
    assert binder_payload["selected_path_patterns"] == []


def test_selected_literal_not_from_input_literal_results_is_rejected() -> None:
    client = FakeGroundedLLMClient(
        {
            "schema_version": "grounded_understanding_v1",
            "status": "grounded",
            "query_shape": "single_hop",
            "selected_bindings": [_binding("target", "vertex", "Service")],
            "selected_literals": [
                {
                    "schema_version": "literal_resolver_result_v1",
                    "raw_literal": "Gold",
                    "resolved": True,
                    "resolved_value": "GOLD",
                    "normalized_value": "GOLD",
                    "match_type": "value_synonym",
                    "confidence": 0.98,
                    "expected_vertex": "Service",
                    "expected_property": "quality_of_service",
                    "evidence": [],
                }
            ],
            "filters": [],
            "projection": [],
            "coverage": _coverage(["Gold", "服务"]),
            "unsupported": None,
            "confidence": 0.9,
        }
    )

    result = GroundedUnderstandingSelector(client).select(
        question_decomposition=_decomposition(),
        candidates=_candidates(_candidate("vertex", "Service")),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstandingFailure)
    assert result.status == "generation_failed"
    assert result.reason == "semantic_match_rejected"
    assert "literal resolver result" in result.message


def test_selected_literal_mutating_resolver_confidence_or_evidence_is_rejected() -> None:
    trusted_literal = LiteralResolverResult(
        raw_literal="Gold",
        resolved=True,
        resolved_value="GOLD",
        normalized_value="GOLD",
        match_type="value_synonym",
        confidence=0.98,
        expected_vertex="Service",
        expected_property="quality_of_service",
        evidence=[
            LiteralEvidence(source="property.value_synonyms", matched="Gold", target="GOLD")
        ],
    )
    mutated_literal = trusted_literal.model_dump(mode="json")
    mutated_literal["confidence"] = 1.0
    mutated_literal["evidence"] = []

    client = FakeGroundedLLMClient(
        {
            "schema_version": "grounded_understanding_v1",
            "status": "grounded",
            "query_shape": "single_hop",
            "selected_bindings": [_binding("target", "vertex", "Service")],
            "selected_literals": [mutated_literal],
            "filters": [],
            "projection": [],
            "coverage": _coverage(["Gold", "服务"]),
            "unsupported": None,
            "confidence": 0.9,
        }
    )

    result = GroundedUnderstandingSelector(client).select(
        question_decomposition=_decomposition(),
        candidates=_candidates(_candidate("vertex", "Service")),
        literal_results=[trusted_literal],
    )

    assert isinstance(result, GroundedUnderstandingFailure)
    assert result.status == "generation_failed"
    assert result.reason == "semantic_match_rejected"
    assert "literal resolver result" in result.message


def _decomposition() -> QuestionDecomposition:
    return QuestionDecomposition(
        result_type="decomposition",
        intent_type="list",
        original_question="Gold 服务使用了哪些隧道",
        literal_candidates=[],
        substantive_terms=[
            {"text": "Gold", "slot": "filter", "attached_to": "服务"},
            {"text": "服务", "slot": "path"},
            {"text": "使用", "slot": "path"},
            {"text": "隧道", "slot": "projection"},
        ],
        modality_terms=[],
        time_terms=[],
        unparsed_terms=[],
        output_shape="rows",
    )


def _path_decomposition() -> QuestionDecomposition:
    return QuestionDecomposition(
        result_type="decomposition",
        intent_type="path",
        original_question="隧道 tun-mpls-001 经过哪些设备",
        literal_candidates=[],
        substantive_terms=[
            {"text": "隧道", "slot": "path"},
            {"text": "经过", "slot": "path"},
            {"text": "设备", "slot": "projection"},
        ],
        modality_terms=[],
        time_terms=[],
        unparsed_terms=[],
        output_shape="path",
    )


def _binding(
    role: str,
    semantic_type: str,
    semantic_id: str,
    *,
    semantic_name: str | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": role,
        "semantic_type": semantic_type,
        "candidate_id": f"{semantic_type}:{semantic_id}",
        "semantic_id": semantic_id,
        "semantic_name": semantic_name or semantic_id,
    }
    if owner is not None:
        payload["owner"] = owner
    return payload


def _candidates(*candidates: SemanticCandidate) -> CandidateRetrievalResult:
    return CandidateRetrievalResult(candidates=list(candidates))


def _candidate(
    semantic_type: str,
    semantic_id: str,
    *,
    owner: str | None = None,
    semantic_name: str | None = None,
    score: float = 1.0,
) -> SemanticCandidate:
    return SemanticCandidate(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
        semantic_name=semantic_name or semantic_id,
        owner=owner,
        score=score,
        match_type="exact",
        evidence=[
            CandidateEvidence(
                term=semantic_id,
                source="test",
                matched_text=semantic_id,
            )
        ],
    )


def _coverage(covered: list[str], *, uncovered: list[str] | None = None) -> dict[str, Any]:
    covered_terms = covered
    uncovered_terms = uncovered or []
    return {
        "substantive_terms": {
            "total": len(covered_terms) + len(uncovered_terms),
            "covered": len(covered_terms),
            "uncovered": uncovered_terms,
        }
    }
