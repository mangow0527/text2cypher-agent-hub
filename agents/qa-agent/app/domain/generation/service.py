from __future__ import annotations

import json
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

from app.domain.generation.registry import QUERY_TYPE_REGISTRY
from app.domain.models import CanonicalSchemaSpec, CypherCandidate, CypherSkeleton, GenerationLimits, ModelConfig
from app.errors import AppError
from app.integrations.openai.model_gateway import ModelGateway


FAMILY_TEMPLATES: dict[str, str] = {
    "lookup_node_return": "MATCH (n:{node}) RETURN n LIMIT 5",
    "lookup_property_projection": "MATCH (n:{node}) RETURN n.{property} AS value LIMIT 5",
    "lookup_entity_detail": "MATCH (n:{node}) WHERE n.{property} = {value} RETURN n LIMIT 5",
    "filter_single_condition": "MATCH (n:{node}) WHERE n.{property} = {value} RETURN n LIMIT 10",
    "filter_boolean_combo": "MATCH (n:{node}) WHERE n.{property} = {value} OR n.{property} STARTS WITH {value} RETURN n LIMIT 10",
    "filter_range_condition": "MATCH (n:{node}) WHERE n.{property} >= {value} RETURN n LIMIT 10",
    "sort_single_metric": "MATCH (n:{node}) RETURN n.{property} AS value ORDER BY value DESC LIMIT 5",
    "topk_entities": "MATCH (n:{node}) RETURN n ORDER BY n.{property} DESC LIMIT 5",
    "sorted_filtered_projection": "MATCH (n:{node}) WHERE n.{property} = '{value}' RETURN n.{property} AS value ORDER BY value DESC LIMIT 5",
    "aggregate_global_count": "MATCH (n:{node}) RETURN count(n) AS total",
    "aggregate_filtered_count": "MATCH (n:{node}) WHERE n.{property} = {value} RETURN count(n) AS total",
    "aggregate_scalar_metric": "MATCH (n:{node}) RETURN count(n) AS total",
    "group_count": "MATCH (n:{node}) RETURN n.{property} AS group_key, count(*) AS total",
    "group_ranked_count": "MATCH (n:{node}) RETURN n.{property} AS group_key, count(*) AS total ORDER BY total DESC LIMIT 5",
    "group_filtered_aggregate": "MATCH (n:{node}) WHERE n.{property} = {value} RETURN n.{property} AS group_key, count(*) AS total",
    "two_hop_return": "MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(c:{target2}) RETURN c LIMIT 5",
    "two_hop_filtered": "MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(c:{target2}) WHERE a.{property} = {value} RETURN c LIMIT 5",
    "multi_hop_projection": "MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(:{target2}) RETURN a.{property} AS key LIMIT 5",
    "attribute_comparison": "MATCH (a:{node}), (b:{node}) WHERE a.{property} <> b.{property} RETURN a.{property} AS left_value, b.{property} AS right_value LIMIT 5",
    "aggregate_comparison": "MATCH (a:{node})-[:{edge}]->(b:{target}) WITH a, count(b) AS total WHERE total >= 1 RETURN a.{property} AS key, total ORDER BY total DESC LIMIT 5",
    "rank_position_comparison": "MATCH (n:{node}) RETURN n ORDER BY n.{property} DESC LIMIT 5",
    "time_range_filter": "MATCH (n:{node}) WHERE n.{property} >= {value} RETURN n LIMIT 10",
    "recent_or_earliest": "MATCH (n:{node}) RETURN n ORDER BY n.{property} DESC LIMIT 1",
    "temporal_ordering": "MATCH (n:{node}) RETURN n.{property} AS event_time ORDER BY event_time DESC LIMIT 5",
    "path_existence": "MATCH p=({node_l}:{node})-[:{edge}]->({target_l}:{target}) RETURN p LIMIT 5",
    "variable_length_path": "MATCH p=({node_l}:{node})-[:{edge}*1..3]->({target_l}:{target}) RETURN p LIMIT 5",
    "path_constrained_target": "MATCH p=({node_l}:{node})-[:{edge}]->(:{target})-[:{edge2}]->({target2_l}:{target2}) WHERE {target2_l}.{property3} = {value2} RETURN p LIMIT 5",
    "distinct_projection": "MATCH (n:{node}) RETURN DISTINCT n.{property} AS value LIMIT 5",
    "set_like_union_projection": "MATCH (a:{node}) RETURN a.{property} AS value UNION MATCH (b:{target}) RETURN b.{property2} AS value",
    "membership_intersection_style": "MATCH (a:{node})-[:{edge}]->(b:{target}) WHERE a.{property} = {value} AND b.{property2} IS NOT NULL RETURN DISTINCT a",
    "with_stage_filter": "MATCH (n:{node}) WITH n MATCH (n)-[:{edge}]->(b:{target}) WHERE n.{property} = {value} RETURN n, b LIMIT 5",
    "with_stage_aggregate": "MATCH (a:{node})-[:{edge}]->(b:{target}) WITH a, count(b) AS cnt MATCH (a)-[:{edge2}]->(c:{target2}) RETURN a.{property} AS key, count(c) AS total",
    "two_stage_refine": "MATCH (n:{node}) WITH n MATCH (n)-[:{edge}]->(b:{target}) RETURN n.{property} AS key, b LIMIT 5",
    "temporal_aggregate_hybrid": "MATCH (n:{node}) WHERE n.{property} >= {value} RETURN count(n) AS total",
    "path_aggregate_hybrid": "MATCH p=({node_l}:{node})-[:{edge}*1..3]->({target_l}:{target}) RETURN count(p) AS total",
    "comparison_subquery_hybrid": "MATCH (a:{node})-[:{edge}]->(b:{target}) WITH a, count(b) AS cnt MATCH (a)-[:{edge2}]->(c:{target2}) WHERE cnt > 0 RETURN a.{property} AS key, count(c) AS total",
}


