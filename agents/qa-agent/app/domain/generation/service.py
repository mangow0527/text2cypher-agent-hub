from __future__ import annotations

import json
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from app.domain.difficulty.service import DifficultyService
from app.domain.generation.registry import QUERY_TYPE_REGISTRY
from app.domain.models import CanonicalSchemaSpec, CoverageSpec, CypherCandidate, CypherSkeleton, GenerationLimits, ModelConfig, QueryPlan
from app.errors import AppError
from app.integrations.openai.model_gateway import ModelGateway


FAMILY_TEMPLATES: dict[str, str] = {
    "lookup_node_return": "MATCH (n:{node}) RETURN n",
    "lookup_property_projection": "MATCH (n:{node}) RETURN n.{property} AS value",
    "lookup_entity_detail": "MATCH (n:{node}) WHERE n.{property} = {value} RETURN n",
    "filter_single_condition": "MATCH (n:{node}) WHERE n.{property} = {value} RETURN n",
    "filter_boolean_combo": "MATCH (n:{node}) WHERE n.{property} = {value} OR n.{property} STARTS WITH {value} RETURN n",
    "filter_range_condition": "MATCH (n:{node}) WHERE n.{property} >= {value} RETURN n",
    "sort_single_metric": "MATCH (n:{node}) RETURN n.{property} AS value ORDER BY value {order_direction}",
    "topk_entities": "MATCH (n:{node}) RETURN n ORDER BY n.{property} {order_direction} LIMIT {limit}",
    "sorted_filtered_projection": "MATCH (n:{node}) WHERE n.{property} = '{value}' RETURN n.{property} AS value ORDER BY value {order_direction} LIMIT {limit}",
    "aggregate_global_count": "MATCH (n:{node}) RETURN count(n) AS total",
    "aggregate_filtered_count": "MATCH (n:{node}) WHERE n.{property} = {value} RETURN count(n) AS total",
    "aggregate_scalar_metric": "MATCH (n:{node}) RETURN count(n) AS total",
    "group_count": "MATCH (n:{node}) RETURN n.{property} AS group_key, count(*) AS total",
    "group_ranked_count": "MATCH (n:{node}) RETURN n.{property} AS group_key, count(*) AS total ORDER BY total {order_direction} LIMIT {limit}",
    "group_filtered_aggregate": "MATCH (n:{node}) WHERE n.{property} = {value} RETURN n.{property} AS group_key, count(*) AS total",
    "two_hop_return": "MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(c:{target2}) RETURN c",
    "two_hop_filtered": "MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(c:{target2}) WHERE a.{property} = {value} RETURN c",
    "multi_hop_projection": "MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(:{target2}) RETURN a.{property} AS key",
    "attribute_comparison": "MATCH (a:{node}), (b:{node}) WHERE a.{property} <> b.{property} RETURN a.{property} AS left_value, b.{property} AS right_value",
    "aggregate_comparison": "MATCH (a:{node})-[:{edge}]->(b:{target}) WITH a, count(b) AS total WHERE total >= {count_threshold} RETURN a.{property} AS key, total ORDER BY total {order_direction} LIMIT {limit}",
    "rank_position_comparison": "MATCH (n:{node}) RETURN n ORDER BY n.{property} {order_direction} LIMIT {limit}",
    "time_range_filter": "MATCH (n:{node}) WHERE n.{property} >= {value} RETURN n",
    "recent_or_earliest": "MATCH (n:{node}) RETURN n ORDER BY n.{property} {order_direction} LIMIT 1",
    "temporal_ordering": "MATCH (n:{node}) RETURN n.{property} AS event_time ORDER BY event_time {order_direction}",
    "path_existence": "MATCH p=({node_l}:{node})-[:{edge}]->({target_l}:{target}) RETURN p",
    "variable_length_path": "MATCH p=({node_l}:{node})-[:{edge}*{path_min_hops}..{path_max_hops}]->({target_l}:{target}) RETURN p",
    "path_constrained_target": "MATCH p=({node_l}:{node})-[:{edge}]->(:{target})-[:{edge2}]->({target2_l}:{target2}) WHERE {target2_l}.{property3} = {value2} RETURN p",
    "distinct_projection": "MATCH (n:{node}) RETURN DISTINCT n.{property} AS value",
    "set_like_union_projection": "MATCH (a:{node}) RETURN a.{property} AS value UNION MATCH (b:{target}) RETURN b.{property2} AS value",
    "membership_intersection_style": "MATCH (a:{node})-[:{edge}]->(b:{target}) WHERE a.{property} = {value} AND b.{property2} IS NOT NULL RETURN DISTINCT a",
    "with_stage_filter": "MATCH (n:{node}) WITH n MATCH (n)-[:{edge}]->(b:{target}) WHERE n.{property} = {value} RETURN n, b",
    "with_stage_aggregate": "MATCH (a:{node})-[:{edge}]->(b:{target}) WITH a, count(b) AS cnt MATCH (a)-[:{edge2}]->(c:{target2}) RETURN a.{property} AS key, count(c) AS total",
    "two_stage_refine": "MATCH (n:{node}) WITH n MATCH (n)-[:{edge}]->(b:{target}) RETURN n.{property} AS key, b",
    "temporal_aggregate_hybrid": "MATCH (n:{node}) WHERE n.{property} >= {value} RETURN count(n) AS total",
    "path_aggregate_hybrid": "MATCH p=({node_l}:{node})-[:{edge}*{path_min_hops}..{path_max_hops}]->({target_l}:{target}) RETURN count(p) AS total",
    "comparison_subquery_hybrid": "MATCH (a:{node})-[:{edge}]->(b:{target}) WITH a, count(b) AS cnt MATCH (a)-[:{edge2}]->(c:{target2}) RETURN a.{property} AS key, count(c) AS total",
}


