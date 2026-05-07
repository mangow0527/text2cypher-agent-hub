from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


NODE_LABEL_PATTERN = re.compile(r"\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")
REL_LABEL_PATTERN = re.compile(r"-\s*\[\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")
VAR_LABEL_PATTERN = re.compile(r"\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")
PROPERTY_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")


@dataclass(frozen=True)
class SchemaReferences:
    node_labels: set[str] = field(default_factory=set)
    relationship_labels: set[str] = field(default_factory=set)
    properties_by_label: dict[str, set[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class SchemaValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


class SchemaAwareValidator:
    def __init__(self, schema: Any) -> None:
        self.references = self._build_references(schema)

    def validate_content(self, content: str) -> SchemaValidationResult:
        if not content.strip():
            return SchemaValidationResult(valid=True)

        errors: list[str] = []
        variable_labels = {
            match.group(1): match.group(2)
            for match in VAR_LABEL_PATTERN.finditer(content)
        }

        for label in sorted(set(NODE_LABEL_PATTERN.findall(content))):
            if self.references.node_labels and label not in self.references.node_labels:
                errors.append(f"unknown node label: {label}")

        for label in sorted(set(REL_LABEL_PATTERN.findall(content))):
            if self.references.relationship_labels and label not in self.references.relationship_labels:
                errors.append(f"unknown relationship label: {label}")

        for variable, prop in sorted(set(PROPERTY_PATTERN.findall(content))):
            label = variable_labels.get(variable)
            if not label and variable[:1].isupper():
                label = variable
                if self.references.node_labels and label not in self.references.node_labels:
                    errors.append(f"unknown node label: {label}")
                    continue
            if not label:
                continue
            known_properties = self.references.properties_by_label.get(label)
            if known_properties and prop not in known_properties:
                errors.append(f"unknown property: {label}.{prop}")

        return SchemaValidationResult(valid=not errors, errors=errors)

    def _build_references(self, schema: Any) -> SchemaReferences:
        node_labels: set[str] = set()
        relationship_labels: set[str] = set()
        properties_by_label: dict[str, set[str]] = {}

        if isinstance(schema, list):
            for item in schema:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "").upper()
                label = self._label_from_item(item)
                if not label:
                    continue
                if item_type == "VERTEX":
                    node_labels.add(label)
                    properties_by_label[label] = self._properties_from_item(item)
                elif item_type == "EDGE":
                    relationship_labels.add(label)
            return SchemaReferences(node_labels, relationship_labels, properties_by_label)

        if isinstance(schema, dict):
            for item in schema.get("vertex_labels", []) + schema.get("node_labels", []):
                label = self._label_from_item(item)
                if not label:
                    continue
                node_labels.add(label)
                properties_by_label[label] = self._properties_from_item(item)
            for item in schema.get("edge_labels", []) + schema.get("relationship_labels", []):
                label = self._label_from_item(item)
                if label:
                    relationship_labels.add(label)

        return SchemaReferences(node_labels, relationship_labels, properties_by_label)

    def _label_from_item(self, item: Any) -> str:
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            return str(item.get("label") or item.get("name") or "").strip()
        return ""

    def _properties_from_item(self, item: Any) -> set[str]:
        if not isinstance(item, dict):
            return set()
        properties: set[str] = set()
        for prop in item.get("properties", []):
            if isinstance(prop, str):
                properties.add(prop)
            elif isinstance(prop, dict):
                name = str(prop.get("name") or prop.get("label") or "").strip()
                if name:
                    properties.add(name)
        return properties
