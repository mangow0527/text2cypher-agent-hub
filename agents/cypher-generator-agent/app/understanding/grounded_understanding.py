from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from services.cypher_generator_agent.app.retrieval.models import (
    CandidateRetrievalResult,
    SemanticCandidate,
)
from services.cypher_generator_agent.app.literals.models import LiteralResolverResult
from services.cypher_generator_agent.app.validation.coverage import CoverageReport

from .llm_client import GroundedLLMClient
from .models import (
    CompactGroundedUnderstanding,
    GROUNDED_UNDERSTANDING_SCHEMA_VERSION,
    GroundedBinding,
    GroundedUnderstanding,
    GroundedUnderstandingAttemptError,
    GroundedUnderstandingFailure,
    GroundedUnderstandingOutcome,
    parse_grounded_understanding_response,
)
from .prompt import (
    build_grounded_understanding_prompt,
    build_grounded_understanding_schema,
    candidate_id,
)


class CandidateBoundaryError(ValueError):
    """Raised when LLM output references semantics outside the candidate set."""


class GroundedUnderstandingSelector:
    def __init__(self, llm_client: GroundedLLMClient, *, max_schema_retries: int = 2) -> None:
        if max_schema_retries < 0:
            raise ValueError("max_schema_retries must be non-negative")
        self._llm_client = llm_client
        self._max_schema_retries = max_schema_retries

    def select(
        self,
        *,
        question_decomposition: Mapping[str, Any] | object,
        candidates: CandidateRetrievalResult | Sequence[SemanticCandidate] | Mapping[str, Any],
        literal_results: Sequence[object],
    ) -> GroundedUnderstandingOutcome:
        prompt = build_grounded_understanding_prompt(
            question_decomposition=question_decomposition,
            candidates=candidates,
            literal_results=literal_results,
        )
        schema = build_grounded_understanding_schema()
        provider = _provider_name(self._llm_client)
        errors: list[GroundedUnderstandingAttemptError] = []

        for attempt in range(1, self._max_schema_retries + 2):
            try:
                payload = self._llm_client.generate_structured(
                    prompt=prompt,
                    schema_name=GROUNDED_UNDERSTANDING_SCHEMA_VERSION,
                    schema=schema,
                    attempt=attempt,
                )
            except Exception as exc:
                errors.append(_attempt_error(attempt, exc))
                return GroundedUnderstandingFailure(
                    status="service_failed",
                    reason="model_invocation_failed",
                    message=str(exc) or "LLM provider invocation failed.",
                    provider=provider,
                    error_type=exc.__class__.__name__,
                    attempts=attempt,
                    retry_count=attempt - 1,
                    errors=errors,
                )

            try:
                parsed = parse_grounded_understanding_response(payload)
            except (ValidationError, ValueError) as exc:
                errors.append(_attempt_error(attempt, exc))
                if attempt <= self._max_schema_retries:
                    continue
                return GroundedUnderstandingFailure(
                    status="generation_failed",
                    reason="grounded_understanding_schema_invalid",
                    message="LLM output did not satisfy grounded_understanding_v1.",
                    provider=provider,
                    error_type=exc.__class__.__name__,
                    attempts=attempt,
                    retry_count=attempt - 1,
                    errors=errors,
                )

            try:
                response = hydrate_grounded_understanding(
                    parsed,
                    candidates,
                    literal_results=literal_results,
                    question_decomposition=question_decomposition,
                )
                validate_candidate_boundaries(response, candidates, literal_results=literal_results)
            except CandidateBoundaryError as exc:
                errors.append(_attempt_error(attempt, exc))
                return GroundedUnderstandingFailure(
                    status="generation_failed",
                    reason="semantic_match_rejected",
                    message=str(exc),
                    provider=provider,
                    error_type=exc.__class__.__name__,
                    attempts=attempt,
                    retry_count=attempt - 1,
                    errors=errors,
                )
            except ValidationError as exc:
                errors.append(_attempt_error(attempt, exc))
                if attempt <= self._max_schema_retries:
                    continue
                return GroundedUnderstandingFailure(
                    status="generation_failed",
                    reason="grounded_understanding_schema_invalid",
                    message="LLM output did not satisfy grounded_understanding_v1.",
                    provider=provider,
                    error_type=exc.__class__.__name__,
                    attempts=attempt,
                    retry_count=attempt - 1,
                    errors=errors,
                )
            return response

        raise RuntimeError("unreachable grounded understanding retry state")