class GenerationService:
    def __init__(self, model_gateway: ModelGateway | None = None) -> None:
        self.model_gateway = model_gateway or ModelGateway()
        self.difficulty_service = DifficultyService()

    def build_skeletons(
        self,
        schema: CanonicalSchemaSpec,
        limits: GenerationLimits,
        diversity_key: str | None = None,
        query_plans: List[QueryPlan] | None = None,
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
        if query_plans:
            family_lookup = {
                (skeleton.query_types[0], skeleton.structure_family, skeleton.difficulty_floor): skeleton
                for skeleton in full_pool
            }
            selected: List[CypherSkeleton] = []
            for index, plan in enumerate(query_plans):
                skeleton = family_lookup.get((plan.query_type, plan.structure_family, plan.difficulty))
                if skeleton is None:
                    continue
                selected.append(
                    skeleton.model_copy(
                        update={
                            "skeleton_id": f"{skeleton.skeleton_id}_plan_{index + 1:02d}",
                        }
                    )
                )
            if selected:
                return selected[: limits.max_skeletons]
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
        query_plans: List[QueryPlan] | None = None,
    ) -> List[CypherCandidate]:
        plan_map = {}
        if query_plans:
            for skeleton, plan in zip(skeletons, query_plans):
                plan_map[skeleton.skeleton_id] = plan
        with ThreadPoolExecutor(max_workers=min(6, max(1, len(skeletons)))) as executor:
            template_nested = list(
                executor.map(
                    lambda skeleton: self._instantiate_template_candidates_for_skeleton(
                        schema,
                        skeleton,
                        limits,
                        plan_map.get(skeleton.skeleton_id),
                    ),
                    skeletons,
                )
            )
        output = [candidate for group in template_nested for candidate in group]
        if not model_config:
            return output

        llm_requests: List[dict] = []
        llm_contexts: Dict[str, dict] = {}
        for skeleton, group in zip(skeletons, template_nested):
            query_plan = plan_map.get(skeleton.skeleton_id)
            if not group or not self._should_use_llm_candidate_generation(skeleton, query_plan):
                continue
            request_payload, context = self._build_llm_request_payload(schema, skeleton, group[0], query_plan)
            llm_requests.append(request_payload)
            llm_contexts[request_payload["request_id"]] = context

        if llm_requests:
            output.extend(self._safe_build_llm_candidates_batch(llm_requests, llm_contexts, model_config))
        return output

    def instantiate_candidates_from_specs(
        self,
        schema: CanonicalSchemaSpec,
        specs: List[CoverageSpec],
        limits: GenerationLimits,
        model_config: ModelConfig | None = None,
    ) -> List[CypherCandidate]:
        template_candidates = [
            self._build_candidate_from_coverage_spec(schema, spec, variant_index=index)
            for index, spec in enumerate(specs)
        ]
        if not model_config:
            return template_candidates[: limits.max_skeletons]

        llm_candidates = self._safe_build_coverage_llm_candidates_batch(schema, specs, template_candidates, model_config)
        return self._merge_llm_with_template_fallback(llm_candidates, template_candidates, limits.max_skeletons)

    def _safe_build_coverage_llm_candidates_batch(
        self,
        schema: CanonicalSchemaSpec,
        specs: List[CoverageSpec],
        template_candidates: List[CypherCandidate],
        model_config: ModelConfig,
    ) -> List[CypherCandidate]:
        try:
            return self._build_coverage_llm_candidates_batch(schema, specs, template_candidates, model_config)
        except AppError as exc:
            if exc.code == "OPENAI_NOT_CONFIGURED":
                return []
            raise

    def _build_coverage_llm_candidates_batch(
        self,
        schema: CanonicalSchemaSpec,
        specs: List[CoverageSpec],
        template_candidates: List[CypherCandidate],
        model_config: ModelConfig,
    ) -> List[CypherCandidate]:
        requests_payload = []
        contexts: dict[str, tuple[CoverageSpec, CypherCandidate, int]] = {}
        for index, (spec, template_candidate) in enumerate(zip(specs, template_candidates)):
            bindings = dict(spec.bindings)
            request_id = spec.spec_id
            requests_payload.append(
                {
                    "request_id": request_id,
                    "schema_summary": self._build_schema_summary(schema, bindings),
                    "query_type": spec.query_type,
                    "structure_family": spec.structure_family,
                    "family_description": f"{spec.intent}; topology={spec.topology}; answer_type={spec.answer_type}",
                    "difficulty": spec.target_difficulty,
                    "template_cypher": template_candidate.cypher,
                    "slot_bindings": bindings,
                    "query_plan": {
                        "coverage_spec": {
                            "intent": spec.intent,
                            "operators": spec.operators,
                            "topology": spec.topology,
                            "answer_type": spec.answer_type,
                            "target_difficulty": spec.target_difficulty,
                            "template_id": spec.template_id,
                        }
                    },
                    "few_shots": self._coverage_few_shots(spec, template_candidate),
                }
            )
            contexts[request_id] = (spec, template_candidate, index)

        batch_config = model_config.model_copy(
            update={"max_output_tokens": max(model_config.max_output_tokens, 1200 * max(1, len(requests_payload)))}
        )
        bundle_text = self.model_gateway.generate_text(
            "cypher_candidate_batch",
            batch_config,
            requests_json=json.dumps(requests_payload, ensure_ascii=False),
        )

        output: list[CypherCandidate] = []
        parsed = self._parse_candidate_batch(bundle_text)
        for request_id, candidates in parsed.items():
            context = contexts.get(request_id)
            if not context:
                continue
            spec, template_candidate, variant_index = context
            for mode, cypher in candidates:
                normalized_mode = mode if mode in {"llm_direct", "llm_refine"} else "llm_direct"
                candidate = self._build_coverage_llm_candidate(
                    schema,
                    spec,
                    template_candidate,
                    cypher,
                    normalized_mode,
                    variant_index,
                )
                if self._coverage_llm_candidate_valid(schema, candidate):
                    output.append(candidate)
        return output

    def _coverage_few_shots(self, spec: CoverageSpec, template_candidate: CypherCandidate) -> list[dict[str, str]]:
        return [
            {
                "template_id": spec.template_id,
                "difficulty": spec.target_difficulty,
                "cypher": template_candidate.cypher,
                "note": "结构示范，不要求机械复写；可改变返回组织、过滤落点或等价结构，但必须保持 schema、难度和结构语义。",
            }
        ]

    def _build_coverage_llm_candidate(
        self,
        schema: CanonicalSchemaSpec,
        spec: CoverageSpec,
        template_candidate: CypherCandidate,
        cypher: str,
        generation_mode: str,
        variant_index: int,
    ) -> CypherCandidate:
        bindings = dict(spec.bindings)
        property_name = bindings.get("property") or self._pick_property(schema, bindings["node"], variant_index)
        property2_name = bindings.get("property2") or self._pick_property(schema, bindings["target"], variant_index)
        property3_name = bindings.get("property3") or self._pick_property(schema, bindings["target2"], variant_index)
        return CypherCandidate(
            skeleton_id=spec.spec_id,
            cypher=self._sanitize_cypher(cypher),
            query_types=[spec.query_type],
            structure_family=spec.structure_family,
            generation_mode=generation_mode,
            bound_schema_items=template_candidate.bound_schema_items,
            bound_values={property_name: bindings.get("value"), property3_name: bindings.get("value2")},
            difficulty=spec.target_difficulty,
            query_plan={
                "coverage_spec": {
                    "intent": spec.intent,
                    "operators": spec.operators,
                    "topology": spec.topology,
                    "answer_type": spec.answer_type,
                    "template_id": spec.template_id,
                }
            },
        )

    def _coverage_llm_candidate_valid(self, schema: CanonicalSchemaSpec, candidate: CypherCandidate) -> bool:
        if not candidate.cypher.strip().upper().startswith("MATCH "):
            return False
        if self.difficulty_service.classify(candidate.cypher) != candidate.difficulty:
            return False
        if not self._cypher_uses_known_schema_items(schema, candidate.cypher):
            return False
        return self._cypher_edges_follow_schema(schema, candidate.cypher)

    def _merge_llm_with_template_fallback(
        self,
        llm_candidates: List[CypherCandidate],
        template_candidates: List[CypherCandidate],
        max_count: int,
    ) -> List[CypherCandidate]:
        output: list[CypherCandidate] = []
        seen: set[str] = set()
        for candidate in [*llm_candidates, *template_candidates]:
            normalized = " ".join(candidate.cypher.lower().split())
            if normalized in seen:
                continue
            output.append(candidate)
            seen.add(normalized)
            if len(output) >= max_count:
                break
        return output

    def _cypher_uses_known_schema_items(self, schema: CanonicalSchemaSpec, cypher: str) -> bool:
        node_set = set(schema.node_types)
        edge_set = set(schema.edge_types)
        labels = set(re.findall(r"\(\s*(?:[a-zA-Z]\w*)?\s*:\s*([A-Za-z_]\w*)", cypher))
        edges = set(re.findall(r"\[:\s*([A-Za-z_]\w*)", cypher))
        if any(label not in node_set for label in labels):
            return False
        if any(edge not in edge_set for edge in edges):
            return False

        variable_labels = {
            match.group("var"): match.group("label")
            for match in re.finditer(r"\(\s*(?P<var>[a-zA-Z]\w*)\s*:\s*(?P<label>[A-Za-z_]\w*)\s*\)", cypher)
        }
        for match in re.finditer(r"\b(?P<var>[a-zA-Z]\w*)\.(?P<prop>[A-Za-z_]\w*)\b", cypher):
            label = variable_labels.get(match.group("var"))
            if not label:
                continue
            if match.group("prop") not in schema.node_properties.get(label, {}):
                return False
        return True

    def _cypher_edges_follow_schema(self, schema: CanonicalSchemaSpec, cypher: str) -> bool:
        constraints = {
            edge: {tuple(pair) for pair in pairs}
            for edge, pairs in schema.edge_constraints.items()
        }
        if not constraints:
            return True

        variable_labels = {
            match.group("var"): match.group("label")
            for match in re.finditer(r"\(\s*(?P<var>[a-zA-Z]\w*)\s*:\s*(?P<label>[A-Za-z_]\w*)\s*\)", cypher)
        }
        pattern = re.compile(
            r"\(\s*(?:(?P<src_var>[a-zA-Z]\w*)\s*)?(?::\s*(?P<src_label>[A-Za-z_]\w*)\s*)?\)"
            r"\s*-\s*\[:(?P<edge>[A-Za-z_]\w*)(?:\*[^\]]*)?\]\s*->\s*"
            r"\(\s*(?:(?P<dst_var>[a-zA-Z]\w*)\s*)?(?::\s*(?P<dst_label>[A-Za-z_]\w*)\s*)?\)"
        )
        for match in pattern.finditer(cypher):
            edge = match.group("edge")
            source = match.group("src_label") or variable_labels.get(match.group("src_var") or "")
            target = match.group("dst_label") or variable_labels.get(match.group("dst_var") or "")
            if source and target and constraints.get(edge) and (source, target) not in constraints[edge]:
                return False
        return True

    def _build_candidate_from_coverage_spec(
        self,
        schema: CanonicalSchemaSpec,
        spec: CoverageSpec,
        variant_index: int = 0,
    ) -> CypherCandidate:
        bindings = dict(spec.bindings)
        cypher = self._cypher_for_coverage_spec(schema, spec, bindings, variant_index)
        property_name = bindings.get("property") or self._pick_property(schema, bindings["node"], variant_index)
        property2_name = bindings.get("property2") or self._pick_property(schema, bindings["target"], variant_index)
        property3_name = bindings.get("property3") or self._pick_property(schema, bindings["target2"], variant_index)
        bound_schema_items = self._coverage_bound_schema_items(spec, bindings, property_name, property2_name, property3_name)
        return CypherCandidate(
            skeleton_id=spec.spec_id,
            cypher=self._sanitize_cypher(cypher),
            query_types=[spec.query_type],
            structure_family=spec.structure_family,
            generation_mode="template",
            bound_schema_items=bound_schema_items,
            bound_values={
                property_name: bindings.get("value"),
                property3_name: bindings.get("value2"),
            },
            difficulty=spec.target_difficulty,
            query_plan={},
        )

    def _cypher_for_coverage_spec(
        self,
        schema: CanonicalSchemaSpec,
        spec: CoverageSpec,
        bindings: Dict[str, str],
        variant_index: int,
    ) -> str:
        node = bindings["node"]
        target = bindings["target"]
        target2 = bindings["target2"]
        target3 = bindings["target3"]
        edge = bindings["edge"]
        edge2 = bindings["edge2"]
        edge3 = bindings["edge3"]
        prop = bindings.get("property") or self._pick_property(schema, node, variant_index)
        prop2 = bindings.get("property2") or self._pick_property(schema, target, variant_index)
        prop3 = bindings.get("property3") or self._pick_property(schema, target2, variant_index)
        value = self._format_literal(bindings.get("value"), self._property_type(schema, node, prop))
        value2 = self._format_literal(bindings.get("value2"), self._property_type(schema, target2, prop3))
        limit_value = int(bindings.get("limit") or [3, 5, 10][variant_index % 3])
        order_direction = str(bindings.get("order_direction") or "DESC").upper()
        if order_direction not in {"ASC", "DESC"}:
            order_direction = "DESC"

        templates = {
            "l1_lookup_node": f"MATCH (n:{node}) RETURN n",
            "l1_project_property": f"MATCH (n:{node}) RETURN n.{prop} AS value",
            "l2_filter_entity": f"MATCH (n:{node}) WHERE n.{prop} = {value} RETURN n",
            "l2_project_filtered_property": f"MATCH (n:{node}) WHERE n.{prop} = {value} RETURN n.{prop} AS value",
            "l3_one_hop": f"MATCH (a:{node})-[:{edge}]->(b:{target}) RETURN b",
            "l3_one_hop_projection": (
                f"MATCH (a:{node})-[:{edge}]->(b:{target}) "
                f"RETURN a.{prop} AS source, b.{prop2} AS target"
            ),
            "l4_count": f"MATCH (n:{node}) RETURN count(n) AS total",
            "l4_count_property": f"MATCH (n:{node}) RETURN count(n.{prop}) AS total",
            "l5_two_hop": f"MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(c:{target2}) RETURN c",
            "l5_two_hop_projection": (
                f"MATCH (a:{node})-[:{edge}]->(b:{target})-[:{edge2}]->(c:{target2}) "
                f"RETURN b.{prop2} AS via, c.{prop3} AS target"
            ),
            "l6_two_hop_filtered_aggregate": (
                f"MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(c:{target2}) "
                f"WHERE a.{prop} = {value} "
                f"RETURN c.{prop3} AS key, count(*) AS total ORDER BY total {order_direction} LIMIT {limit_value}"
            ),
            "l6_two_hop_target_filtered_aggregate": (
                f"MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(c:{target2}) "
                f"WHERE c.{prop3} = {value2} "
                f"RETURN a.{prop} AS key, count(c) AS total ORDER BY total {order_direction} LIMIT {limit_value}"
            ),
            "l7_three_hop": (
                f"MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(:{target2})-[:{edge3}]->(d:{target3}) "
                f"RETURN d"
            ),
            "l7_three_hop_projection": (
                f"MATCH (a:{node})-[:{edge}]->(b:{target})-[:{edge2}]->(c:{target2})-[:{edge3}]->(d:{target3}) "
                f"RETURN a.{prop} AS source, b.{prop2} AS via1, c.{prop3} AS via2, d AS target"
            ),
            "l8_with_nested_aggregation": (
                f"MATCH (a:{node})-[:{edge}]->(b:{target}) "
                f"WITH a, count(b) AS first_total "
                f"MATCH (a)-[:{edge}]->(:{target})-[:{edge2}]->(c:{target2}) "
                f"WHERE c.{prop3} = {value2} "
                f"RETURN a.{prop} AS key, first_total AS first_total, count(c) AS total"
            ),
            "l8_with_path_refine_aggregation": (
                f"MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(c:{target2}) "
                f"WITH a, count(c) AS first_total "
                f"MATCH (a)-[:{edge}]->(:{target})-[:{edge2}]->(c2:{target2}) "
                f"RETURN a.{prop} AS key, first_total AS first_total, count(c2) AS total ORDER BY first_total {order_direction} LIMIT {limit_value}"
            ),
        }
        default_template_by_level = {
            "L1": "l1_lookup_node",
            "L2": "l2_filter_entity",
            "L3": "l3_one_hop",
            "L4": "l4_count",
            "L5": "l5_two_hop",
            "L6": "l6_two_hop_filtered_aggregate",
            "L7": "l7_three_hop",
            "L8": "l8_with_nested_aggregation",
        }
        template_id = spec.template_id if spec.template_id in templates else default_template_by_level[spec.target_difficulty]
        return templates[template_id]

    def _coverage_bound_schema_items(
        self,
        spec: CoverageSpec,
        bindings: Dict[str, str],
        property_name: str,
        property2_name: str,
        property3_name: str,
    ) -> dict[str, list[str]]:
        level_usage = {
            "L1": (["node"], [], [property_name]),
            "L2": (["node"], [], [property_name]),
            "L3": (["node", "target"], ["edge"], [property_name, property2_name]),
            "L4": (["node"], [], [property_name]),
            "L5": (["node", "target", "target2"], ["edge", "edge2"], [property2_name, property3_name]),
            "L6": (["node", "target", "target2"], ["edge", "edge2"], [property_name, property3_name]),
            "L7": (
                ["node", "target", "target2", "target3"],
                ["edge", "edge2", "edge3"],
                [property_name, property2_name, property3_name],
            ),
            "L8": (["node", "target", "target2"], ["edge", "edge2"], [property_name, property3_name]),
        }
        node_keys, edge_keys, properties = level_usage[spec.target_difficulty]
        return {
            "nodes": [bindings[key] for key in node_keys if bindings.get(key)],
            "edges": [bindings[key] for key in edge_keys if bindings.get(key)],
            "properties": [prop for prop in properties if prop],
        }

    def _instantiate_template_candidates_for_skeleton(
        self,
        schema: CanonicalSchemaSpec,
        skeleton: CypherSkeleton,
        limits: GenerationLimits,
        query_plan: QueryPlan | None = None,
    ) -> List[CypherCandidate]:
        candidates: List[CypherCandidate] = []
        bindings = query_plan.bindings if query_plan else self._pick_pattern_bindings(schema, 0)
        template_candidate = self._build_template_candidate(schema, skeleton, bindings, variant_index=0, query_plan=query_plan)
        candidates.append(template_candidate)
        if limits.max_candidates_per_skeleton > 1:
            for variant_index in range(1, limits.max_candidates_per_skeleton):
                variant_bindings = query_plan.bindings if query_plan else self._pick_pattern_bindings(schema, variant_index)
                candidates.append(self._build_template_candidate(schema, skeleton, variant_bindings, variant_index=variant_index, query_plan=query_plan))
        return candidates

    def _should_use_llm_candidate_generation(
        self,
        skeleton: CypherSkeleton,
        query_plan: QueryPlan | None,
    ) -> bool:
        if query_plan is None:
            return True
        difficulty = query_plan.difficulty.upper()
        if difficulty in {"L6", "L7", "L8"}:
            return True
        semantics = query_plan.required_semantics
        if any(
            semantics.get(flag)
            for flag in ("grouping", "temporal", "comparison", "variable_length", "with_stage")
        ):
            return True
        if int(semantics.get("min_hops", 0) or 0) >= 2:
            return True
        complex_query_types = {"MULTI_HOP", "COMPARISON", "TEMPORAL", "PATH", "SUBQUERY", "HYBRID"}
        return query_plan.query_type in complex_query_types

    def _safe_build_llm_candidates(
        self,
        schema: CanonicalSchemaSpec,
        skeleton: CypherSkeleton,
        bindings: Dict[str, str],
        template_candidate: CypherCandidate,
        model_config: ModelConfig,
        query_plan: QueryPlan | None,
    ) -> List[CypherCandidate]:
        try:
            return self._build_llm_candidates(schema, skeleton, bindings, template_candidate, model_config, query_plan)
        except AppError as exc:
            if exc.code == "OPENAI_NOT_CONFIGURED":
                return []
            raise

    def _safe_build_llm_candidates_batch(
        self,
        requests_payload: List[dict],
        contexts: Dict[str, dict],
        model_config: ModelConfig,
    ) -> List[CypherCandidate]:
        try:
            return self._build_llm_candidates_batch(requests_payload, contexts, model_config)
        except AppError as exc:
            if exc.code == "OPENAI_NOT_CONFIGURED":
                return []
            raise

    def _build_llm_request_payload(
        self,
        schema: CanonicalSchemaSpec,
        skeleton: CypherSkeleton,
        template_candidate: CypherCandidate,
        query_plan: QueryPlan | None,
    ) -> tuple[dict, dict]:
        bindings = query_plan.bindings if query_plan else self._pick_pattern_bindings(schema, 0)
        property_name = self._pick_property(schema, bindings["node"], 0)
        property2_name = self._pick_property(schema, bindings["target"], 0)
        property3_name = self._pick_property(schema, bindings["target2"], 0)
        raw_value = bindings.get("value")
        raw_value2 = bindings.get("value2")
        value = raw_value if raw_value is not None else self._pick_raw_value(schema, bindings["node"], property_name, 0)
        value2 = raw_value2 if raw_value2 is not None else self._pick_raw_value(schema, bindings["target2"], property3_name, 0)
        slot_bindings = {
            **bindings,
            "property": property_name,
            "property2": property2_name,
            "property3": property3_name,
            "value": value,
            "value2": value2,
        }
        family_info = self._family_info(skeleton.query_types[0], skeleton.structure_family)
        request_id = skeleton.skeleton_id
        request_payload = {
            "request_id": request_id,
            "schema_summary": self._build_schema_summary(schema, bindings),
            "query_type": skeleton.query_types[0],
            "structure_family": skeleton.structure_family,
            "family_description": family_info.get("description", ""),
            "difficulty": skeleton.difficulty_floor,
            "template_cypher": template_candidate.cypher,
            "slot_bindings": slot_bindings,
            "query_plan": query_plan.model_dump() if query_plan else {},
        }
        context = {
            "skeleton": skeleton,
            "bindings": bindings,
            "property_name": property_name,
            "property2_name": property2_name,
            "property3_name": property3_name,
            "value": value,
            "value2": value2,
            "query_plan": query_plan,
        }
        return request_payload, context

    def _build_llm_candidates_batch(
        self,
        requests_payload: List[dict],
        contexts: Dict[str, dict],
        model_config: ModelConfig,
    ) -> List[CypherCandidate]:
        batch_config = model_config.model_copy(
            update={"max_output_tokens": max(model_config.max_output_tokens, 1200 * max(1, len(requests_payload)))}
        )
        bundle_text = self.model_gateway.generate_text(
            "cypher_candidate_batch",
            batch_config,
            requests_json=json.dumps(requests_payload, ensure_ascii=False),
        )
        output: List[CypherCandidate] = []
        parsed = self._parse_candidate_batch(bundle_text)
        for request_id, candidates in parsed.items():
            context = contexts.get(request_id)
            if not context:
                continue
            for mode, cypher in candidates:
                normalized_mode = mode if mode in {"llm_direct", "llm_refine"} else "llm_direct"
                output.append(
                    self._build_candidate(
                        skeleton=context["skeleton"],
                        cypher=cypher,
                        bindings=context["bindings"],
                        property_name=context["property_name"],
                        property2_name=context["property2_name"],
                        property3_name=context["property3_name"],
                        value=context["value"],
                        value2=context["value2"],
                        generation_mode=normalized_mode,
                        query_plan=context["query_plan"],
                    )
                )
        return output

    def _build_template_candidate(
        self,
        schema: CanonicalSchemaSpec,
        skeleton: CypherSkeleton,
        bindings: Dict[str, str],
        variant_index: int = 0,
        query_plan: QueryPlan | None = None,
    ) -> CypherCandidate:
        node = bindings["node"]
        target = bindings["target"]
        target2 = bindings["target2"]
        property_name = bindings.get("property") or self._pick_property(schema, node, variant_index)
        property2_name = bindings.get("property2") or self._pick_property(schema, target, variant_index)
        property3_name = bindings.get("property3") or self._pick_property(schema, target2, variant_index)
        raw_value = bindings.get("value")
        raw_value2 = bindings.get("value2")
        value = (
            self._format_literal(raw_value, self._property_type(schema, node, property_name))
            if raw_value is not None
            else self._pick_value(schema, node, property_name, variant_index)
        )
        value2 = (
            self._format_literal(raw_value2, self._property_type(schema, target2, property3_name))
            if raw_value2 is not None
            else self._pick_value(schema, target2, property3_name, variant_index)
        )
        format_args = {
            **bindings,
            "property": property_name,
            "property2": property2_name,
            "property3": property3_name,
            "value": value,
            "value2": value2,
        }
        cypher = skeleton.pattern_template.format(**format_args)
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
            query_plan=query_plan,
        )

    def _build_llm_candidates(
        self,
        schema: CanonicalSchemaSpec,
        skeleton: CypherSkeleton,
        bindings: Dict[str, str],
        template_candidate: CypherCandidate,
        model_config: ModelConfig,
        query_plan: QueryPlan | None,
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
            query_plan=json.dumps(query_plan.model_dump(), ensure_ascii=False) if query_plan else "{}",
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
                    query_plan=query_plan,
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
        query_plan: QueryPlan | None,
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
            query_plan=query_plan.model_dump() if query_plan else {},
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

    def _parse_candidate_batch(self, bundle_text: str) -> Dict[str, List[tuple[str, str]]]:
        try:
            payload = json.loads(bundle_text)
            items = payload.get("items", [])
            output: Dict[str, List[tuple[str, str]]] = {}
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    request_id = str(item.get("request_id", "")).strip()
                    candidates = item.get("candidates", [])
                    parsed: List[tuple[str, str]] = []
                    if isinstance(candidates, list):
                        for candidate in candidates:
                            if not isinstance(candidate, dict):
                                continue
                            mode = str(candidate.get("mode", "llm_direct")).strip()
                            cypher = self._sanitize_cypher(str(candidate.get("cypher", "")))
                            if cypher:
                                parsed.append((mode, cypher))
                    if request_id and parsed:
                        output[request_id] = parsed
            if output:
                return output
        except json.JSONDecodeError:
            pass
        return {}

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
        property_type = self._property_type(schema, node, property_name)
        return self._format_literal(self._pick_raw_value(schema, node, property_name, variant_index), property_type)

    def _pick_raw_value(self, schema: CanonicalSchemaSpec, node: str, property_name: str, variant_index: int = 0):
        key = f"{node}.{property_name}"
        values = schema.value_catalog.get(key, [])
        if values:
            return values[variant_index % len(values)]
        lower = self._property_type(schema, node, property_name).lower()
        if any(token in lower for token in ("int", "long", "double", "float", "number", "decimal")):
            return 1
        if "bool" in lower:
            return True
        return "sample"

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
            "limit": [3, 5, 10][variant_index % 3],
            "order_direction": "DESC" if variant_index % 2 == 0 else "ASC",
            "path_min_hops": 1,
            "path_max_hops": 2 + (variant_index % 3),
            "count_threshold": 2 + (variant_index % 3),
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