class GenerationService:
    def __init__(self, model_gateway: ModelGateway | None = None) -> None:
        self.model_gateway = model_gateway or ModelGateway()

    def build_skeletons(
        self,
        schema: CanonicalSchemaSpec,
        limits: GenerationLimits,
        diversity_key: str | None = None,
    ) -> List[CypherSkeleton]:
        if not schema.node_types:
            raise AppError("SKELETON_BUILD_ERROR", "No node types available for skeleton generation.")

        full_pool: List[CypherSkeleton] = []
        index = 1
        for query_type, families in QUERY_TYPE_REGISTRY.items():
            for family in families:
                structure_family = family["family"]
                template = FAMILY_TEMPLATES.get(structure_family)
                if not template:
                    raise AppError("SKELETON_BUILD_ERROR", f"Missing template for family: {structure_family}")
                for difficulty_level in family["difficulty_band"]:
                    full_pool.append(
                        CypherSkeleton(
                            skeleton_id=f"{structure_family}_{difficulty_level.lower()}_{index:02d}",
                            query_types=[query_type],
                            structure_family=structure_family,
                            pattern_template=template,
                            slots=self._slots_for_template(template),
                            difficulty_floor=difficulty_level,
                        )
                    )
                    index += 1
        if len(full_pool) <= limits.max_skeletons:
            return full_pool
        start = self._rotation_start(diversity_key, len(full_pool))
        rotated = full_pool[start:] + full_pool[:start]
        return rotated[: limits.max_skeletons]

    def _rotation_start(self, diversity_key: str | None, pool_size: int) -> int:
        if not diversity_key or pool_size <= 1:
            return 0
        digest = hashlib.sha256(diversity_key.encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % pool_size

    def instantiate_candidates(
        self,
        schema: CanonicalSchemaSpec,
        skeletons: List[CypherSkeleton],
        limits: GenerationLimits,
        model_config: ModelConfig | None = None,
    ) -> List[CypherCandidate]:
        with ThreadPoolExecutor(max_workers=min(2, max(1, len(skeletons)))) as executor:
            nested = list(
                executor.map(
                    lambda skeleton: self._instantiate_for_skeleton(schema, skeleton, limits, model_config),
                    skeletons,
                )
            )
        return [candidate for group in nested for candidate in group]

    def _instantiate_for_skeleton(
        self,
        schema: CanonicalSchemaSpec,
        skeleton: CypherSkeleton,
        limits: GenerationLimits,
        model_config: ModelConfig | None = None,
    ) -> List[CypherCandidate]:
        candidates: List[CypherCandidate] = []
        bindings = self._pick_pattern_bindings(schema, 0)
        template_candidate = self._build_template_candidate(schema, skeleton, bindings, variant_index=0)
        candidates.append(template_candidate)
        if limits.max_candidates_per_skeleton > 1:
            for variant_index in range(1, limits.max_candidates_per_skeleton):
                variant_bindings = self._pick_pattern_bindings(schema, variant_index)
                candidates.append(self._build_template_candidate(schema, skeleton, variant_bindings, variant_index=variant_index))
        if model_config:
            candidates.extend(self._safe_build_llm_candidates(schema, skeleton, bindings, template_candidate, model_config))
        return candidates

    def _safe_build_llm_candidates(
        self,
        schema: CanonicalSchemaSpec,
        skeleton: CypherSkeleton,
        bindings: Dict[str, str],
        template_candidate: CypherCandidate,
        model_config: ModelConfig,
    ) -> List[CypherCandidate]:
        try:
            return self._build_llm_candidates(schema, skeleton, bindings, template_candidate, model_config)
        except AppError as exc:
            if exc.code == "OPENAI_NOT_CONFIGURED":
                return []
            raise

    def _build_template_candidate(
        self,
        schema: CanonicalSchemaSpec,
        skeleton: CypherSkeleton,
        bindings: Dict[str, str],
        variant_index: int = 0,
    ) -> CypherCandidate:
        node = bindings["node"]
        target = bindings["target"]
        target2 = bindings["target2"]
        property_name = self._pick_property(schema, node, variant_index)
        property2_name = self._pick_property(schema, target, variant_index)
        property3_name = self._pick_property(schema, target2, variant_index)
        value = self._pick_value(schema, node, property_name, variant_index)
        value2 = self._pick_value(schema, target2, property3_name, variant_index)
        cypher = skeleton.pattern_template.format(
            **bindings,
            property=property_name,
            property2=property2_name,
            property3=property3_name,
            value=value,
            value2=value2,
        )
        return self._build_candidate(
            skeleton=skeleton,
            cypher=cypher,
            bindings=bindings,
            property_name=property_name,
            property2_name=property2_name,
            property3_name=property3_name,
            value=value,
            value2=value2,
            generation_mode="template",
        )

    def _build_llm_candidates(
        self,
        schema: CanonicalSchemaSpec,
        skeleton: CypherSkeleton,
        bindings: Dict[str, str],
        template_candidate: CypherCandidate,
        model_config: ModelConfig,
    ) -> List[CypherCandidate]:
        family_info = self._family_info(skeleton.query_types[0], skeleton.structure_family)
        property_name = self._pick_property(schema, bindings["node"], 0)
        property2_name = self._pick_property(schema, bindings["target"], 0)
        property3_name = self._pick_property(schema, bindings["target2"], 0)
        value = self._pick_value(schema, bindings["node"], property_name, 0)
        value2 = self._pick_value(schema, bindings["target2"], property3_name, 0)
        slot_bindings = {
            **bindings,
            "property": property_name,
            "property2": property2_name,
            "property3": property3_name,
            "value": value,
            "value2": value2,
        }
        bundle_text = self.model_gateway.generate_text(
            "cypher_candidate_bundle",
            model_config,
            schema_summary=self._build_schema_summary(schema, bindings),
            query_type=skeleton.query_types[0],
            structure_family=skeleton.structure_family,
            family_description=family_info.get("description", ""),
            difficulty=skeleton.difficulty_floor,
            template_cypher=template_candidate.cypher,
            slot_bindings=json.dumps(slot_bindings, ensure_ascii=False),
        )
        output: List[CypherCandidate] = []
        for mode, cypher in self._parse_candidate_bundle(bundle_text):
            normalized_mode = mode if mode in {"llm_direct", "llm_refine"} else "llm_direct"
            output.append(
                self._build_candidate(
                    skeleton=skeleton,
                    cypher=cypher,
                    bindings=bindings,
                    property_name=property_name,
                    property2_name=property2_name,
                    property3_name=property3_name,
                    value=value,
                    value2=value2,
                    generation_mode=normalized_mode,
                )
            )
        return output

    def _build_candidate(
        self,
        *,
        skeleton: CypherSkeleton,
        cypher: str,
        bindings: Dict[str, str],
        property_name: str,
        property2_name: str,
        property3_name: str,
        value: str,
        value2: str,
        generation_mode: str,
    ) -> CypherCandidate:
        bound_nodes = [bindings[key] for key in ("node", "target", "target2", "target3") if key in bindings]
        bound_edges = [bindings[key] for key in ("edge", "edge2", "edge3") if key in bindings]
        bound_properties = [prop for prop in (property_name, property2_name, property3_name) if prop]
        return CypherCandidate(
            skeleton_id=skeleton.skeleton_id,
            cypher=self._sanitize_cypher(cypher),
            query_types=skeleton.query_types,
            structure_family=skeleton.structure_family,
            generation_mode=generation_mode,
            bound_schema_items={
                "nodes": bound_nodes,
                "edges": bound_edges,
                "properties": bound_properties,
            },
            bound_values={property_name: value, property3_name: value2},
            difficulty=skeleton.difficulty_floor,
        )

    def _parse_candidate_bundle(self, bundle_text: str) -> List[tuple[str, str]]:
        try:
            payload = json.loads(bundle_text)
            candidates = payload.get("candidates", [])
            output = []
            if isinstance(candidates, list):
                for item in candidates:
                    if not isinstance(item, dict):
                        continue
                    cypher = self._sanitize_cypher(str(item.get("cypher", "")))
                    mode = str(item.get("mode", "llm_direct")).strip()
                    if cypher:
                        output.append((mode, cypher))
            if output:
                return output
        except json.JSONDecodeError:
            pass

        extracted = self._sanitize_cypher(bundle_text)
        return [("llm_direct", extracted)] if extracted else []

    def _sanitize_cypher(self, text: str) -> str:
        stripped = text.strip()
        fenced = re.findall(r"```(?:cypher)?\s*(.*?)```", stripped, flags=re.IGNORECASE | re.DOTALL)
        if fenced:
            stripped = fenced[0].strip()
        if "\n" in stripped:
            lines = [line.strip() for line in stripped.splitlines() if line.strip()]
            for idx, line in enumerate(lines):
                if line.upper().startswith(("MATCH ", "WITH ", "CALL ")):
                    stripped = " ".join(lines[idx:]).strip()
                    break
        if "MATCH " in stripped.upper() and not stripped.upper().startswith("MATCH "):
            match_start = stripped.upper().find("MATCH ")
            stripped = stripped[match_start:].strip()
        return stripped.rstrip(";")

    def _build_schema_summary(self, schema: CanonicalSchemaSpec, bindings: Dict[str, str]) -> str:
        lines: list[str] = []
        for node_key in ("node", "target", "target2", "target3"):
            node = bindings.get(node_key)
            if not node:
                continue
            props = schema.node_properties.get(node, {})
            if isinstance(props, dict):
                lines.append(f"节点 {node}: 属性 {', '.join(props.keys()) or '无'}")
        for edge_key in ("edge", "edge2", "edge3"):
            edge = bindings.get(edge_key)
            if not edge:
                continue
            constraints = schema.edge_constraints.get(edge, [])
            props = schema.edge_properties.get(edge, {})
            route = " | ".join(f"{src}->{dst}" for src, dst in constraints) if constraints else "无"
            prop_text = ", ".join(props.keys()) if isinstance(props, dict) else "无"
            lines.append(f"关系 {edge}: 约束 {route}; 属性 {prop_text}")
        return "\n".join(dict.fromkeys(lines)) if lines else "无摘要"

    def _family_info(self, query_type: str, structure_family: str) -> dict:
        families = QUERY_TYPE_REGISTRY.get(query_type, [])
        for family in families:
            if family["family"] == structure_family:
                return family
        return {}

    def _pick_property(self, schema: CanonicalSchemaSpec, node: str, variant_index: int = 0) -> str:
        properties = schema.node_properties.get(node, {})
        if isinstance(properties, list):
            if not properties:
                return "name"
            item = properties[variant_index % len(properties)]
            if isinstance(item, dict):
                return item.get("name", "name")
            return str(item)
        property_names = list(properties.keys()) if isinstance(properties, dict) else []
        return property_names[variant_index % len(property_names)] if property_names else "name"

    def _pick_value(self, schema: CanonicalSchemaSpec, node: str, property_name: str, variant_index: int = 0) -> str:
        key = f"{node}.{property_name}"
        values = schema.value_catalog.get(key, [])
        property_type = self._property_type(schema, node, property_name)
        if values:
            return self._format_literal(values[variant_index % len(values)], property_type)
        return self._default_literal(property_type)

    def _property_type(self, schema: CanonicalSchemaSpec, node: str, property_name: str) -> str:
        properties = schema.node_properties.get(node, {})
        if isinstance(properties, dict):
            return str(properties.get(property_name, "string"))
        return "string"

    def _default_literal(self, property_type: str) -> str:
        lower = property_type.lower()
        if any(token in lower for token in ("int", "long", "double", "float", "number", "decimal")):
            return "1"
        if "bool" in lower:
            return "true"
        return "'sample'"

    def _format_literal(self, value, property_type: str) -> str:
        lower = property_type.lower()
        if value is None:
            return self._default_literal(property_type)
        if any(token in lower for token in ("int", "long", "double", "float", "number", "decimal")):
            return str(value)
        if "bool" in lower:
            return "true" if bool(value) else "false"
        escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"

    def _pick_pattern_bindings(self, schema: CanonicalSchemaSpec, variant_index: int = 0) -> Dict[str, str]:
        if schema.edge_constraints:
            first_edge, source, target = self._pick_edge_triplet(schema, None, variant_index)
            second_edge, _, target2 = self._pick_edge_triplet(schema, target, variant_index + 1)
            third_edge, _, target3 = self._pick_edge_triplet(schema, target2, variant_index + 2)
        else:
            source = schema.node_types[variant_index % len(schema.node_types)]
            target = schema.node_types[(variant_index + 1) % len(schema.node_types)] if len(schema.node_types) > 1 else source
            target2 = schema.node_types[(variant_index + 2) % len(schema.node_types)] if len(schema.node_types) > 2 else target
            target3 = schema.node_types[(variant_index + 3) % len(schema.node_types)] if len(schema.node_types) > 3 else target2
            first_edge = schema.edge_types[variant_index % len(schema.edge_types)] if schema.edge_types else "RELATED_TO"
            second_edge = schema.edge_types[(variant_index + 1) % len(schema.edge_types)] if len(schema.edge_types) > 1 else first_edge
            third_edge = schema.edge_types[(variant_index + 2) % len(schema.edge_types)] if len(schema.edge_types) > 2 else second_edge

        return {
            "node": source,
            "target": target,
            "target2": target2,
            "target3": target3,
            "node_l": "a",
            "target_l": "b",
            "target2_l": "c",
            "target3_l": "d",
            "edge": first_edge,
            "edge2": second_edge,
            "edge3": third_edge,
        }

    def _pick_edge_triplet(
        self,
        schema: CanonicalSchemaSpec,
        required_source: str | None,
        variant_index: int,
    ) -> tuple[str, str, str]:
        options: list[tuple[str, str, str]] = []
        for edge_name, constraints in schema.edge_constraints.items():
            if constraints:
                for src, dst in constraints:
                    if required_source is None or src == required_source:
                        options.append((edge_name, src, dst))
            elif required_source is None:
                src = schema.node_types[variant_index % len(schema.node_types)]
                dst = schema.node_types[(variant_index + 1) % len(schema.node_types)] if len(schema.node_types) > 1 else src
                options.append((edge_name, src, dst))

        if options:
            return options[variant_index % len(options)]

        fallback_source = required_source or schema.node_types[variant_index % len(schema.node_types)]
        fallback_target = schema.node_types[(variant_index + 1) % len(schema.node_types)] if len(schema.node_types) > 1 else fallback_source
        fallback_edge = schema.edge_types[variant_index % len(schema.edge_types)] if schema.edge_types else "RELATED_TO"
        return fallback_edge, fallback_source, fallback_target

    def _slots_for_template(self, template: str) -> Dict[str, List[str]]:
        return {
            "node_slots": [slot for slot in ("node", "target", "target2", "target3") if f"{{{slot}}}" in template],
            "edge_slots": [slot for slot in ("edge", "edge2", "edge3") if f"{{{slot}}}" in template],
            "property_slots": [slot for slot in ("property", "property2", "property3") if f"{{{slot}}}" in template],
            "filter_slots": [slot for slot in ("value", "value2") if f"{{{slot}}}" in template],
            "agg_slots": ["aggregate"] if "count(" in template.lower() else [],
            "order_slots": ["ordering"] if "ORDER BY" in template.upper() else [],
            "return_slots": self._infer_return_slots(template),
        }

    def _infer_return_slots(self, template: str) -> List[str]:
        upper = template.upper()
        if "RETURN P" in upper:
            return ["p"]
        if "RETURN N, B" in upper:
            return ["n", "b"]
        if "RETURN N, B LIMIT" in upper:
            return ["n", "b"]
        if "RETURN A, B" in upper:
            return ["a", "b"]
        if "RETURN C LIMIT" in upper:
            return ["c"]
        if "RETURN D LIMIT" in upper:
            return ["d"]
        if "RETURN COUNT(" in upper and " AS TOTAL" in upper and " AS KEY" in upper:
            return ["key", "total"]
        if "RETURN COUNT(" in upper and " AS TOTAL" in upper:
            return ["total"]
        if "RETURN DISTINCT" in upper:
            return ["value"]
        if " AS VALUE" in upper:
            return ["value"]
        return ["n"]
