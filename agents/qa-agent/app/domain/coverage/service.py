from __future__ import annotations

import hashlib
from typing import Any

from app.domain.models import CanonicalSchemaSpec, CoverageSpec, GenerationLimits


LEVEL_BLUEPRINTS: list[dict[str, Any]] = [
    {
        "difficulty": "L1",
        "intent": "lookup",
        "operators": [],
        "topology": "zero_hop",
        "answer_type": "node",
        "template_id": "l1_lookup_node",
        "template_ids": ["l1_lookup_node", "l1_project_property"],
        "query_type": "LOOKUP",
        "structure_family": "lookup_node_return",
    },
    {
        "difficulty": "L2",
        "intent": "filter",
        "operators": ["where"],
        "topology": "zero_hop",
        "answer_type": "node",
        "template_id": "l2_filter_entity",
        "template_ids": ["l2_filter_entity", "l2_project_filtered_property"],
        "query_type": "FILTER",
        "structure_family": "filter_single_condition",
    },
    {
        "difficulty": "L3",
        "intent": "path",
        "operators": [],
        "topology": "one_hop",
        "answer_type": "node",
        "template_id": "l3_one_hop",
        "template_ids": ["l3_one_hop", "l3_one_hop_projection"],
        "query_type": "PATH",
        "structure_family": "path_existence",
    },
    {
        "difficulty": "L4",
        "intent": "aggregation",
        "operators": ["count"],
        "topology": "zero_hop",
        "answer_type": "scalar",
        "template_id": "l4_count",
        "template_ids": ["l4_count", "l4_count_property"],
        "query_type": "AGGREGATION",
        "structure_family": "aggregate_global_count",
    },
    {
        "difficulty": "L5",
        "intent": "path",
        "operators": [],
        "topology": "two_hop",
        "answer_type": "node",
        "template_id": "l5_two_hop",
        "template_ids": ["l5_two_hop", "l5_two_hop_projection"],
        "query_type": "MULTI_HOP",
        "structure_family": "two_hop_return",
    },
    {
        "difficulty": "L6",
        "intent": "path_filter_aggregation",
        "operators": ["where", "count", "order_by", "limit"],
        "topology": "two_hop",
        "answer_type": "table",
        "template_id": "l6_two_hop_filtered_aggregate",
        "template_ids": ["l6_two_hop_filtered_aggregate", "l6_two_hop_target_filtered_aggregate"],
        "query_type": "MULTI_HOP",
        "structure_family": "two_hop_filtered",
    },
    {
        "difficulty": "L7",
        "intent": "path",
        "operators": [],
        "topology": "three_hop",
        "answer_type": "node",
        "template_id": "l7_three_hop",
        "template_ids": ["l7_three_hop", "l7_three_hop_projection"],
        "query_type": "MULTI_HOP",
        "structure_family": "multi_hop_projection",
    },
    {
        "difficulty": "L8",
        "intent": "subquery_aggregation",
        "operators": ["with", "count"],
        "topology": "multi_stage",
        "answer_type": "table",
        "template_id": "l8_with_nested_aggregation",
        "template_ids": ["l8_with_nested_aggregation", "l8_with_path_refine_aggregation"],
        "query_type": "SUBQUERY",
        "structure_family": "with_stage_aggregate",
    },
]


