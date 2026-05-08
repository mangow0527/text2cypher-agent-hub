from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

import yaml


@dataclass(frozen=True)
class SlotMatch:
    text: str
    kind: str
    candidate: str
    confidence: float
    source: str
    start: int
    end: int
    entity: str | None = None

    @property
    def property(self) -> str:
        return self.candidate


@dataclass(frozen=True)
class FilterSlot:
    text: str
    entity: str
    property: str
    operator: str
    value: str
    confidence: float
    source: str
    start: int
    end: int


@dataclass(frozen=True)
class MetricSlot:
    text: str
    metric: str
    entity: str
    aggregation: str
    confidence: float
    source: str
    start: int
    end: int
    property: str | None = None


@dataclass(frozen=True)
class OrderSlot:
    text: str
    entity: str
    property: str
    direction: str
    confidence: float
    source: str
    start: int
    end: int


@dataclass(frozen=True)
class LimitSlot:
    text: str
    value: int
    confidence: float
    source: str
    start: int
    end: int


@dataclass(frozen=True)
class SlotMatchResult:
    entities: list[SlotMatch]
    relationships: list[SlotMatch]
    properties: list[SlotMatch]
    return_fields: list[SlotMatch]
    filters: list[FilterSlot]
    metrics: list[MetricSlot]
    group_by: list[SlotMatch]
    order_by: list[OrderSlot]
    limit: LimitSlot | None


class SlotMatcher:
    def __init__(self, dictionary: dict[str, Any]) -> None:
        self._dictionary = dictionary
        self._entity_properties = {
            str(entity): tuple(str(prop) for prop in properties)
            for entity, properties in dictionary.get("entity_properties", {}).items()
        }
        self._default_filter_entity = {
            str(prop): str(entity)
            for prop, entity in dictionary.get("default_filter_entity", {}).items()
        }

    @classmethod
    def from_default_config(cls) -> "SlotMatcher":
        return cls(_load_default_dictionary())

    @classmethod
    def from_config_path(cls, path: str | Path) -> "SlotMatcher":
        with Path(path).open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        return cls(data)

    def match(self, question: str) -> SlotMatchResult:
        entities = self._match_simple(question, "entities", "entity", 0.95)
        relationships = self._match_simple(question, "relationships", "relationship", 0.91)
        properties = self._match_simple(question, "properties", "property", 0.92)
        filters = self._match_filters(question, entities)
        metrics = self._match_metrics(question, entities, properties)
        group_by = self._match_group_by(question, entities, properties)
        order_by = self._match_order_by(question, entities, properties)
        limit = self._match_limit(question)
        return_fields = self._return_fields(properties, filters, metrics, group_by, order_by)

        return SlotMatchResult(
            entities=entities,
            relationships=relationships,
            properties=properties,
            return_fields=return_fields,
            filters=filters,
            metrics=metrics,
            group_by=group_by,
            order_by=order_by,
            limit=limit,
        )

    def _match_simple(
        self,
        question: str,
        section: str,
        kind: str,
        confidence: float,
    ) -> list[SlotMatch]:
        matches: list[SlotMatch] = []
        for candidate, config in self._dictionary.get(section, {}).items():
            for synonym in _synonyms(config):
                for occurrence in _find_occurrences(question, synonym):
                    matches.append(
                        SlotMatch(
                            text=occurrence.text,
                            kind=kind,
                            candidate=str(candidate),
                            confidence=confidence,
                            source="dictionary",
                            start=occurrence.start,
                            end=occurrence.end,
                            entity=None,
                        )
                    )
        return _deduplicate(matches)

    def _match_filters(self, question: str, entities: list[SlotMatch]) -> list[FilterSlot]:
        filters: list[FilterSlot] = []
        for property_name, value_map in self._dictionary.get("values", {}).items():
            property_matches = [slot for slot in self._match_simple(question, "properties", "property", 0.92) if slot.candidate == property_name]
            for canonical_value, synonyms in value_map.items():
                for synonym in synonyms:
                    for value_occurrence in _find_occurrences(question, str(synonym)):
                        if not property_matches and property_name not in self._default_filter_entity:
                            continue
                        entity = self._filter_entity(question, property_name, entities, value_occurrence.start)
                        if not entity:
                            continue
                        text, start, end = self._filter_span(question, property_matches, value_occurrence, entity)
                        filters.append(
                            FilterSlot(
                                text=text,
                                entity=entity,
                                property=str(property_name),
                                operator="=",
                                value=str(canonical_value),
                                confidence=0.93,
                                source="dictionary",
                                start=start,
                                end=end,
                            )
                        )

        filters.extend(self._match_numeric_filters(question, entities))
        return _deduplicate(filters)

    def _filter_entity(
        self,
        question: str,
        property_name: str,
        entities: list[SlotMatch],
        value_start: int,
    ) -> str | None:
        default_entity = self._default_filter_entity.get(property_name)
        nearby_entities = [entity for entity in entities if 0 <= entity.start - value_start <= 12 or 0 <= value_start - entity.end <= 12]
        if default_entity and any(entity.candidate == default_entity for entity in nearby_entities):
            return default_entity
        if nearby_entities:
            return nearby_entities[0].candidate
        if default_entity and default_entity in self._entity_properties and property_name in self._entity_properties[default_entity]:
            return default_entity
        owners = [entity.candidate for entity in entities if property_name in self._entity_properties.get(entity.candidate, ())]
        return owners[0] if owners else None

    def _filter_span(
        self,
        question: str,
        property_matches: list[SlotMatch],
        value_occurrence: "_Occurrence",
        entity: str,
    ) -> tuple[str, int, int]:
        entity_matches = self._match_simple(question, "entities", "entity", 0.95)
        nearby_property = _nearest_before_or_after(property_matches, value_occurrence.start, max_distance=6)
        nearby_entity = None
        if not nearby_property:
            nearby_entity = next((slot for slot in entity_matches if slot.candidate == entity and abs(slot.start - value_occurrence.end) <= 8), None)
        start = value_occurrence.start
        end = value_occurrence.end
        if nearby_property:
            start = min(start, nearby_property.start)
            end = max(end, nearby_property.end)
        if nearby_entity:
            start = min(start, nearby_entity.start)
            end = max(end, nearby_entity.end)
        text = question[start:end].strip(" 的")
        return text, start, end

    def _match_numeric_filters(self, question: str, entities: list[SlotMatch]) -> list[FilterSlot]:
        filters: list[FilterSlot] = []
        operator_words = {"大于": ">", "超过": ">", "高于": ">", "小于": "<", "低于": "<", "不少于": ">=", "不低于": ">=", "至少": ">=", "不超过": "<=", "至多": "<="}
        properties = self._match_simple(question, "properties", "property", 0.92)
        for prop in properties:
            for word, operator in operator_words.items():
                pattern = rf"{re.escape(prop.text)}\s*{word}\s*([0-9]+(?:\.[0-9]+)?)"
                match = re.search(pattern, question, flags=re.IGNORECASE)
                if not match:
                    continue
                entity = self._owner_for_property(prop.candidate, entities)
                if not entity:
                    continue
                filters.append(
                    FilterSlot(
                        text=match.group(0),
                        entity=entity,
                        property=prop.candidate,
                        operator=operator,
                        value=match.group(1),
                        confidence=0.9,
                        source="dictionary",
                        start=match.start(),
                        end=match.end(),
                    )
                )
        return filters

    def _match_metrics(
        self,
        question: str,
        entities: list[SlotMatch],
        properties: list[SlotMatch],
    ) -> list[MetricSlot]:
        metrics: list[MetricSlot] = []
        metric_config = self._dictionary.get("metric_templates", {})
        for aggregation, config in metric_config.items():
            for synonym in _synonyms(config):
                for occurrence in _find_occurrences(question, synonym):
                    if aggregation == "count":
                        entity = self._nearest_entity(entities, occurrence.start) or (entities[0].candidate if entities else "")
                        if not entity:
                            continue
                        text = self._metric_text(question, occurrence, None)
                        metrics.append(
                            MetricSlot(text=text, metric=f"count_{entity}", entity=entity, property=None, aggregation="count", confidence=0.92, source="dictionary", start=occurrence.start, end=occurrence.end)
                        )
                        continue

                    prop = _nearest_before_or_after(properties, occurrence.end, max_distance=8)
                    if not prop:
                        continue
                    entity = self._owner_for_property(prop.candidate, entities)
                    if not entity:
                        continue
                    text = question[min(occurrence.start, prop.start) : max(occurrence.end, prop.end)]
                    metrics.append(
                        MetricSlot(
                            text=text,
                            metric=f"{aggregation}_{entity}_{prop.candidate}",
                            entity=entity,
                            property=prop.candidate,
                            aggregation=str(aggregation),
                            confidence=0.92,
                            source="dictionary",
                            start=min(occurrence.start, prop.start),
                            end=max(occurrence.end, prop.end),
                        )
                    )
        return _deduplicate(metrics)

    def _match_group_by(
        self,
        question: str,
        entities: list[SlotMatch],
        properties: list[SlotMatch],
    ) -> list[SlotMatch]:
        group_slots: list[SlotMatch] = []
        prefixes = [str(prefix) for prefix in self._dictionary.get("group_by", {}).get("prefixes", [])]
        for prop in properties:
            prefix = next((value for value in prefixes if question[max(0, prop.start - len(value)) : prop.start] == value), None)
            if not prefix:
                continue
            entity = self._owner_for_property(prop.candidate, entities)
            if not entity:
                continue
            group_slots.append(
                SlotMatch(
                    text=f"{prefix}{prop.text}",
                    kind="group_by",
                    candidate=prop.candidate,
                    confidence=0.91,
                    source="dictionary",
                    start=prop.start - len(prefix),
                    end=prop.end,
                    entity=entity,
                )
            )
        return _deduplicate(group_slots)

    def _match_order_by(
        self,
        question: str,
        entities: list[SlotMatch],
        properties: list[SlotMatch],
    ) -> list[OrderSlot]:
        order_slots: list[OrderSlot] = []
        for direction, words in self._dictionary.get("order", {}).items():
            for prop in properties:
                for word in words:
                    text = f"{prop.text}{word}"
                    index = question.find(text)
                    if index == -1:
                        continue
                    entity = self._owner_for_property(prop.candidate, entities)
                    if not entity:
                        continue
                    order_slots.append(
                        OrderSlot(text=text, entity=entity, property=prop.candidate, direction=str(direction), confidence=0.91, source="dictionary", start=index, end=index + len(text))
                    )
        return _deduplicate(order_slots)

    def _match_limit(self, question: str) -> LimitSlot | None:
        for pattern in self._dictionary.get("limit", {}).get("patterns", []):
            match = re.search(str(pattern), question, flags=re.IGNORECASE)
            if match:
                return LimitSlot(
                    text=match.group(0),
                    value=int(match.group(1)),
                    confidence=0.94,
                    source="dictionary",
                    start=match.start(),
                    end=match.end(),
                )
        return None

    def _return_fields(
        self,
        properties: list[SlotMatch],
        filters: list[FilterSlot],
        metrics: list[MetricSlot],
        group_by: list[SlotMatch],
        order_by: list[OrderSlot],
    ) -> list[SlotMatch]:
        used_as_control = {slot.property for slot in filters}
        used_as_control.update(slot.property for slot in metrics if slot.property)
        used_as_control.update(slot.candidate for slot in group_by)
        used_as_control.update(slot.property for slot in order_by)
        return [slot for slot in properties if slot.candidate not in used_as_control]

    def _nearest_entity(self, entities: list[SlotMatch], index: int) -> str | None:
        if not entities:
            return None
        return min(entities, key=lambda entity: abs(entity.start - index)).candidate

    def _owner_for_property(self, property_name: str, entities: list[SlotMatch]) -> str | None:
        owners = [entity.candidate for entity in entities if property_name in self._entity_properties.get(entity.candidate, ())]
        if len(owners) == 1:
            return owners[0]
        if owners:
            return owners[-1]
        return None

    def _metric_text(self, question: str, occurrence: "_Occurrence", prop: SlotMatch | None) -> str:
        if prop is None:
            return question[occurrence.start : occurrence.end]
        return question[min(occurrence.start, prop.start) : max(occurrence.end, prop.end)]


