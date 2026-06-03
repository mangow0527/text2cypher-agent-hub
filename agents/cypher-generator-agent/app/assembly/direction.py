from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Any

from services.cypher_generator_agent.app.semantic_model.model import EdgeDefinition
from services.cypher_generator_agent.app.semantic_model.registry import GraphSemanticRegistry


class DirectionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class DirectionResolution:
    status: DirectionStatus
    edge_names: list[str] = field(default_factory=list)
    matched_terms: dict[str, list[str]] = field(default_factory=dict)
    reason: str | None = None


def derive_direction_mapping(registry: GraphSemanticRegistry) -> "DirectionMapper":
    return DirectionMapper(registry)


class DirectionMapper:
    def __init__(self, registry: GraphSemanticRegistry) -> None:
        self.registry = registry
        self.term_to_edges = self._build_index(registry)

    def resolve_direction_terms(self, question_or_terms: str | Iterable[str]) -> DirectionResolution:
        terms = [question_or_terms] if isinstance(question_or_terms, str) else [str(term) for term in question_or_terms]
        normalized_inputs = [_normalize(term) for term in terms]
        matched: dict[str, set[str]] = {}
        for normalized in normalized_inputs:
            if not normalized:
                continue
            for indexed_term, edge_names in self.term_to_edges.items():
                if indexed_term and indexed_term in normalized:
                    matched.setdefault(indexed_term, set()).update(edge_names)
        matched = self._narrow_generic_terms_with_domain_context(matched, normalized_inputs)
        matched = _keep_longest_matches(matched)

        edge_names = sorted({edge for edges in matched.values() for edge in edges})
        if not edge_names:
            return DirectionResolution(status=DirectionStatus.UNRESOLVED)
        if len(edge_names) == 1:
            return DirectionResolution(
                status=DirectionStatus.RESOLVED,
                edge_names=edge_names,
                matched_terms=_freeze_matches(matched),
            )
        return DirectionResolution(
            status=DirectionStatus.AMBIGUOUS,
            edge_names=edge_names,
            matched_terms=_freeze_matches(matched),
            reason="conflicting_direction_terms",
        )

    @staticmethod
    def _build_index(registry: GraphSemanticRegistry) -> dict[str, frozenset[str]]:
        mutable: dict[str, set[str]] = defaultdict(set)
        for edge in registry.model.edges:
            for term in _edge_terms(edge):
                normalized = _normalize(term)
                if normalized:
                    mutable[normalized].add(edge.name)
        return {term: frozenset(edge_names) for term, edge_names in mutable.items()}

    def _narrow_generic_terms_with_domain_context(
        self,
        matched: dict[str, set[str]],
        normalized_inputs: list[str],
    ) -> dict[str, set[str]]:
        domains = {domain for text in normalized_inputs for domain in _DOMAIN_ALIASES.values() if domain in text}
        if len(domains) != 1:
            return matched
        domain = next(iter(domains))
        narrowed: dict[str, set[str]] = {}
        for term, edge_names in matched.items():
            domain_edges = self.term_to_edges.get(f"{domain}{term}")
            if term in _GENERIC_DIRECTION_TERMS and domain_edges:
                narrowed[term] = set(edge_names) & set(domain_edges)
                continue
            narrowed[term] = edge_names
        return narrowed


def _edge_terms(edge: EdgeDefinition) -> set[str]:
    terms: set[str] = {edge.name}
    terms.update(_split_identifier(edge.name))
    terms.update(_text_terms(edge.direction_semantics))
    terms.update(_text_terms(edge.description))
    terms.update(_ai_context_terms(edge.ai_context))
    directional_terms = {edge.name}
    directional_terms.update(_split_identifier(edge.name))
    directional_terms.update(_text_terms(edge.direction_semantics))
    directional_terms.update(_ai_context_terms(edge.ai_context))
    terms.update(_direction_expansions(edge, directional_terms))
    return terms


