from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .intent_recognition import IntentRecognitionResult


class BusinessSlotSchemaConfigError(ValueError):
    pass


@dataclass(frozen=True)
class BusinessSlotRequiredWhen:
    slot: str
    values: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "BusinessSlotRequiredWhen":
        slot = _required_str(value, "slot")
        values = value.get("values")
        if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
            raise BusinessSlotSchemaConfigError("required_when.values must be a non-empty list of strings")
        return cls(slot=slot, values=tuple(values))

    def matches(self, frame: "BusinessSlotFrame") -> bool:
        actual_values = set(frame.values_for(self.slot))
        return any(value in actual_values for value in self.values)

    def to_dict(self) -> dict[str, object]:
        return {
            "slot": self.slot,
            "values": list(self.values),
        }


@dataclass(frozen=True)
class BusinessSlotDefinition:
    name: str
    description: str
    required: bool
    required_when: tuple[BusinessSlotRequiredWhen, ...]
    min_count: int
    depend_slots: tuple[str, ...]
    priority: int
    follow_up_question: str

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "BusinessSlotDefinition":
        min_count = int(value.get("min_count", 1))
        if min_count < 1:
            raise BusinessSlotSchemaConfigError("business slot min_count must be >= 1")
        depend_slots = value.get("depend_slots", [])
        if not isinstance(depend_slots, list) or not all(isinstance(item, str) for item in depend_slots):
            raise BusinessSlotSchemaConfigError("depend_slots must be a list of strings")
        required_when = value.get("required_when", [])
        if not isinstance(required_when, list):
            raise BusinessSlotSchemaConfigError("required_when must be a list")
        return cls(
            name=_required_str(value, "name"),
            description=_required_str(value, "description"),
            required=bool(value.get("required", False)),
            required_when=tuple(BusinessSlotRequiredWhen.from_mapping(item) for item in required_when if _is_mapping(item, "required_when")),
            min_count=min_count,
            depend_slots=tuple(depend_slots),
            priority=int(value.get("priority", 0)),
            follow_up_question=_required_str(value, "follow_up_question"),
        )

    def is_required_for(self, frame: "BusinessSlotFrame") -> bool:
        return self.required or any(condition.matches(frame) for condition in self.required_when)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "required": self.required,
            "required_when": [condition.to_dict() for condition in self.required_when],
            "min_count": self.min_count,
            "depend_slots": list(self.depend_slots),
            "priority": self.priority,
            "follow_up_question": self.follow_up_question,
        }


@dataclass(frozen=True)
class BusinessSlotSchema:
    schema_id: str
    scenario_id: str
    primary_intent: str
    secondary_intents: tuple[str, ...]
    description: str
    slots: tuple[BusinessSlotDefinition, ...]

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "BusinessSlotSchema":
        secondary_intents = value.get("secondary_intents")
        if not isinstance(secondary_intents, list) or not all(isinstance(item, str) for item in secondary_intents):
            raise BusinessSlotSchemaConfigError("secondary_intents must be a list of strings")
        slots = value.get("slots")
        if not isinstance(slots, list):
            raise BusinessSlotSchemaConfigError("slots must be a list")
        return cls(
            schema_id=_required_str(value, "schema_id"),
            scenario_id=_required_str(value, "scenario_id"),
            primary_intent=_required_str(value, "primary_intent"),
            secondary_intents=tuple(secondary_intents),
            description=_required_str(value, "description"),
            slots=tuple(BusinessSlotDefinition.from_mapping(slot) for slot in slots),
        )

    @property
    def required_slots(self) -> tuple[BusinessSlotDefinition, ...]:
        return tuple(slot for slot in self.slots if slot.required)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_id": self.schema_id,
            "scenario_id": self.scenario_id,
            "primary_intent": self.primary_intent,
            "secondary_intents": list(self.secondary_intents),
            "description": self.description,
            "slots": [slot.to_dict() for slot in self.slots],
        }


@dataclass(frozen=True)
class BusinessSlotValue:
    name: str
    values: tuple[str, ...]
    source: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "values": list(self.values),
            "source": self.source,
        }


@dataclass(frozen=True)
class BusinessSlotFrame:
    schema_id: str
    scenario_id: str
    slots: tuple[BusinessSlotValue, ...]

    def values_for(self, name: str) -> tuple[str, ...]:
        for slot in self.slots:
            if slot.name == name:
                return slot.values
        return ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_id": self.schema_id,
            "scenario_id": self.scenario_id,
            "slots": [slot.to_dict() for slot in self.slots],
        }