class CoverageService:
    def build_specs(
        self,
        schema: CanonicalSchemaSpec,
        limits: GenerationLimits,
        target_qa_count: int,
        diversity_key: str | None = None,
    ) -> list[CoverageSpec]:
        if not schema.node_types:
            return []

        requested = min(max(target_qa_count, 1), limits.max_skeletons)
        blueprints = self._rotated_blueprints(diversity_key)
        specs: list[CoverageSpec] = []
        index = 0
        while len(specs) < requested:
            blueprint = blueprints[index % len(blueprints)]
            template_ids = blueprint.get("template_ids") or [blueprint["template_id"]]
            template_cycle = index // len(blueprints)
            template_id = template_ids[template_cycle % len(template_ids)]
            bindings = self._build_bindings(schema, index)
            specs.append(
                CoverageSpec(
                    intent=blueprint["intent"],
                    operators=list(blueprint["operators"]),
                    topology=blueprint["topology"],
                    answer_type=blueprint["answer_type"],
                    target_difficulty=blueprint["difficulty"],
                    template_id=template_id,
                    query_type=blueprint["query_type"],
                    structure_family=blueprint["structure_family"],
                    bindings=bindings,
                )
            )
            index += 1
        return specs

    def _rotated_blueprints(self, diversity_key: str | None) -> list[dict[str, Any]]:
        if not diversity_key:
            return list(LEVEL_BLUEPRINTS)
        digest = hashlib.sha256(diversity_key.encode("utf-8")).hexdigest()
        start = int(digest[:8], 16) % len(LEVEL_BLUEPRINTS)
        rotated = LEVEL_BLUEPRINTS[start:] + LEVEL_BLUEPRINTS[:start]
        if len(rotated) >= 8:
            first_cycle = sorted(rotated[:8], key=lambda item: int(item["difficulty"][1:]))
            return first_cycle + rotated[8:]
        return rotated

    def _build_bindings(self, schema: CanonicalSchemaSpec, variant_index: int) -> dict[str, Any]:
        triplets = self._edge_triplets(schema)
        if triplets:
            path = self._continuous_path(triplets, length=3, variant_index=variant_index)
            edge, source, target = path[0]
            edge2, _, target2 = path[1] if len(path) > 1 else (edge, source, target)
            edge3, _, target3 = path[2] if len(path) > 2 else (edge2, target, target2)
        else:
            source = schema.node_types[variant_index % len(schema.node_types)]
            target = schema.node_types[(variant_index + 1) % len(schema.node_types)] if len(schema.node_types) > 1 else source
            target2 = schema.node_types[(variant_index + 2) % len(schema.node_types)] if len(schema.node_types) > 2 else target
            target3 = schema.node_types[(variant_index + 3) % len(schema.node_types)] if len(schema.node_types) > 3 else target2
            edge = schema.edge_types[variant_index % len(schema.edge_types)] if schema.edge_types else "RELATED_TO"
            edge2 = schema.edge_types[(variant_index + 1) % len(schema.edge_types)] if len(schema.edge_types) > 1 else edge
            edge3 = schema.edge_types[(variant_index + 2) % len(schema.edge_types)] if len(schema.edge_types) > 2 else edge2

        property_name = self._pick_property(schema, source, variant_index)
        property2_name = self._pick_property(schema, target, variant_index)
        property3_name = self._pick_property(schema, target2, variant_index)
        return {
            "node": source,
            "target": target,
            "target2": target2,
            "target3": target3,
            "edge": edge,
            "edge2": edge2,
            "edge3": edge3,
            "property": property_name,
            "property2": property2_name,
            "property3": property3_name,
            "value": self._pick_value(schema, source, property_name, variant_index),
            "value2": self._pick_value(schema, target2, property3_name, variant_index + 1),
            "limit": self._pick_limit(variant_index),
            "order_direction": "DESC" if (variant_index // len(LEVEL_BLUEPRINTS)) % 2 == 0 else "ASC",
            "node_l": "a",
            "target_l": "b",
            "target2_l": "c",
            "target3_l": "d",
        }

    def _edge_triplets(self, schema: CanonicalSchemaSpec) -> list[tuple[str, str, str]]:
        output: list[tuple[str, str, str]] = []
        for edge_name, constraints in schema.edge_constraints.items():
            output.extend((edge_name, src, dst) for src, dst in constraints)
        return output

    def _next_triplet(
        self,
        triplets: list[tuple[str, str, str]],
        source: str,
        variant_index: int,
    ) -> tuple[str, str, str]:
        matching = [triplet for triplet in triplets if triplet[1] == source]
        if matching:
            return matching[variant_index % len(matching)]
        return triplets[variant_index % len(triplets)]

    def _continuous_path(
        self,
        triplets: list[tuple[str, str, str]],
        length: int,
        variant_index: int,
    ) -> list[tuple[str, str, str]]:
        paths = self._continuous_paths(triplets, length)
        if paths:
            return paths[variant_index % len(paths)]
        return [triplets[variant_index % len(triplets)]]

    def _continuous_paths(
        self,
        triplets: list[tuple[str, str, str]],
        length: int,
    ) -> list[list[tuple[str, str, str]]]:
        if length <= 1:
            return [[triplet] for triplet in triplets]
        return [
            path
            for triplet in triplets
            for path in self._extend_path(triplets, [triplet], length)
        ]

    def _extend_path(
        self,
        triplets: list[tuple[str, str, str]],
        current_path: list[tuple[str, str, str]],
        target_length: int,
    ) -> list[list[tuple[str, str, str]]]:
        if len(current_path) >= target_length:
            return [current_path]
        tail = current_path[-1]
        continuations = [candidate for candidate in triplets if candidate[1] == tail[2]]
        output: list[list[tuple[str, str, str]]] = []
        for continuation in continuations:
            output.extend(self._extend_path(triplets, [*current_path, continuation], target_length))
        return output

    def _pick_property(self, schema: CanonicalSchemaSpec, node: str, variant_index: int) -> str:
        props = schema.node_properties.get(node, {})
        names = list(props.keys()) if isinstance(props, dict) else []
        return names[variant_index % len(names)] if names else "id"

    def _pick_value(self, schema: CanonicalSchemaSpec, node: str, property_name: str, variant_index: int):
        values = schema.value_catalog.get(f"{node}.{property_name}", [])
        if values:
            return values[variant_index % len(values)]
        property_type = str(schema.node_properties.get(node, {}).get(property_name, "string")).lower()
        if any(token in property_type for token in ("int", "long", "double", "float", "number", "decimal")):
            return 1
        if "bool" in property_type:
            return True
        return "sample"

    def _pick_limit(self, variant_index: int) -> int:
        return [3, 5, 10][variant_index % 3]