def _ai_context_terms(ai_context: dict[str, Any]) -> set[str]:
    terms: set[str] = set()
    for value in ai_context.values():
        if isinstance(value, str):
            terms.update(_text_terms(value))
        elif isinstance(value, list | tuple | set):
            for item in value:
                if isinstance(item, str):
                    terms.update(_text_terms(item))
    return terms


def _text_terms(text: str | None) -> set[str]:
    if not text:
        return set()
    parts = {text}
    parts.update(re.findall(r"[\u4e00-\u9fffA-Za-z0-9_\-]+", text))
    parts.update(_compact_chinese_direction_phrases(text))
    return parts


def _compact_chinese_direction_phrases(text: str) -> set[str]:
    phrases: set[str] = set()
    for match in re.finditer(r"([\u4e00-\u9fff]{0,8}(?:源端|宿端|目的|目的端|终点|起点|经过|穿过|途经)[\u4e00-\u9fff]{0,4})", text):
        phrase = match.group(1)
        phrases.add(phrase)
        phrases.add(phrase.removesuffix("设备").removesuffix("端"))
    return phrases


def _split_identifier(identifier: str) -> set[str]:
    return {part for part in re.split(r"[_\W]+", identifier) if part}


def _direction_expansions(edge: EdgeDefinition, current_terms: set[str]) -> set[str]:
    domains = _domain_terms(edge)
    categories = _direction_categories(edge, current_terms)
    expansions: set[str] = set()
    for domain in domains:
        if "source" in categories:
            expansions.update({f"{domain}源", f"{domain}源端", f"{domain}起", f"{domain}起点"})
        if "destination" in categories:
            expansions.update({f"{domain}目的", f"{domain}目的端", f"{domain}宿", f"{domain}宿端", f"{domain}到达", f"{domain}终", f"{domain}终点"})
        if "through" in categories:
            expansions.update({f"{domain}经过", f"{domain}穿过", f"{domain}途经"})
    if "source" in categories:
        expansions.update({"源", "源端", "起", "起点"})
    if "destination" in categories:
        expansions.update({"目的", "目的端", "宿", "宿端", "到达", "终", "终点"})
    if "through" in categories:
        expansions.update({"经过", "穿过", "途经"})
    return expansions


def _domain_terms(edge: EdgeDefinition) -> set[str]:
    domains = {_normalize(edge.from_vertex), *_split_identifier(edge.name)}
    return {_DOMAIN_ALIASES[domain] for domain in domains if domain in _DOMAIN_ALIASES}


def _direction_categories(edge: EdgeDefinition, terms: set[str]) -> set[str]:
    normalized = {_normalize(term) for term in terms}
    joined = " ".join(normalized)
    categories: set[str] = set()
    if {"src", "source"} & normalized or "源端" in joined or "起点" in joined:
        categories.add("source")
    if {"dst", "destination"} & normalized or "宿端" in joined or "目的" in joined or "终点" in joined:
        categories.add("destination")
    if "through" in normalized or "经过" in joined or "穿过" in joined or "途经" in joined or "traverses" in normalized:
        categories.add("through")
    return categories


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())


def _freeze_matches(matched: dict[str, set[str]]) -> dict[str, list[str]]:
    return {term: sorted(edge_names) for term, edge_names in sorted(matched.items())}


def _keep_longest_matches(matched: dict[str, set[str]]) -> dict[str, set[str]]:
    terms = sorted(matched, key=len, reverse=True)
    kept: dict[str, set[str]] = {}
    for term in terms:
        if any(term != longer and term in longer for longer in kept):
            continue
        kept[term] = matched[term]
    return kept


_DOMAIN_ALIASES = {
    "tunnel": "隧道",
    "fiber": "光纤",
    "link": "链路",
    "path": "路径",
}

_GENERIC_DIRECTION_TERMS = {
    "源",
    "源端",
    "起",
    "起点",
    "目的",
    "目的端",
    "宿",
    "宿端",
    "到达",
    "终",
    "终点",
    "经过",
    "穿过",
    "途经",
}
