from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import CandidateRetrievalResult, SemanticCandidate


RerankDecision = Literal["kept", "demoted"]


class StructuralRerankTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_id: str
    semantic_type: str
    original_score: float
    structural_score: float
    adjusted_score: float
    decision: RerankDecision
    reason: str
    vertex_context: list[str] = Field(default_factory=list)


class StructuralRerankResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[SemanticCandidate]
    trace: list[StructuralRerankTrace]


class StructuralReranker:
    def __init__(
        self,
        *,
        unrelated_owner_weight: float = 0.50,
        unrelated_edge_weight: float = 0.85,
    ) -> None:
        self._unrelated_owner_weight = unrelated_owner_weight
        self._unrelated_edge_weight = unrelated_edge_weight

    def rerank(
        self,
        retrieval_result: CandidateRetrievalResult | Sequence[SemanticCandidate],
        *,
        structural_requirements: Mapping[str, Any] | object | None,
    ) -> StructuralRerankResult:
        candidates = _as_candidates(retrieval_result)
        vertex_context = sorted(
            {
                candidate.semantic_id
                for candidate in candidates
                if candidate.semantic_type == "vertex"
            }
        )

        if not vertex_context or not _has_structural_requirements(structural_requirements):
            return StructuralRerankResult(
                candidates=list(candidates),
                trace=[
                    _trace(
                        candidate,
                        structural_score=1.0,
                        adjusted_score=candidate.score,
                        decision="kept",
                        reason="no_vertex_context",
                        vertex_context=[],
                    )
                    for candidate in candidates
                ],
            )

        reranked: list[tuple[int, SemanticCandidate, StructuralRerankTrace]] = []
        vertex_set = set(vertex_context)
        for index, candidate in enumerate(candidates):
            structural_score, decision, reason = self._score_candidate(candidate, vertex_set)
            adjusted_score = candidate.score * structural_score
            adjusted_candidate = candidate.model_copy(update={"score": adjusted_score})
            reranked.append(
                (
                    index,
                    adjusted_candidate,
                    _trace(
                        candidate,
                        structural_score=structural_score,
                        adjusted_score=adjusted_score,
                        decision=decision,
                        reason=reason,
                        vertex_context=vertex_context,
                    ),
                )
            )

        reranked.sort(key=lambda item: (-item[1].score, item[0]))
        return StructuralRerankResult(
            candidates=[candidate for _, candidate, _ in reranked],
            trace=[trace for _, _, trace in reranked],
        )

    def _score_candidate(
        self,
        candidate: SemanticCandidate,
        vertex_set: set[str],
    ) -> tuple[float, RerankDecision, str]:
        if candidate.semantic_type in {"property", "metric"}:
            if candidate.owner in vertex_set:
                return 1.0, "kept", "owner_in_vertex_context"
            return self._unrelated_owner_weight, "demoted", "owner_not_in_vertex_context"

        if candidate.semantic_type == "edge":
            endpoints = _edge_endpoints(candidate.metadata)
            if endpoints & vertex_set:
                return 1.0, "kept", "edge_touches_vertex_context"
            return self._unrelated_edge_weight, "demoted", "edge_misses_vertex_context"

        return 1.0, "kept", "semantic_type_not_structurally_filtered"


def _as_candidates(
    retrieval_result: CandidateRetrievalResult | Sequence[SemanticCandidate],
) -> list[SemanticCandidate]:
    if isinstance(retrieval_result, CandidateRetrievalResult):
        return list(retrieval_result.candidates)
    return list(retrieval_result)


def _has_structural_requirements(structural_requirements: Mapping[str, Any] | object | None) -> bool:
    if structural_requirements is None:
        return False
    if isinstance(structural_requirements, Mapping):
        return bool(structural_requirements)
    model_dump = getattr(structural_requirements, "model_dump", None)
    if callable(model_dump):
        return bool(model_dump())
    return bool(structural_requirements)


def _edge_endpoints(metadata: Mapping[str, Any]) -> set[str]:
    endpoints: set[str] = set()
    for key in ("from", "to", "from_vertex", "to_vertex"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            endpoints.add(value)
    return endpoints


def _trace(
    candidate: SemanticCandidate,
    *,
    structural_score: float,
    adjusted_score: float,
    decision: RerankDecision,
    reason: str,
    vertex_context: list[str],
) -> StructuralRerankTrace:
    return StructuralRerankTrace(
        semantic_id=candidate.semantic_id,
        semantic_type=candidate.semantic_type,
        original_score=candidate.score,
        structural_score=structural_score,
        adjusted_score=adjusted_score,
        decision=decision,
        reason=reason,
        vertex_context=vertex_context,
    )
