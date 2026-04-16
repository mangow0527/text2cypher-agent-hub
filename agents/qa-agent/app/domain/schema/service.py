from __future__ import annotations

from app.domain.models import CanonicalSchemaSpec
from app.errors import AppError


class SchemaService:
    def normalize(self, schema_input: dict) -> CanonicalSchemaSpec:
        if isinstance(schema_input, list):
            return self._normalize_tugraph_schema_array(schema_input)
        if isinstance(schema_input, dict):
            return self._normalize_object_schema(schema_input)
        raise AppError("SCHEMA_PARSE_ERROR", "Schema input must be a JSON object or a TuGraph schema array.")

    def _normalize_object_schema(self, schema_input: dict) -> CanonicalSchemaSpec:
        nodes = schema_input.get("node_types") or list((schema_input.get("nodes") or {}).keys())
        edges = schema_input.get("edge_types") or list((schema_input.get("edges") or {}).keys())
        node_props = schema_input.get("node_properties") or schema_input.get("nodes") or {}
        edge_props = schema_input.get("edge_properties") or schema_input.get("edges") or {}

        if not nodes:
            raise AppError("SCHEMA_VALIDATION_ERROR", "At least one node type is required.")

        return CanonicalSchemaSpec(
            node_types=nodes,
            edge_types=edges,
            node_properties=node_props,
            edge_properties=edge_props,
            edge_constraints=schema_input.get("edge_constraints", {}),
            primary_keys=schema_input.get("primary_keys", {}),
            constraints=schema_input.get("constraints", []),
            indexes=schema_input.get("indexes", []),
            value_catalog=schema_input.get("value_catalog", {}),
            semantic_alias=schema_input.get("semantic_alias", {}),
            raw_schema={"items": schema_input},
        )

    def _normalize_tugraph_schema_array(self, schema_items: list[dict]) -> CanonicalSchemaSpec:
        node_types: list[str] = []
        edge_types: list[str] = []
        node_properties: dict[str, dict[str, str]] = {}
        edge_properties: dict[str, dict[str, str]] = {}
        edge_constraints: dict[str, list[list[str]]] = {}
        primary_keys: dict[str, str] = {}
        indexes: list[str] = []
        constraints: list[str] = []
        value_catalog: dict[str, list[str]] = {}

        for item in schema_items:
            label = item.get("label")
            item_type = item.get("type")
            if not label or not item_type:
                continue

            properties = {
                prop["name"]: prop.get("type", "STRING")
                for prop in item.get("properties", [])
                if isinstance(prop, dict) and prop.get("name")
            }

            if item_type == "VERTEX":
                node_types.append(label)
                node_properties[label] = properties
                if item.get("primary"):
                    primary_keys[label] = item["primary"]
            elif item_type == "EDGE":
                edge_types.append(label)
                edge_properties[label] = properties
                edge_constraints[label] = item.get("constraints", [])

            for prop in item.get("properties", []):
                if prop.get("index"):
                    indexes.append(f"{label}.{prop.get('name')}")
                if prop.get("unique"):
                    constraints.append(f"UNIQUE {label}.{prop.get('name')}")
                if prop.get("description"):
                    catalog_values = self._extract_enum_like_values(prop["description"])
                    if catalog_values:
                        value_catalog[f"{label}.{prop['name']}"] = catalog_values

        if not node_types:
            raise AppError("SCHEMA_VALIDATION_ERROR", "No vertex labels were found in schema input.")

        return CanonicalSchemaSpec(
            node_types=node_types,
            edge_types=edge_types,
            node_properties=node_properties,
            edge_properties=edge_properties,
            edge_constraints=edge_constraints,
            primary_keys=primary_keys,
            constraints=constraints,
            indexes=indexes,
            value_catalog=value_catalog,
            semantic_alias={},
            raw_schema={"items": schema_items},
        )

    def _extract_enum_like_values(self, description: str) -> list[str]:
        if "|" not in description:
            return []
        tail = description.split(":")[-1]
        parts = [part.strip() for part in tail.split("|")]
        return [part for part in parts if part]