def hydrate_grounded_understanding(
    understanding: CompactGroundedUnderstanding | GroundedUnderstanding,
    candidates: CandidateRetrievalResult | Sequence[SemanticCandidate] | Mapping[str, Any],
    *,
    literal_results: Sequence[object] = (),
    question_decomposition: Mapping[str, Any] | object | None = None,
) -> GroundedUnderstanding:
    if isinstance(understanding, GroundedUnderstanding):
        return understanding

    index = _CandidateBoundaryIndex(_coerce_candidates(candidates))
    selected_literals = _hydrate_selected_literals(understanding.selected_literal_ids, literal_results)
    selected_bindings = [
        _hydrate_binding(binding, index) for binding in understanding.selected_bindings
    ]
    operation_context = _OperationHydrationContext(index=index, selected_bindings=selected_bindings)
    group_by = operation_context.normalize_group_by(understanding.group_by)
    measures = operation_context.normalize_measures(understanding.measures)
    sort = operation_context.normalize_sort(understanding.sort)
    projection = operation_context.normalize_projection(
        understanding.projection,
        query_shape=understanding.query_shape,
    )
    return GroundedUnderstanding(
        schema_version=understanding.schema_version,
        status=understanding.status,
        query_shape=understanding.query_shape,
        selected_bindings=selected_bindings,
        selected_literals=selected_literals,
        filters=_normalize_mapping_list(understanding.filters),
        projection=projection,
        group_by=group_by,
        measures=measures,
        sort=sort,
        limit=understanding.limit,
        assumptions=_normalize_assumptions(understanding.assumptions),
        ambiguities=understanding.ambiguities,
        coverage=_hydrated_coverage(
            status=understanding.status,
            question_decomposition=question_decomposition,
        ),
        unsupported=understanding.unsupported,
        confidence=0.0,
    )


def validate_candidate_boundaries(
    understanding: GroundedUnderstanding,
    candidates: CandidateRetrievalResult | Sequence[SemanticCandidate] | Mapping[str, Any],
    *,
    literal_results: Sequence[object] = (),
) -> None:
    index = _CandidateBoundaryIndex(_coerce_candidates(candidates))
    for binding in understanding.selected_bindings:
        _validate_binding(binding, index)
    for ambiguity in understanding.ambiguities:
        for ambiguity_candidate_id in ambiguity.candidate_ids:
            index.require(ambiguity_candidate_id)

    _validate_filter_references(understanding.filters, index)
    _validate_projection_references(understanding.projection, index, field_name="projection")
    _validate_projection_references(understanding.sort, index, field_name="sort")
    _validate_group_by_references(understanding.group_by, index)
    _validate_literal_references(understanding, literal_results)


def _hydrate_binding(
    binding: Any,
    index: "_CandidateBoundaryIndex",
) -> GroundedBinding:
    candidate = index.require(binding.candidate_id)
    return GroundedBinding(
        role=binding.role or candidate.semantic_type,
        semantic_type=candidate.semantic_type,
        candidate_id=binding.candidate_id,
        semantic_id=candidate.semantic_id,
        semantic_name=candidate.semantic_name,
        owner=candidate.owner,
        direction=binding.direction,
    )


def _hydrate_selected_literals(
    selected_literal_ids: Sequence[str],
    literal_results: Sequence[object],
) -> list[LiteralResolverResult]:
    literal_index = {
        f"literal:{index}": (
            literal_result
            if isinstance(literal_result, LiteralResolverResult)
            else LiteralResolverResult.model_validate(literal_result)
        )
        for index, literal_result in enumerate(literal_results)
    }
    selected_literals: list[LiteralResolverResult] = []
    for literal_id in selected_literal_ids:
        try:
            selected_literals.append(literal_index[literal_id])
        except KeyError as exc:
            raise CandidateBoundaryError(
                f"literal_id {literal_id} is not present in literal resolver results"
            ) from exc
    return selected_literals


