from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from services.cypher_generator_agent.app.semantic_model import (
    GraphSemanticRegistry,
    PropertyDefinition,
    RegistryLookupError,
)

from .models import (
    LiteralAlternative,
    LiteralEvidence,
    LiteralResolverRequest,
    LiteralResolverResult,
)
from .typed_parser import parse_typed_literal
from .value_index import StaticValueIndex, normalize_literal_text


_ID_SHAPE_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$", re.IGNORECASE)
_FUZZY_THRESHOLD = 0.50
_AUTO_FUZZY_THRESHOLD = 0.95
_AUTO_FUZZY_GAP = 0.10


class LiteralResolver:
    def __init__(
        self,
        registry: GraphSemanticRegistry,
        value_index: StaticValueIndex | None = None,
        *,
        embedding_enabled: bool = False,
    ) -> None:
        if embedding_enabled:
            raise ValueError("LiteralResolver v1 keeps embedding lookup disabled")
        self.registry = registry
        self.value_index = value_index or StaticValueIndex.empty()

    def resolve(self, request: LiteralResolverRequest) -> LiteralResolverResult:
        owner = request.owner
        try:
            prop = self.registry.get_property(owner, request.expected_property)
        except RegistryLookupError:
            return self._unresolved_result(
                request,
                error_code="literal_property_mismatch",
                evidence=[
                    LiteralEvidence(
                        source="semantic_registry",
                        matched=f"{owner}.{request.expected_property}",
                        target=None,
                    )
                ],
            )
        raw_literal = request.raw_literal.strip()

        if self._must_use_value_index_exact(request, prop):
            index_result = self._resolve_value_index_exact(
                request,
                owner,
                prop.name,
                value_index_miss_is_error=True,
            )
            if index_result.resolved:
                return index_result
            passthrough = self._resolve_literal_passthrough(
                request,
                prop,
                value_index_miss=index_result.value_index_miss,
            )
            if passthrough is not None:
                return passthrough
            return index_result

        exact_result = self._resolve_exact_valid_value(request, prop)
        if exact_result is not None:
            return exact_result

        synonym_result = self._resolve_value_synonym(request, prop)
        if synonym_result is not None:
            return synonym_result

        contained_result = self._resolve_contained_valid_value(request, prop)
        if contained_result is not None:
            return contained_result

        parsed = parse_typed_literal(
            raw_literal,
            prop.type,
            request.literal_kind_hint,
            prop.description,
        )
        if parsed is not None:
            return self._resolved_result(
                request,
                resolved_value=parsed.resolved_value,
                normalized_value=parsed.normalized_value,
                match_type="typed_parse",
                confidence=parsed.confidence,
                evidence=[
                    LiteralEvidence(
                        source=parsed.source,
                        matched=raw_literal,
                        target=parsed.target,
                    )
                ],
            )

        alternatives = self._fuzzy_alternatives(request, prop)
        if alternatives:
            if not self._is_high_risk_enum(prop) and self._can_auto_resolve_fuzzy(alternatives):
                best = alternatives[0]
                return self._resolved_result(
                    request,
                    resolved_value=best.value,
                    normalized_value=best.value,
                    match_type="fuzzy_text",
                    confidence=best.confidence,
                    evidence=[
                        LiteralEvidence(
                            source=best.source,
                            matched=raw_literal,
                            target=best.value,
                        )
                    ],
                    alternatives=[],
                )
            return self._unresolved_result(
                request,
                alternatives=alternatives,
                requires_user_choice=True,
                value_index_miss=self._value_index_miss(owner, prop.name, request.raw_literal),
                error_code="literal_ambiguous",
            )

        index_result = self._resolve_value_index_exact(
            request,
            owner,
            prop.name,
            value_index_miss_is_error=self.value_index.has_property(owner, prop.name),
        )
        if index_result.resolved:
            return index_result

        passthrough = self._resolve_literal_passthrough(
            request,
            prop,
            value_index_miss=index_result.value_index_miss,
        )
        if passthrough is not None:
            return passthrough
        return index_result

    def _resolve_exact_valid_value(
        self,
        request: LiteralResolverRequest,
        prop: PropertyDefinition,
    ) -> LiteralResolverResult | None:
        normalized_raw = normalize_literal_text(request.raw_literal)
        for value in prop.valid_values:
            if normalize_literal_text(value) == normalized_raw:
                return self._resolved_result(
                    request,
                    resolved_value=value,
                    normalized_value=value,
                    match_type="exact",
                    confidence=1.0,
                    evidence=[
                        LiteralEvidence(
                            source="property.valid_values",
                            matched=request.raw_literal.strip(),
                            target=value,
                        )
                    ],
                )
        return None

    def _resolve_contained_valid_value(
        self,
        request: LiteralResolverRequest,
        prop: PropertyDefinition,
    ) -> LiteralResolverResult | None:
        matches: list[tuple[Any, str, str]] = []
        for value in prop.valid_values:
            for display in [value, *prop.value_synonyms.get(value, [])]:
                if _literal_contains_display(request.raw_literal, display):
                    source = "property.valid_values" if display == value else "property.value_synonyms"
                    matches.append((value, str(display), source))
                    break

        unique_values = {value for value, _, _ in matches}
        if len(unique_values) != 1:
            return None

        value, display, source = matches[0]
        return self._resolved_result(
            request,
            resolved_value=value,
            normalized_value=value,
            match_type="exact" if source == "property.valid_values" else "value_synonym",
            confidence=0.99,
            evidence=[
                LiteralEvidence(
                    source=source,
                    matched=request.raw_literal.strip(),
                    target=display,
                )
            ],
        )

    def _resolve_value_synonym(
        self,
        request: LiteralResolverRequest,
        prop: PropertyDefinition,
    ) -> LiteralResolverResult | None:
        normalized_raw = normalize_literal_text(request.raw_literal)
        for target, synonyms in prop.value_synonyms.items():
            for synonym in synonyms:
                if normalize_literal_text(synonym) == normalized_raw:
                    return self._resolved_result(
                        request,
                        resolved_value=target,
                        normalized_value=target,
                        match_type="value_synonym",
                        confidence=0.98,
                        evidence=[
                            LiteralEvidence(
                                source="property.value_synonyms",
                                matched=request.raw_literal.strip(),
                                target=target,
                            )
                        ],
                    )
        return None

    def _resolve_value_index_exact(
        self,
        request: LiteralResolverRequest,
        owner: str,
        property_name: str,
        *,
        value_index_miss_is_error: bool,
    ) -> LiteralResolverResult:
        raw_literal = request.raw_literal.strip()
        entry = self.value_index.lookup_exact(owner, property_name, raw_literal)
        if entry is not None:
            return self._resolved_result(
                request,
                resolved_value=entry.value,
                normalized_value=entry.value,
                match_type="value_index_exact",
                confidence=1.0,
                evidence=[
                    LiteralEvidence(
                        source="static_value_index",
                        matched=raw_literal,
                        target=entry.value,
                    )
                ],
            )
        return self._unresolved_result(
            request,
            value_index_miss=value_index_miss_is_error,
            error_code="literal_value_index_miss" if value_index_miss_is_error else "literal_unresolved",
        )

    def _fuzzy_alternatives(
        self,
        request: LiteralResolverRequest,
        prop: PropertyDefinition,
    ) -> list[LiteralAlternative]:
        raw_literal = request.raw_literal.strip()
        candidates = self._property_candidates(request.owner, prop)
        best_by_value: dict[Any, LiteralAlternative] = {}

        for value, display_values, source in candidates:
            best_display = str(value)
            best_score = 0.0
            for display in display_values:
                score = _fuzzy_score(raw_literal, display)
                if score > best_score:
                    best_score = score
                    best_display = display
            if best_score < _FUZZY_THRESHOLD:
                continue
            alternative = LiteralAlternative(
                value=value,
                display=best_display,
                confidence=round(best_score, 4),
                source=source,
                why="closest local literal candidate",
            )
            current = best_by_value.get(value)
            if current is None or alternative.confidence > current.confidence:
                best_by_value[value] = alternative

        alternatives = sorted(
            best_by_value.values(),
            key=lambda alternative: (-alternative.confidence, str(alternative.value)),
        )
        return alternatives[:3]

    def _property_candidates(
        self,
        owner: str,
        prop: PropertyDefinition,
    ) -> list[tuple[Any, list[str], str]]:
        if prop.valid_values:
            return [
                (
                    value,
                    [value, *prop.value_synonyms.get(value, [])],
                    "property.valid_values",
                )
                for value in prop.valid_values
            ]

        if prop.type.strip().lower() == "string":
            return [
                (entry.value, [entry.value], "static_value_index")
                for entry in self.value_index.iter_values(owner, prop.name)
            ]

        return []

    def _must_use_value_index_exact(
        self,
        request: LiteralResolverRequest,
        prop: PropertyDefinition,
    ) -> bool:
        return self._is_owner_id_property(request, prop)

    def _resolve_literal_passthrough(
        self,
        request: LiteralResolverRequest,
        prop: PropertyDefinition,
        *,
        value_index_miss: bool,
    ) -> LiteralResolverResult | None:
        if not self._can_passthrough_raw_literal(prop):
            return None
        raw_literal = request.raw_literal.strip()
        if not raw_literal:
            return None
        return self._resolved_result(
            request,
            resolved_value=raw_literal,
            normalized_value=raw_literal,
            match_type="literal_passthrough",
            confidence=0.9,
            evidence=[
                LiteralEvidence(
                    source="literal_passthrough",
                    matched=raw_literal,
                    target=raw_literal,
                )
            ],
            value_index_miss=value_index_miss,
        )

    def _can_passthrough_raw_literal(self, prop: PropertyDefinition) -> bool:
        if prop.valid_values:
            return False
        return prop.type.strip().lower() in {"string", "str", "text"}

    def _value_index_miss(self, owner: str, property_name: str, raw_literal: str) -> bool:
        if not self.value_index.has_property(owner, property_name):
            return False
        return self.value_index.lookup_exact(owner, property_name, raw_literal) is None

    def _is_owner_id_property(
        self,
        request: LiteralResolverRequest,
        prop: PropertyDefinition,
    ) -> bool:
        if request.expected_vertex is None:
            return prop.name == "id"
        return self.registry.get_vertex(request.expected_vertex).id_property == prop.name

    def _is_high_risk_enum(self, prop: PropertyDefinition) -> bool:
        return bool(prop.valid_values)

    def _can_auto_resolve_fuzzy(self, alternatives: list[LiteralAlternative]) -> bool:
        if not alternatives or alternatives[0].confidence < _AUTO_FUZZY_THRESHOLD:
            return False
        if len(alternatives) == 1:
            return True
        return alternatives[0].confidence - alternatives[1].confidence >= _AUTO_FUZZY_GAP

    def _resolved_result(
        self,
        request: LiteralResolverRequest,
        *,
        resolved_value: Any,
        normalized_value: Any,
        match_type: str,
        confidence: float,
        evidence: list[LiteralEvidence],
        alternatives: list[LiteralAlternative] | None = None,
        value_index_miss: bool = False,
    ) -> LiteralResolverResult:
        return LiteralResolverResult(
            raw_literal=request.raw_literal,
            resolved=True,
            resolved_value=resolved_value,
            normalized_value=normalized_value,
            match_type=match_type,
            confidence=confidence,
            expected_vertex=request.expected_vertex,
            expected_edge=request.expected_edge,
            expected_property=request.expected_property,
            evidence=evidence,
            alternatives=alternatives or [],
            requires_user_choice=False,
            value_index_miss=value_index_miss,
            error_code=None,
        )

    def _unresolved_result(
        self,
        request: LiteralResolverRequest,
        *,
        alternatives: list[LiteralAlternative] | None = None,
        requires_user_choice: bool = False,
        value_index_miss: bool = False,
        error_code: str | None = None,
        evidence: list[LiteralEvidence] | None = None,
    ) -> LiteralResolverResult:
        return LiteralResolverResult(
            raw_literal=request.raw_literal,
            resolved=False,
            resolved_value=None,
            normalized_value=None,
            match_type="unresolved",
            confidence=0.0,
            expected_vertex=request.expected_vertex,
            expected_edge=request.expected_edge,
            expected_property=request.expected_property,
            evidence=evidence or [],
            alternatives=alternatives or [],
            requires_user_choice=requires_user_choice,
            value_index_miss=value_index_miss,
            error_code=error_code,
        )