@dataclass(frozen=True)
class _Occurrence:
    text: str
    start: int
    end: int


@lru_cache(maxsize=1)
def _load_default_dictionary() -> dict[str, Any]:
    config_path = Path(__file__).resolve().parents[1] / "config" / "slot_dictionary.yaml"
    with config_path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _synonyms(config: object) -> tuple[str, ...]:
    if isinstance(config, dict):
        values = config.get("synonyms", ())
    else:
        values = config
    return tuple(str(value) for value in values or ())


def _find_occurrences(question: str, synonym: str) -> list[_Occurrence]:
    if not synonym:
        return []
    return [_Occurrence(match.group(0), match.start(), match.end()) for match in re.finditer(re.escape(synonym), question, flags=re.IGNORECASE)]


def _deduplicate(slots: list[Any]) -> list[Any]:
    selected: dict[tuple[str, int, int], Any] = {}
    for slot in sorted(slots, key=lambda item: (item.start, -(item.end - item.start), getattr(item, "candidate", ""))):
        key = (getattr(slot, "kind", slot.__class__.__name__), slot.start, slot.end)
        current = selected.get(key)
        if current is None or len(slot.text) > len(current.text):
            selected[key] = slot
    return sorted(selected.values(), key=lambda item: (item.start, item.end, getattr(item, "candidate", "")))


def _nearest_before_or_after(slots: list[SlotMatch], index: int, max_distance: int) -> SlotMatch | None:
    candidates = [slot for slot in slots if abs(slot.end - index) <= max_distance or abs(slot.start - index) <= max_distance]
    if not candidates:
        return None
    return min(candidates, key=lambda slot: min(abs(slot.end - index), abs(slot.start - index)))
