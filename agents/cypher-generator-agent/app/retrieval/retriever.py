from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry

from .index import SemanticSearchDocument, build_semantic_index
from .models import CandidateRetrievalResult, SemanticCandidate, SemanticType
from .scoring import DeterministicCandidateScorer


class CandidateRetriever:
    def __init__(
        self,
        registry: GraphSemanticRegistry,
        scorer: DeterministicCandidateScorer | None = None,
        embedding_provider: object | None = None,
    ) -> None:
        self._registry = registry
        self._scorer = scorer or DeterministicCandidateScorer()
        self._embedding_provider = embedding_provider
        self._documents = build_semantic_index(registry)

    def retrieve(
        self,
        decomposition: Mapping[str, Any] | object,
        *,
        semantic_types: set[SemanticType] | None = None,
        limit: int | None = None,
    ) -> CandidateRetrievalResult:
        terms = _extract_search_terms(decomposition)
        candidates: list[SemanticCandidate] = []

        for document in self._documents:
            if semantic_types is not None and document.semantic_type not in semantic_types:
                continue
            match = self._scorer.best_match(document, terms)
            if not match:
                continue
            candidates.append(
                SemanticCandidate(
                    semantic_type=document.semantic_type,
                    semantic_id=document.semantic_id,
                    semantic_name=document.semantic_name,
                    owner=document.owner,
                    score=match.score,
                    match_type=match.match_type,
                    evidence=[match.evidence],
                    metadata=dict(document.metadata),
                )
            )

        candidates = self._with_vertex_id_properties(candidates, semantic_types=semantic_types)
        candidates.sort(key=_candidate_sort_key)
        if limit is not None:
            candidates = candidates[:limit]
        return CandidateRetrievalResult(candidates=candidates)

    @property
    def embedding_provider(self) -> object | None:
        return self._embedding_provider

    def _with_vertex_id_properties(
        self,
        candidates: list[SemanticCandidate],
        *,
        semantic_types: set[SemanticType] | None,
    ) -> list[SemanticCandidate]:
        if semantic_types is not None and "property" not in semantic_types:
            return candidates

        existing = {(candidate.semantic_type, candidate.semantic_id) for candidate in candidates}
        documents_by_id = {
            (document.semantic_type, document.semantic_id): document for document in self._documents
        }
        additions: list[SemanticCandidate] = []
        for vertex_candidate in candidates:
            if vertex_candidate.semantic_type != "vertex":
                continue
            id_property = str(vertex_candidate.metadata.get("id_property") or "")
            if not id_property:
                continue
            property_id = f"{vertex_candidate.semantic_id}.{id_property}"
            key = ("property", property_id)
            if key in existing:
                continue
            document = documents_by_id.get(key)
            if document is None:
                continue
            additions.append(
                SemanticCandidate(
                    semantic_type=document.semantic_type,
                    semantic_id=document.semantic_id,
                    semantic_name=document.semantic_name,
                    owner=document.owner,
                    score=min(vertex_candidate.score, 0.90),
                    match_type="text",
                    evidence=[
                        {
                            "term": vertex_candidate.semantic_name,
                            "source": "vertex.id_property",
                            "matched_text": property_id,
                        }
                    ],
                    metadata=dict(document.metadata),
                )
            )
            existing.add(key)
        return [*candidates, *additions]


def _candidate_sort_key(candidate: SemanticCandidate) -> tuple[float, int, str]:
    type_priority = {
        "vertex": 5,
        "edge": 4,
        "path_pattern": 3,
        "property": 2,
        "metric": 1,
    }[candidate.semantic_type]
    return (-candidate.score, -type_priority, candidate.semantic_id)


def _extract_search_terms(decomposition: Mapping[str, Any] | object) -> list[str]:
    values: list[str] = []
    mapping = _as_mapping(decomposition)
    question = _get_value(mapping, decomposition, ("question", "source_question", "utterance", "original_question"))

    for key in (
        "terms",
        "literal_candidates",
        "substantive_terms",
        "semantic_terms",
        "coverage_terms",
        "entities",
        "relations",
        "keywords",
    ):
        raw_terms = _get_value(mapping, decomposition, (key,))
        values.extend(_flatten_terms(raw_terms))

    values.extend(_flatten_terms(_get_value(mapping, decomposition, ("term", "text"))))

    if question:
        values.append(str(question))
    return _dedupe_preserve_order(values)


def _as_mapping(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dumped
    return None


def _get_value(
    mapping: Mapping[str, Any] | None,
    source: object,
    keys: tuple[str, ...],
) -> Any:
    if mapping is not None:
        for key in keys:
            if key in mapping:
                return mapping[key]
        return None
    for key in keys:
        if hasattr(source, key):
            return getattr(source, key)
    return None


def _flatten_terms(raw_terms: Any) -> list[str]:
    if raw_terms is None:
        return []
    if isinstance(raw_terms, str):
        return [raw_terms]
    if isinstance(raw_terms, Mapping):
        for key in ("text", "term", "surface", "name", "value"):
            value = raw_terms.get(key)
            if isinstance(value, str):
                return [value]
        return []
    if isinstance(raw_terms, Sequence):
        terms: list[str] = []
        for item in raw_terms:
            terms.extend(_flatten_terms(item))
        return terms
    for key in ("text", "term", "surface", "name", "value"):
        value = getattr(raw_terms, key, None)
        if isinstance(value, str):
            return [value]
    return []


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = value.strip()
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms
