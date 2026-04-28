from __future__ import annotations

import hashlib
from typing import Any

from app.domain.generation.registry import QUERY_TYPE_REGISTRY
from app.domain.models import CanonicalSchemaSpec, GenerationLimits, QueryPlan


class QueryPlanService:
    def build_plans(
        self,
        schema: CanonicalSchemaSpec,
        limits: GenerationLimits,
        target_qa_count: int,
        diversity_key: str | None = None,
    ) -> list[QueryPlan]:
        families = self._select_family_pool(schema, target_qa_count)
        if not families:
            return []

        requested = min(target_qa_count, limits.max_skeletons)
        start = self._rotation_start(diversity_key, len(families))
        rotated = families[start:] + families[:start]

        plans: list[QueryPlan] = []
        selected_families = self._sample_rotated_families(rotated, requested)
        for index, family in enumerate(selected_families):
            difficulty_band = family["difficulty_band"]
            difficulty = difficulty_band[(start + index) % len(difficulty_band)]
            bindings = self._build_bindings(schema, family["structural_markers"], index)
            plans.append(
                QueryPlan(
                    query_type=family["query_type"],
                    structure_family=family["family"],
                    difficulty=difficulty,
                    bindings=bindings,
                    required_semantics=self._required_semantics(family["structural_markers"], bindings),
                    disallowed_constructs=[
                        "optional match",
                        "call",
                        "apoc",
                        "collect(",
                        "case ",
                    ],
                    rationale=family.get("description", ""),
                )
            )
        return plans

    def _sample_rotated_families(self, families: list[dict[str, Any]], requested: int) -> list[dict[str, Any]]:
        if requested >= len(families):
            return families[:requested]
        if requested <= 1:
            return families[:1]
        step = len(families) / requested
        selected: list[dict[str, Any]] = []
        used_indexes: set[int] = set()
        cursor = 0.0
        for _ in range(requested):
            index = min(len(families) - 1, int(cursor))
            while index in used_indexes and index < len(families) - 1:
                index += 1
            if index in used_indexes:
                index = next(candidate for candidate in range(len(families)) if candidate not in used_indexes)
            selected.append(families[index])
            used_indexes.add(index)
            cursor += step
        return selected

    def _select_family_pool(self, schema: CanonicalSchemaSpec, target_qa_count: int) -> list[dict[str, Any]]:
        families = self._flatten_registry()
        if target_qa_count > 3:
            return families

        preferred = []
        for family in families:
            markers = family["structural_markers"]
            if markers.get("min_hops", 0) > 0:
                continue
            if markers.get("with_stage") or markers.get("subquery") or markers.get("comparison") or markers.get("variable_length"):
                continue
            if markers.get("filtering") and not self._has_catalog_value(schema):
                continue
            preferred.append(family)
        return preferred or families

    def _has_catalog_value(self, schema: CanonicalSchemaSpec) -> bool:
        return any(values for values in schema.value_catalog.values())

    def _flatten_registry(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for query_type, families in QUERY_TYPE_REGISTRY.items():
            for family in families:
                output.append({**family, "query_type": query_type})
        return output

    def _rotation_start(self, diversity_key: str | None, pool_size: int) -> int:
        if pool_size <= 1:
            return 0
        if not diversity_key:
            return 0
        digest = hashlib.sha256(diversity_key.encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % pool_size

    def _build_bindings(self, schema: CanonicalSchemaSpec, markers: dict[str, Any], variant_index: int) -> dict[str, Any]:
        edge_triplets = self._edge_triplets(schema)
        if edge_triplets:
            first_edge, source, target = edge_triplets[variant_index % len(edge_triplets)]
            second_edge, _, target2 = edge_triplets[(variant_index + 1) % len(edge_triplets)]
            third_edge, _, target3 = edge_triplets[(variant_index + 2) % len(edge_triplets)]
        else:
            source = schema.node_types[variant_index % len(schema.node_types)]
            target = schema.node_types[(variant_index + 1) % len(schema.node_types)] if len(schema.node_types) > 1 else source
            target2 = schema.node_types[(variant_index + 2) % len(schema.node_types)] if len(schema.node_types) > 2 else target
            target3 = schema.node_types[(variant_index + 3) % len(schema.node_types)] if len(schema.node_types) > 3 else target2
            first_edge = schema.edge_types[variant_index % len(schema.edge_types)] if schema.edge_types else "RELATED_TO"
            second_edge = schema.edge_types[(variant_index + 1) % len(schema.edge_types)] if schema.edge_types else first_edge
            third_edge = schema.edge_types[(variant_index + 2) % len(schema.edge_types)] if schema.edge_types else second_edge

        property_name = self._pick_property(schema, source, variant_index)
        property2_name = self._pick_property(schema, target, variant_index)
        property3_name = self._pick_property(schema, target2, variant_index)

        return {
            "node": source,
            "target": target,
            "target2": target2,
            "target3": target3,
            "edge": first_edge,
            "edge2": second_edge,
            "edge3": third_edge,
            "property": property_name,
            "property2": property2_name,
            "property3": property3_name,
            "value": self._pick_value(schema, source, property_name, variant_index),
            "value2": self._pick_value(schema, target2, property3_name, variant_index + 1),
            "limit": [3, 5, 10][variant_index % 3],
            "order_direction": "DESC" if variant_index % 2 == 0 else "ASC",
            "path_min_hops": 1,
            "path_max_hops": 2 + (variant_index % 3),
            "count_threshold": 2 + (variant_index % 3),
            "node_l": "a",
            "target_l": "b",
            "target2_l": "c",
            "target3_l": "d",
            "markers": markers,
        }

    def _required_semantics(self, markers: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
        semantics: dict[str, Any] = {}
        if markers.get("ordering"):
            semantics["ordering"] = True
            if markers.get("topk") or markers.get("limit"):
                semantics["limit"] = bindings.get("limit")
        if markers.get("aggregation"):
            semantics["aggregation"] = True
        if markers.get("grouping"):
            semantics["grouping"] = True
        if markers.get("filtering"):
            semantics["filtering"] = True
        if markers.get("temporal"):
            semantics["temporal"] = True
        if markers.get("comparison"):
            semantics["comparison"] = True
        if markers.get("variable_length"):
            semantics["variable_length"] = True
        if markers.get("min_hops", 0):
            semantics["min_hops"] = markers["min_hops"]
        if markers.get("with_stage") or markers.get("subquery"):
            semantics["with_stage"] = True
        return semantics

    def _edge_triplets(self, schema: CanonicalSchemaSpec) -> list[tuple[str, str, str]]:
        output: list[tuple[str, str, str]] = []
        for edge_name, constraints in schema.edge_constraints.items():
            if constraints:
                output.extend((edge_name, src, dst) for src, dst in constraints)
        return output

    def _pick_property(self, schema: CanonicalSchemaSpec, node: str, variant_index: int) -> str:
        props = schema.node_properties.get(node, {})
        keys = list(props.keys()) if isinstance(props, dict) else []
        return keys[variant_index % len(keys)] if keys else "id"

    def _pick_value(self, schema: CanonicalSchemaSpec, node: str, property_name: str, variant_index: int) -> Any:
        values = schema.value_catalog.get(f"{node}.{property_name}", [])
        if values:
            return values[variant_index % len(values)]
        property_type = str(schema.node_properties.get(node, {}).get(property_name, "string")).lower()
        if any(token in property_type for token in ("int", "long", "double", "float", "number")):
            return 1
        if "bool" in property_type:
            return True
        return "sample"