def _looks_like_id(raw_literal: str) -> bool:
    return _ID_SHAPE_RE.match(raw_literal.strip()) is not None


def _fuzzy_score(raw_literal: str, candidate: str) -> float:
    normalized_raw = normalize_literal_text(raw_literal)
    normalized_candidate = normalize_literal_text(candidate)
    if not normalized_raw or not normalized_candidate:
        return 0.0

    score = SequenceMatcher(None, normalized_raw, normalized_candidate).ratio()
    if normalized_candidate in normalized_raw or normalized_raw in normalized_candidate:
        score = max(score, 0.85)
    return min(score, 1.0)


def _literal_contains_display(raw_literal: str, display: Any) -> bool:
    normalized_raw = normalize_literal_text(raw_literal)
    normalized_display = normalize_literal_text(display)
    if not normalized_raw or not normalized_display:
        return False
    if not any(char.isascii() and char.isalnum() for char in normalized_display):
        return False
    if normalized_raw == normalized_display:
        return False
    start = normalized_raw.find(normalized_display)
    while start != -1:
        end = start + len(normalized_display)
        before = normalized_raw[start - 1] if start > 0 else ""
        after = normalized_raw[end] if end < len(normalized_raw) else ""
        if not _is_ascii_word_char(before) and not _is_ascii_word_char(after):
            return True
        start = normalized_raw.find(normalized_display, start + 1)
    return False


def _is_ascii_word_char(value: str) -> bool:
    return bool(value) and value.isascii() and (value.isalnum() or value == "_")