def _hydrated_coverage(
    *,
    status: str,
    question_decomposition: Mapping[str, Any] | object | None,
) -> CoverageReport:
    terms = _substantive_term_texts(question_decomposition)
    if status == "grounded":
        return CoverageReport.model_validate(
            {
                "substantive_terms": {
                    "total": len(terms),
                    "covered": len(terms),
                    "uncovered": [],
                }
            }
        )
    return CoverageReport.model_validate(
        {
            "substantive_terms": {
                "total": len(terms),
                "covered": 0,
                "uncovered": terms,
            }
        }
    )


def _substantive_term_texts(question_decomposition: Mapping[str, Any] | object | None) -> list[str]:
    payload = _decomposition_payload(question_decomposition)
    raw_terms = payload.get("substantive_terms")
    if not isinstance(raw_terms, list | tuple):
        return []
    terms: list[str] = []
    for item in raw_terms:
        if not isinstance(item, Mapping):
            continue
        text = item.get("text")
        if text is not None:
            terms.append(str(text))
    return terms


def _decomposition_payload(question_decomposition: Mapping[str, Any] | object | None) -> dict[str, Any]:
    if question_decomposition is None:
        return {}
    model_dump = getattr(question_decomposition, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            return dumped
    if isinstance(question_decomposition, Mapping):
        return dict(question_decomposition)
    return {}


class _OperationHydrationContext:
    _AGGREGATE_QUERY_SHAPES = {"ad_hoc_aggregate", "metric_aggregate", "top_n", "two_step_aggregate"}

    def __init__(
        self,
        *,
        index: "_CandidateBoundaryIndex",
        selected_bindings: Sequence[GroundedBinding],
    ) -> None:
        self.index = index
        self.selected_bindings = list(selected_bindings)
        self.group_alias_by_label: dict[str, str] = {}
        self.measure_alias_by_label: dict[str, str] = {}

    def normalize_group_by(self, values: Sequence[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for value in values or []:
            if isinstance(value, Mapping) and _is_dimension_item(value):
                item = dict(value)
                label = _dimension_label(item)
                if label:
                    self.group_alias_by_label[label] = str(item["alias"])
                normalized.append(item)
                continue
            prop = self._property_ref(value, field_name="group_by")
            if prop is None:
                normalized.append(dict(value) if isinstance(value, Mapping) else {"label": str(value)})
                continue
            alias = _alias_for_property(prop["owner"], prop["name"])
            self.group_alias_by_label[_property_label(prop)] = alias
            self.group_alias_by_label[prop["name"]] = alias
            normalized.append(
                {
                    "alias": alias,
                    "target": _snake_case(prop["owner"]),
                    "property": prop,
                }
            )
        return normalized

    def normalize_measures(self, values: Sequence[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for value in values or []:
            if isinstance(value, Mapping) and _is_measure_item(value):
                item = dict(value)
                label = _measure_label(item)
                if label:
                    self.measure_alias_by_label[label] = str(item["alias"])
                normalized.append(item)
                continue
            item = self._measure_from_hint(value)
            if item is None:
                normalized.append(dict(value) if isinstance(value, Mapping) else {"label": str(value)})
                continue
            label = _measure_label(item)
            if label:
                self.measure_alias_by_label[label] = str(item["alias"])
            normalized.append(item)
        return normalized

    def normalize_sort(self, values: Sequence[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for value in values or []:
            if isinstance(value, Mapping):
                item = dict(value)
                if "source" in item:
                    normalized.append(item)
                    continue
                source = self._source_for_label(_string_hint(item))
                if source:
                    normalized.append({"source": source, "direction": _sort_direction(item)})
                    continue
                normalized.append(item)
                continue
            text = str(value).strip()
            source = self._source_for_label(text)
            if source:
                normalized.append({"source": source, "direction": _sort_direction(text)})
            elif text:
                normalized.append({"source": text, "direction": _sort_direction(text)})
        return normalized

    def normalize_projection(self, values: Sequence[Any], *, query_shape: str) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        aggregate_shape = query_shape in self._AGGREGATE_QUERY_SHAPES
        for value in values or []:
            if isinstance(value, Mapping):
                item = dict(value)
                if "source" in item or item.get("semantic_type") == "vertex_full":
                    normalized.append(item)
                    continue
                if item.get("semantic_type") == "vertex":
                    vertex_full = self._vertex_full_projection(item)
                    normalized.append(vertex_full or item)
                    continue
                source_item = self._source_projection_from_item(item)
                if aggregate_shape and source_item is not None:
                    normalized.append(source_item)
                    continue
                prop = self._property_ref(item, field_name="projection")
                if prop is not None:
                    source_item = self._aggregate_source_projection(prop) if aggregate_shape else None
                    normalized.append(source_item or _property_projection(prop, alias=item.get("alias")))
                    continue
                normalized.append(item)
                continue
            text = str(value).strip()
            if not text:
                continue
            source = self._source_for_label(text)
            if aggregate_shape and source:
                normalized.append({"alias": source.split(".", 1)[1], "source": source})
                continue
            prop = self._property_ref(text, field_name="projection")
            if prop is not None:
                source_item = self._aggregate_source_projection(prop) if aggregate_shape else None
                normalized.append(source_item or _property_projection(prop))
                continue
            normalized.append({"label": text})
        return normalized

    def _aggregate_source_projection(self, prop: Mapping[str, str]) -> dict[str, Any] | None:
        alias = self.group_alias_by_label.get(_property_label(prop)) or self.group_alias_by_label.get(prop["name"])
        if alias:
            return {"alias": alias, "source": f"group.{alias}"}
        return None

    def _source_projection_from_item(self, item: Mapping[str, Any]) -> dict[str, Any] | None:
        for hint in _source_hints(item):
            source = self._source_for_label(hint)
            if source:
                alias = item.get("alias") or source.split(".", 1)[1]
                return {"alias": str(alias), "source": source}
        return None

    def _vertex_full_projection(self, item: Mapping[str, Any]) -> dict[str, Any] | None:
        vertex = item.get("semantic_id") or item.get("name") or item.get("vertex") or item.get("vertex_full")
        if not vertex:
            return None
        candidate = self.index.require(f"vertex:{vertex}")
        payload: dict[str, Any] = {
            "semantic_type": "vertex_full",
            "name": candidate.semantic_id,
        }
        alias = item.get("alias")
        if alias:
            payload["alias"] = alias
        projection_terms = item.get("projection_terms")
        if projection_terms:
            payload["projection_terms"] = projection_terms
        return payload

    def _source_for_label(self, raw: Any) -> str | None:
        text = str(raw or "").strip()
        if not text:
            return None
        label = _sort_label(text)
        if label in self.measure_alias_by_label:
            return f"measure.{self.measure_alias_by_label[label]}"
        if label in self.group_alias_by_label:
            return f"group.{self.group_alias_by_label[label]}"
        return None

    def _measure_from_hint(self, value: Any) -> dict[str, Any] | None:
        text = _string_hint(value)
        if not text:
            return None
        count_match = re.search(r"count\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", text, flags=re.IGNORECASE)
        if not count_match:
            return None
        owner = count_match.group(1)
        property_name = "id"
        self.index.require_property_candidate(owner, property_name, field_name="measures")
        alias_text = _extract_alias_text(text)
        alias = "cnt" if alias_text else f"{_snake_case(owner)}_count"
        labels = {text, "count", f"count({owner})"}
        if alias_text:
            labels.add(alias_text)
        item = {
            "alias": alias,
            "function": "count",
            "target": _snake_case(owner),
            "property": {"owner": owner, "name": property_name},
        }
        if alias_text:
            item["projection_terms"] = [alias_text]
        for label in labels:
            self.measure_alias_by_label[label] = alias
        return item

    def _property_ref(self, value: Any, *, field_name: str) -> dict[str, str] | None:
        if isinstance(value, Mapping):
            nested = value.get("property")
            if isinstance(nested, Mapping):
                owner = nested.get("owner")
                name = nested.get("name") or nested.get("property_name")
                if owner and name:
                    self.index.require_property_candidate(str(owner), str(name), field_name=field_name)
                    return {"owner": str(owner), "name": str(name)}
            owner = value.get("owner")
            name = value.get("name") or value.get("property") or value.get("property_name")
            semantic_id = value.get("semantic_id")
            if (owner is None or name is None) and isinstance(semantic_id, str) and "." in semantic_id:
                owner, name = semantic_id.split(".", 1)
            if isinstance(name, str) and "." in name and owner is None:
                owner, name = name.split(".", 1)
            if owner and name:
                self.index.require_property_candidate(str(owner), str(name), field_name=field_name)
                return {"owner": str(owner), "name": str(name)}
            return None
        text = str(value).strip()
        if text.startswith("property:"):
            text = text.split(":", 1)[1]
        if "." not in text:
            return None
        owner, name = text.split(".", 1)
        if not owner or not name:
            return None
        self.index.require_property_candidate(owner, name, field_name=field_name)
        return {"owner": owner, "name": name}


def _normalize_mapping_list(values: Sequence[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for value in values or []:
        if isinstance(value, Mapping):
            normalized.append(dict(value))
        else:
            normalized.append({"value": value})
    return normalized


def _normalize_assumptions(values: Sequence[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for value in values or []:
        if isinstance(value, Mapping):
            normalized.append(dict(value))
        else:
            normalized.append({"type": "llm_assumption", "message": str(value)})
    return normalized


def _is_dimension_item(value: Mapping[str, Any]) -> bool:
    return isinstance(value.get("property"), Mapping) and bool(value.get("alias")) and bool(value.get("target"))


def _is_measure_item(value: Mapping[str, Any]) -> bool:
    return (
        isinstance(value.get("property"), Mapping)
        and bool(value.get("alias"))
        and bool(value.get("target"))
        and bool(value.get("function"))
    )


def _property_projection(prop: Mapping[str, str], *, alias: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "semantic_type": "property",
        "owner": prop["owner"],
        "name": prop["name"],
    }
    if alias is not None:
        payload["alias"] = alias
    return payload


def _property_label(prop: Mapping[str, str]) -> str:
    return f"{prop['owner']}.{prop['name']}"


def _alias_for_property(owner: str, name: str) -> str:
    return f"{_snake_case(owner)}_{_snake_case(name)}"


def _dimension_label(item: Mapping[str, Any]) -> str | None:
    prop = item.get("property")
    if not isinstance(prop, Mapping):
        return None
    owner = prop.get("owner")
    name = prop.get("name") or prop.get("property_name")
    if not owner or not name:
        return None
    return f"{owner}.{name}"


def _measure_label(item: Mapping[str, Any]) -> str | None:
    alias = item.get("alias")
    return str(alias) if alias else None


def _string_hint(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("source", "label", "name", "alias", "semantic_id"):
            raw = value.get(key)
            if raw:
                return str(raw)
        return ""
    return str(value or "")


def _source_hints(value: Mapping[str, Any]) -> list[str]:
    hints: list[str] = []
    for key in ("source", "label", "alias", "name", "semantic_id"):
        raw = value.get(key)
        if raw:
            hints.append(str(raw))
    return hints


def _sort_label(value: str) -> str:
    text = value.strip()
    text = re.sub(r"\s+(asc|desc|ascending|descending|升序|降序)\s*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def _sort_direction(value: Any) -> str:
    if isinstance(value, Mapping):
        raw = value.get("direction") or value.get("order") or value.get("sort")
        if raw is None:
            raw = _string_hint(value)
    else:
        raw = str(value)
    text = str(raw).lower()
    if "desc" in text or "降序" in text or "最多" in text:
        return "desc"
    return "asc"


def _extract_alias_text(value: str) -> str | None:
    match = re.search(r"\bas\s+(.+)$", value, flags=re.IGNORECASE)
    if not match:
        return None
    alias = match.group(1).strip()
    return alias or None


def _snake_case(value: str) -> str:
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"[^0-9A-Za-z_]+", "_", text).strip("_").lower()
    return text or "value"


class _CandidateBoundaryIndex:
    def __init__(self, candidates: Sequence[SemanticCandidate]) -> None:
        self._by_candidate_id = {candidate_id(candidate): candidate for candidate in candidates}
        self._by_property_key = {
            candidate.semantic_id: candidate
            for candidate in candidates
            if candidate.semantic_type == "property"
        }
        self._by_owner_name = {
            (candidate.owner, candidate.semantic_name): candidate
            for candidate in candidates
            if candidate.semantic_type == "property" and candidate.owner is not None
        }

    def require(self, selected_candidate_id: str) -> SemanticCandidate:
        try:
            return self._by_candidate_id[selected_candidate_id]
        except KeyError as exc:
            raise CandidateBoundaryError(
                f"candidate_id {selected_candidate_id} is not present in candidate set"
            ) from exc

    def property_candidate(self, owner: str, name: str) -> SemanticCandidate | None:
        return self._by_property_key.get(f"{owner}.{name}") or self._by_owner_name.get((owner, name))

    def require_property_candidate(self, owner: str, name: str, *, field_name: str) -> SemanticCandidate:
        candidate = self.property_candidate(owner, name)
        if candidate is None:
            raise CandidateBoundaryError(
                f"{field_name} property {owner}.{name} is not present in candidate set"
            )
        return candidate

    def require_semantic_reference(
        self,
        *,
        semantic_type: str,
        semantic_id: str,
        semantic_name: str | None = None,
        owner: str | None = None,
        field_name: str,
    ) -> None:
        selected_candidate_id = f"{semantic_type}:{semantic_id}"
        candidate = self.require(selected_candidate_id)
        if candidate.semantic_type != semantic_type:
            raise CandidateBoundaryError(
                f"{field_name} semantic_type mismatch for {selected_candidate_id}: "
                f"output={semantic_type}, candidate={candidate.semantic_type}"
            )
        if candidate.semantic_id != semantic_id:
            raise CandidateBoundaryError(
                f"{field_name} semantic_id mismatch for {selected_candidate_id}: "
                f"output={semantic_id}, candidate={candidate.semantic_id}"
            )
        if semantic_name is not None and candidate.semantic_name != semantic_name:
            raise CandidateBoundaryError(
                f"{field_name} semantic_name mismatch for {selected_candidate_id}: "
                f"output={semantic_name}, candidate={candidate.semantic_name}"
            )
        if owner != candidate.owner:
            raise CandidateBoundaryError(
                f"{field_name} owner mismatch for {selected_candidate_id}: "
                f"output={owner}, candidate={candidate.owner}"
            )


def _validate_binding(binding: GroundedBinding, index: _CandidateBoundaryIndex) -> None:
    candidate = index.require(binding.candidate_id)
    if binding.candidate_id != candidate_id(candidate):
        raise CandidateBoundaryError(
            f"candidate_id mismatch for {binding.candidate_id}: candidate payload is {candidate_id(candidate)}"
        )
    if binding.semantic_type != candidate.semantic_type:
        raise CandidateBoundaryError(
            f"semantic_type mismatch for {binding.candidate_id}: "
            f"output={binding.semantic_type}, candidate={candidate.semantic_type}"
        )
    if binding.semantic_id != candidate.semantic_id:
        raise CandidateBoundaryError(
            f"semantic_id mismatch for {binding.candidate_id}: "
            f"output={binding.semantic_id}, candidate={candidate.semantic_id}"
        )
    if binding.semantic_name != candidate.semantic_name:
        raise CandidateBoundaryError(
            f"semantic_name mismatch for {binding.candidate_id}: "
            f"output={binding.semantic_name}, candidate={candidate.semantic_name}"
        )
    if binding.owner != candidate.owner:
        raise CandidateBoundaryError(
            f"owner mismatch for {binding.candidate_id}: output={binding.owner}, candidate={candidate.owner}"
        )


def _validate_filter_references(filters: list[dict[str, Any]], index: _CandidateBoundaryIndex) -> None:
    for item in filters:
        owner = item.get("owner")
        property_name = item.get("property") or item.get("property_name")
        if owner is None or property_name is None:
            continue
        index.require_semantic_reference(
            semantic_type="property",
            semantic_id=f"{owner}.{property_name}",
            semantic_name=str(property_name),
            owner=str(owner),
            field_name="filters",
        )


def _validate_projection_references(
    items: list[dict[str, Any]],
    index: _CandidateBoundaryIndex,
    *,
    field_name: str,
) -> None:
    for item in items:
        if "source" in item and "semantic_type" not in item and "property" not in item:
            continue
        semantic_type = item.get("semantic_type")
        if semantic_type is None and _looks_like_property_reference(item):
            semantic_type = "property"
        if semantic_type is None:
            continue
        _validate_reference_item(item, str(semantic_type), index, field_name=field_name)


def _validate_group_by_references(group_by: list[dict[str, Any]], index: _CandidateBoundaryIndex) -> None:
    for item in group_by:
        property_ref = item.get("property")
        if not isinstance(property_ref, Mapping):
            continue
        owner = property_ref.get("owner")
        property_name = property_ref.get("name") or property_ref.get("property_name")
        if owner is None or property_name is None:
            continue
        index.require_semantic_reference(
            semantic_type="property",
            semantic_id=f"{owner}.{property_name}",
            semantic_name=str(property_name),
            owner=str(owner),
            field_name="group_by",
        )


def _validate_literal_references(
    understanding: GroundedUnderstanding,
    literal_results: Sequence[object],
) -> None:
    allowed = {_literal_fingerprint(literal_result) for literal_result in literal_results}
    for literal in understanding.selected_literals:
        fingerprint = _literal_fingerprint(literal)
        if fingerprint not in allowed:
            raise CandidateBoundaryError(
                "selected literal resolver result is not present in input literal_results: "
                f"{literal.raw_literal} -> {literal.expected_vertex or literal.expected_edge}."
                f"{literal.expected_property}"
            )


def _literal_fingerprint(item: object) -> tuple[Any, ...]:
    result = item if isinstance(item, LiteralResolverResult) else LiteralResolverResult.model_validate(item)
    return (json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),)


def _validate_reference_item(
    item: Mapping[str, Any],
    semantic_type: str,
    index: _CandidateBoundaryIndex,
    *,
    field_name: str,
) -> None:
    if semantic_type == "property":
        owner, property_name = _owner_property(item)
        index.require_semantic_reference(
            semantic_type="property",
            semantic_id=f"{owner}.{property_name}",
            semantic_name=property_name,
            owner=owner,
            field_name=field_name,
        )
        return
    if semantic_type == "vertex_full":
        semantic_id = str(
            item.get("semantic_id") or item.get("name") or item.get("vertex_full") or item.get("vertex") or ""
        )
        if not semantic_id:
            return
        semantic_name = item.get("semantic_name") or item.get("name") or semantic_id
        index.require_semantic_reference(
            semantic_type="vertex",
            semantic_id=semantic_id,
            semantic_name=str(semantic_name) if semantic_name is not None else None,
            owner=None,
            field_name=field_name,
        )
        return

    semantic_id = str(item.get("semantic_id") or item.get("name") or item.get(semantic_type) or "")
    if not semantic_id:
        return
    semantic_name = item.get("semantic_name") or item.get("name")
    index.require_semantic_reference(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
        semantic_name=str(semantic_name) if semantic_name is not None else None,
        owner=str(item["owner"]) if item.get("owner") is not None else None,
        field_name=field_name,
    )


def _owner_property(item: Mapping[str, Any]) -> tuple[str, str]:
    nested = item.get("property")
    if isinstance(nested, Mapping):
        owner = nested.get("owner")
        property_name = nested.get("name") or nested.get("property_name")
        if owner is not None and property_name is not None:
            return str(owner), str(property_name)

    owner = item.get("owner")
    property_name = item.get("property") or item.get("property_name") or item.get("name")
    semantic_id = item.get("semantic_id")
    if (owner is None or property_name is None) and isinstance(semantic_id, str) and "." in semantic_id:
        owner, property_name = semantic_id.split(".", 1)
    if owner is None or property_name is None:
        raise CandidateBoundaryError(f"property reference is missing owner/name: {item!r}")
    return str(owner), str(property_name)


def _looks_like_property_reference(item: Mapping[str, Any]) -> bool:
    return any(key in item for key in ("owner", "property", "property_name")) or (
        isinstance(item.get("semantic_id"), str) and "." in item["semantic_id"]
    )


def _coerce_candidates(
    candidates: CandidateRetrievalResult | Sequence[SemanticCandidate] | Mapping[str, Any],
) -> list[SemanticCandidate]:
    if isinstance(candidates, CandidateRetrievalResult):
        return list(candidates.candidates)
    if isinstance(candidates, Mapping):
        return [
            candidate if isinstance(candidate, SemanticCandidate) else SemanticCandidate.model_validate(candidate)
            for candidate in candidates.get("candidates", [])
        ]
    return [
        candidate if isinstance(candidate, SemanticCandidate) else SemanticCandidate.model_validate(candidate)
        for candidate in candidates
    ]


def _provider_name(llm_client: GroundedLLMClient) -> str:
    return str(getattr(llm_client, "provider", "unknown"))


def _attempt_error(attempt: int, exc: Exception) -> GroundedUnderstandingAttemptError:
    return GroundedUnderstandingAttemptError(
        attempt=attempt,
        error_type=exc.__class__.__name__,
        message=str(exc),
    )
