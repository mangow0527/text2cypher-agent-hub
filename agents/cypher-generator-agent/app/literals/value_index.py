from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class ValueIndexEntry:
    owner: str
    property_name: str
    value: str
    metadata: Mapping[str, Any]


class StaticValueIndex:
    def __init__(
        self,
        values: Mapping[str, Mapping[str, Mapping[str, Mapping[str, Any]]]] | None = None,
        *,
        source: str = "empty",
        live_lookup: bool = False,
    ) -> None:
        if live_lookup:
            raise ValueError("LiteralResolver v1 only accepts static value indexes")
        self.source = source
        self.live_lookup = live_lookup
        self._values = values or {}

    @classmethod
    def empty(cls) -> "StaticValueIndex":
        return cls()

    @classmethod
    def from_path(cls, path: str | Path) -> "StaticValueIndex":
        with Path(path).open(encoding="utf-8") as file:
            payload = json.load(file)
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "StaticValueIndex":
        values = payload.get("values", {})
        if not isinstance(values, Mapping):
            raise ValueError("static value index values must be a mapping")
        return cls(
            values=values,
            source=str(payload.get("source", "static_value_index")),
            live_lookup=bool(payload.get("live_lookup", False)),
        )

    def lookup_exact(
        self,
        owner: str,
        property_name: str,
        raw_literal: str,
    ) -> ValueIndexEntry | None:
        property_values = self._property_values(owner, property_name)
        if not property_values:
            return None

        normalized_raw = normalize_literal_text(raw_literal)
        for value, metadata in property_values.items():
            if normalize_literal_text(value) == normalized_raw:
                return ValueIndexEntry(
                    owner=owner,
                    property_name=property_name,
                    value=value,
                    metadata=metadata,
                )
        return None

    def iter_values(self, owner: str, property_name: str) -> list[ValueIndexEntry]:
        property_values = self._property_values(owner, property_name)
        return [
            ValueIndexEntry(
                owner=owner,
                property_name=property_name,
                value=value,
                metadata=metadata,
            )
            for value, metadata in property_values.items()
        ]

    def has_property(self, owner: str, property_name: str) -> bool:
        return bool(self._property_values(owner, property_name))

    def _property_values(
        self,
        owner: str,
        property_name: str,
    ) -> Mapping[str, Mapping[str, Any]]:
        owner_values = self._values.get(owner, {})
        if not isinstance(owner_values, Mapping):
            return {}
        property_values = owner_values.get(property_name, {})
        if not isinstance(property_values, Mapping):
            return {}
        return property_values


def normalize_literal_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    return " ".join(text.split())