@dataclass(frozen=True)
class BusinessSlotCompletenessResult:
    accepted: bool
    schema_id: str | None
    scenario_id: str | None
    missing_slots: tuple[BusinessSlotDefinition, ...]
    clarification_questions: tuple[str, ...]

    @classmethod
    def not_applicable(cls) -> "BusinessSlotCompletenessResult":
        return cls(
            accepted=False,
            schema_id=None,
            scenario_id=None,
            missing_slots=(),
            clarification_questions=(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "schema_id": self.schema_id,
            "scenario_id": self.scenario_id,
            "missing_slots": [slot.to_dict() for slot in self.missing_slots],
            "clarification_questions": list(self.clarification_questions),
        }


class BusinessSlotSchemaRegistry:
    def __init__(self, schemas: list[BusinessSlotSchema]) -> None:
        self._schemas = schemas
        self._by_intent = {
            (schema.primary_intent, secondary_intent): schema
            for schema in schemas
            for secondary_intent in schema.secondary_intents
        }

    @classmethod
    def from_path(cls, path: Path) -> "BusinessSlotSchemaRegistry":
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise BusinessSlotSchemaConfigError(f"{path} must contain a YAML mapping")
        raw_schemas = document.get("schemas")
        if not isinstance(raw_schemas, list):
            raise BusinessSlotSchemaConfigError("schemas must be a list")
        return cls([BusinessSlotSchema.from_mapping(schema) for schema in raw_schemas])

    def select(self, intent: IntentRecognitionResult) -> BusinessSlotSchema:
        key = (intent.primary_intent, intent.secondary_intent)
        schema = self._by_intent.get(key)
        if schema is None:
            raise BusinessSlotSchemaConfigError(f"no business slot schema for intent: {key[0]}.{key[1]}")
        return schema

    def validate(
        self,
        *,
        schema: BusinessSlotSchema,
        frame: BusinessSlotFrame,
    ) -> BusinessSlotCompletenessResult:
        missing = tuple(
            slot
            for slot in schema.slots
            if slot.is_required_for(frame)
            if len(frame.values_for(slot.name)) < slot.min_count
        )
        return BusinessSlotCompletenessResult(
            accepted=not missing,
            schema_id=schema.schema_id,
            scenario_id=schema.scenario_id,
            missing_slots=missing,
            clarification_questions=tuple(slot.follow_up_question for slot in missing),
        )


class BusinessSlotFiller:
    def fill(
        self,
        *,
        schema: BusinessSlotSchema,
        intent: IntentRecognitionResult,
        low_level_slots: object,
    ) -> BusinessSlotFrame:
        values: list[BusinessSlotValue] = []
        self._append(values, "query_object", _candidates(getattr(low_level_slots, "entities", [])), "slot_matching")
        self._append(values, "relationship_scope", _candidates(getattr(low_level_slots, "relationships", [])), "slot_matching")
        self._append(values, "attribute_set", _candidates(getattr(low_level_slots, "return_fields", [])), "slot_matching")
        self._append(values, "metric_family", _metric_names(getattr(low_level_slots, "metrics", [])), "slot_matching")
        self._append(values, "group_by_dimension", _candidates(getattr(low_level_slots, "group_by", [])), "slot_matching")
        self._append(values, "filters_thresholds", _filter_names(getattr(low_level_slots, "filters", [])), "slot_matching")
        self._append(values, "order_topn", _order_names(getattr(low_level_slots, "order_by", []), getattr(low_level_slots, "limit", None)), "slot_matching")
        self._append(values, "query_action", (_query_action(intent),), "intent")
        return BusinessSlotFrame(
            schema_id=schema.schema_id,
            scenario_id=schema.scenario_id,
            slots=tuple(slot for slot in values if slot.values),
        )

    def _append(self, values: list[BusinessSlotValue], name: str, raw_values: tuple[str, ...], source: str) -> None:
        if raw_values:
            values.append(BusinessSlotValue(name=name, values=raw_values, source=source))


@lru_cache(maxsize=1)
def get_default_business_slot_schema_registry() -> BusinessSlotSchemaRegistry:
    return BusinessSlotSchemaRegistry.from_path(
        Path(__file__).resolve().parents[1] / "config" / "business_slot_schemas.yaml"
    )


def _candidates(slots: object) -> tuple[str, ...]:
    return tuple(str(getattr(slot, "candidate")) for slot in slots if getattr(slot, "candidate", None))


def _metric_names(slots: object) -> tuple[str, ...]:
    return tuple(str(getattr(slot, "metric")) for slot in slots if getattr(slot, "metric", None))


def _filter_names(slots: object) -> tuple[str, ...]:
    values: list[str] = []
    for slot in slots:
        prop = getattr(slot, "property", None)
        operator = getattr(slot, "operator", None)
        value = getattr(slot, "value", None)
        if prop and operator and value is not None:
            values.append(f"{prop}{operator}{value}")
    return tuple(values)


def _order_names(order_slots: object, limit: object) -> tuple[str, ...]:
    values = [
        f"{getattr(slot, 'property')} {str(getattr(slot, 'direction')).upper()}"
        for slot in order_slots
        if getattr(slot, "property", None) and getattr(slot, "direction", None)
    ]
    limit_value = getattr(limit, "value", None)
    if values and limit_value is not None:
        values = [f"{values[0]} LIMIT {limit_value}", *values[1:]]
    return tuple(values)


def _query_action(intent: IntentRecognitionResult) -> str:
    primary = intent.primary_intent
    secondary = intent.secondary_intent
    if primary == "record_retrieval_query":
        if secondary == "entity_detail_query":
            return "detail"
        return "list"
    if primary == "metric_query":
        return "stat"
    if primary == "breakdown_query":
        return "stat"
    if primary == "ranking_query":
        return "topn"
    if primary == "existence_query":
        return "exists"
    return primary or "unknown"


def _required_str(value: dict[str, Any], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw:
        raise BusinessSlotSchemaConfigError(f"{key} must be a non-empty string")
    return raw


def _is_mapping(value: object, context: str) -> bool:
    if isinstance(value, dict):
        return True
    raise BusinessSlotSchemaConfigError(f"{context} entries must be mappings")
