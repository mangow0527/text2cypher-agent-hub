from __future__ import annotations

import re
import unittest
import json
import threading
import time

from app.domain.coverage.service import CoverageService
from app.domain.difficulty.service import DifficultyService
from app.domain.generation.service import GenerationService
from app.domain.models import (
    CanonicalSchemaSpec,
    CypherCandidate,
    GenerationLimits,
    JobRecord,
    JobRequest,
    ModelConfig,
    QASample,
    ResultSignature,
    RuntimeMeta,
    ValidationConfig,
)
from app.domain.validation.service import ValidationService
from app.orchestrator.service import Orchestrator


class CoverageLLMGateway:
    def __init__(self, cypher: str) -> None:
        self.cypher = cypher
        self.calls: list[str] = []
        self.last_requests: list[dict] = []
        self.request_batches: list[list[dict]] = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def generate_text(self, prompt_name, model_config, **kwargs):
        self.calls.append(prompt_name)
        self.last_requests = json.loads(kwargs.get("requests_json", "[]"))
        self.request_batches.append(self.last_requests)
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.05)
        with self.lock:
            self.active -= 1
        return json.dumps(
            {
                "items": [
                    {
                        "request_id": item["request_id"],
                        "candidates": [
                            {"mode": "llm_direct", "cypher": self.cypher},
                        ],
                    }
                    for item in self.last_requests
                ]
            },
            ensure_ascii=False,
        )


class RejectDifficultyRoundtrip:
    def __init__(self, rejected_difficulty: str) -> None:
        self.rejected_difficulty = rejected_difficulty

    def check(self, sample: QASample, model_config: ModelConfig):
        return sample.difficulty != self.rejected_difficulty, sample.question_variants_zh, sample.question_variant_styles


class EmptyResultGraphExecutor:
    def execute(self, cypher, config):
        return RuntimeMeta(latency_ms=1, planner="fake-graph"), ResultSignature(), True


class CoverageGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = CanonicalSchemaSpec(
            node_types=["NetworkElement", "Port", "Tunnel", "Service"],
            edge_types=["HAS_PORT", "FIBER_SRC", "SERVES"],
            node_properties={
                "NetworkElement": {"id": "string", "vendor": "string", "created_at": "int"},
                "Port": {"id": "string", "admin_status": "string"},
                "Tunnel": {"id": "string", "latency": "int"},
                "Service": {"id": "string", "priority": "int"},
            },
            edge_constraints={
                "HAS_PORT": [["NetworkElement", "Port"]],
                "FIBER_SRC": [["Port", "Tunnel"]],
                "SERVES": [["Tunnel", "Service"]],
            },
            value_catalog={
                "NetworkElement.id": ["ne-1"],
                "NetworkElement.vendor": ["VendorA"],
                "NetworkElement.created_at": [20240101],
                "Port.admin_status": ["UP"],
                "Tunnel.latency": [12],
                "Service.priority": [3],
            },
        )

    def test_coverage_specs_cover_all_l1_to_l8_levels(self) -> None:
        specs = CoverageService().build_specs(
            schema=self.schema,
            limits=GenerationLimits(max_skeletons=16, max_candidates_per_skeleton=1, max_variants_per_question=1),
            target_qa_count=8,
            diversity_key="job_coverage",
        )

        self.assertEqual({spec.target_difficulty for spec in specs[:8]}, {f"L{level}" for level in range(1, 9)})
        self.assertTrue(all(spec.intent for spec in specs))
        self.assertTrue(all(spec.answer_type for spec in specs))
        self.assertTrue(all(spec.bindings.get("node") for spec in specs))

    def test_coverage_specs_follow_requested_difficulty_targets(self) -> None:
        specs = CoverageService().build_specs(
            schema=self.schema,
            limits=GenerationLimits(max_skeletons=16, max_candidates_per_skeleton=1, max_variants_per_question=1),
            target_qa_count=8,
            difficulty_targets={"L1": 2, "L4": 3, "L8": 1},
            diversity_key="job_targeted_difficulty",
        )

        self.assertEqual([spec.target_difficulty for spec in specs], ["L1", "L1", "L4", "L4", "L4", "L8"])
        self.assertEqual(len({spec.template_id for spec in specs if spec.target_difficulty == "L4"}), 2)

    def test_output_config_uses_difficulty_target_sum_as_target_count(self) -> None:
        from app.domain.models import JobRequest

        request = JobRequest(output_config={"target_qa_count": 10, "difficulty_targets": {"L2": 2, "L7": 3, "L8": 0}})

        self.assertEqual(request.output_config.target_qa_count, 5)
        self.assertEqual(request.output_config.difficulty_targets, {"L2": 2, "L7": 3})

    def test_release_selection_follows_requested_difficulty_targets(self) -> None:
        samples = [
            self._qa_sample("qa_l1_a", "L1"),
            self._qa_sample("qa_l1_b", "L1"),
            self._qa_sample("qa_l2_a", "L2"),
            self._qa_sample("qa_l8_a", "L8"),
            self._qa_sample("qa_l8_b", "L8"),
            self._qa_sample("qa_l4_extra", "L4"),
        ]

        selected, meta = Orchestrator()._select_release_batch(
            samples,
            {"questions": set(), "cyphers": set()},
            target_qa_count=5,
            difficulty_targets={"L1": 2, "L2": 1, "L8": 2},
        )

        self.assertEqual(meta["selected_count"], 5)
        self.assertEqual(meta["difficulty_shortfalls"], {})
        self.assertEqual(
            {level: sum(1 for sample in selected if sample.difficulty == level) for level in {"L1", "L2", "L8"}},
            {"L1": 2, "L2": 1, "L8": 2},
        )

    def test_release_selection_reports_difficulty_shortfalls(self) -> None:
        selected, meta = Orchestrator()._select_release_batch(
            [self._qa_sample("qa_l1_a", "L1"), self._qa_sample("qa_l8_a", "L8")],
            {"questions": set(), "cyphers": set()},
            target_qa_count=3,
            difficulty_targets={"L1": 2, "L8": 1},
        )

        self.assertEqual(len(selected), 2)
        self.assertEqual(meta["difficulty_shortfalls"], {"L1": 1})

    def test_release_selection_rejects_empty_answers_even_with_difficulty_targets(self) -> None:
        l1 = self._qa_sample("qa_l1_a", "L1")
        l2 = self._qa_sample("qa_l2_empty", "L2")
        l2.answer = []
        l2.result_signature.row_count = 0
        l2.result_signature.result_preview = []

        selected, meta = Orchestrator()._select_release_batch(
            [l1, l2],
            {"questions": set(), "cyphers": set()},
            target_qa_count=2,
            difficulty_targets={"L1": 1, "L2": 1},
        )

        self.assertEqual(meta["difficulty_shortfalls"], {"L2": 1})
        self.assertEqual([sample.difficulty for sample in selected], ["L1"])

    def test_validation_rejects_empty_runtime_results_by_default(self) -> None:
        candidate = CypherCandidate(
            skeleton_id="empty_result",
            cypher="MATCH (n:NetworkElement) RETURN n",
            query_types=["LOOKUP"],
            structure_family="lookup_node_return",
            generation_mode="template",
            bound_schema_items={"nodes": ["NetworkElement"], "edges": []},
            difficulty="L1",
        )

        sample = ValidationService(graph_executor=EmptyResultGraphExecutor()).validate(
            candidate,
            self.schema,
            ValidationConfig(),
            tugraph_config=None,
        )

        self.assertTrue(sample.validation.runtime)
        self.assertFalse(sample.validation.result_sanity)

    def test_targeted_roundtrip_keeps_structurally_valid_missing_difficulty(self) -> None:
        l1 = self._qa_sample("qa_l1", "L1")
        l8 = self._qa_sample("qa_l8", "L8")
        l8.question_canonical_zh = "统计每个服务关联的隧道源网元数量，返回前3个服务。"
        l8.cypher = (
            "MATCH (a:Service)-[:SERVICE_USES_TUNNEL]->(:Tunnel)-[:TUNNEL_SRC]->(c:NetworkElement) "
            "WITH a, count(c) AS first_total "
            "MATCH (a)-[:SERVICE_USES_TUNNEL]->(:Tunnel)-[:TUNNEL_SRC]->(c2:NetworkElement) "
            "RETURN a.name AS key, first_total, count(c2) AS total ORDER BY first_total ASC LIMIT 3"
        )
        for sample in (l1, l8):
            sample.provenance["canonical_pass"] = "true"
            sample.provenance["canonical_checks"] = json.dumps({"return_target": True, "topk_limit": True}, ensure_ascii=False)
            sample.provenance["approved_styles"] = json.dumps(sample.question_variant_styles, ensure_ascii=False)
        job = JobRecord(
            request=JobRequest(
                output_config={"target_qa_count": 2, "difficulty_targets": {"L1": 1, "L8": 1}},
                validation_config={"roundtrip_required": True},
            )
        )
        orchestrator = Orchestrator(roundtrip_service=RejectDifficultyRoundtrip("L8"))

        selected = orchestrator._apply_roundtrip(job, [l1, l8], ModelConfig(), "online")

        self.assertEqual({sample.difficulty for sample in selected}, {"L1", "L8"})
        self.assertFalse(next(sample for sample in selected if sample.difficulty == "L8").validation.roundtrip_check)

    def _qa_sample(self, sample_id: str, difficulty: str) -> QASample:
        return QASample.model_validate(
            {
                "id": sample_id,
                "question_canonical_zh": f"{difficulty} 样例问题 {sample_id}？",
                "question_variants_zh": [f"{difficulty} 样例问题 {sample_id}？"],
                "question_variant_styles": ["natural_short"],
                "cypher": f"MATCH (n) RETURN '{sample_id}' AS id",
                "cypher_normalized": f"match (n) return '{sample_id}' as id",
                "query_types": ["LOOKUP"],
                "difficulty": difficulty,
                "answer": [{"id": sample_id}],
                "validation": {
                    "syntax": True,
                    "schema": True,
                    "type_value": True,
                    "query_type_valid": True,
                    "family_valid": True,
                    "runtime": True,
                    "result_sanity": True,
                    "difficulty_valid": True,
                    "roundtrip_check": True,
                },
                "result_signature": {"row_count": 1, "result_preview": [{"id": sample_id}]},
                "split": "silver",
                "provenance": {"structure_family": "lookup_node_return", "generation_mode": "template"},
            }
        )

    def _cypher_candidate(
        self,
        skeleton_id: str,
        difficulty: str,
        cypher: str,
        generation_mode: str = "template",
    ) -> CypherCandidate:
        return CypherCandidate(
            skeleton_id=skeleton_id,
            cypher=cypher,
            query_types=["LOOKUP"],
            structure_family="lookup_node_return",
            generation_mode=generation_mode,
            difficulty=difficulty,
        )

    def test_generation_builds_candidates_directly_from_coverage_specs(self) -> None:
        service = GenerationService()
        specs = CoverageService().build_specs(
            schema=self.schema,
            limits=GenerationLimits(max_skeletons=16, max_candidates_per_skeleton=1, max_variants_per_question=1),
            target_qa_count=8,
            diversity_key="job_generation",
        )

        candidates = service.instantiate_candidates_from_specs(self.schema, specs, GenerationLimits(max_candidates_per_skeleton=1))

        self.assertEqual(len(candidates), 8)
        self.assertEqual({candidate.difficulty for candidate in candidates}, {f"L{level}" for level in range(1, 9)})
        self.assertTrue(all(candidate.query_plan == {} for candidate in candidates))
        self.assertTrue(all(candidate.bound_schema_items.get("nodes") for candidate in candidates))
        self.assertTrue(any("WITH" in candidate.cypher.upper() for candidate in candidates if candidate.difficulty == "L8"))

    def test_coverage_merge_keeps_template_fallback_for_each_requested_difficulty(self) -> None:
        service = GenerationService()
        template_candidates = [
            self._cypher_candidate("tmpl_l1", "L1", "MATCH (n:NetworkElement) RETURN n"),
            self._cypher_candidate("tmpl_l2", "L2", "MATCH (n:NetworkElement) WHERE n.vendor = 'VendorA' RETURN n"),
            self._cypher_candidate(
                "tmpl_l7",
                "L7",
                "MATCH (a:NetworkElement)-[:HAS_PORT]->(:Port)-[:FIBER_SRC]->(:Tunnel)-[:SERVES]->(d:Service) RETURN d",
            ),
            self._cypher_candidate(
                "tmpl_l8",
                "L8",
                "MATCH (a:NetworkElement)-[:HAS_PORT]->(b:Port) WITH a, count(b) AS first_total "
                "MATCH (a)-[:HAS_PORT]->(:Port)-[:FIBER_SRC]->(c:Tunnel) RETURN a.id AS key, first_total, count(c) AS total",
            ),
        ]
        llm_candidates = [
            self._cypher_candidate(f"llm_l1_{index}", "L1", f"MATCH (n:NetworkElement) RETURN n LIMIT {index + 1}", "llm_direct")
            for index in range(2)
        ] + [
            self._cypher_candidate(
                f"llm_l2_{index}",
                "L2",
                f"MATCH (n:NetworkElement) WHERE n.vendor = 'VendorA' RETURN n LIMIT {index + 1}",
                "llm_direct",
            )
            for index in range(2)
        ]

        merged = service._merge_llm_with_template_fallback(llm_candidates, template_candidates, max_count=4)

        self.assertEqual({candidate.difficulty for candidate in merged}, {"L1", "L2", "L7", "L8"})

    def test_sanitize_rewrites_order_by_to_return_alias(self) -> None:
        cypher = (
            "MATCH (a:Service)-[:SERVICE_USES_TUNNEL]->(:Tunnel)-[:TUNNEL_SRC]->(c:NetworkElement) "
            "WITH a, count(c) AS total_elements "
            "MATCH (a)-[:SERVICE_USES_TUNNEL]->(:Tunnel)-[:TUNNEL_SRC]->(c2:NetworkElement) "
            "RETURN a.name AS key, total_elements AS first_total, count(c2) AS total "
            "ORDER BY total_elements ASC LIMIT 3"
        )

        sanitized = GenerationService()._sanitize_cypher(cypher)

        self.assertIn("ORDER BY first_total ASC", sanitized)
        self.assertNotIn("ORDER BY total_elements", sanitized)

    def test_coverage_candidates_classify_to_declared_difficulty(self) -> None:
        specs = CoverageService().build_specs(
            schema=self.schema,
            limits=GenerationLimits(max_skeletons=16, max_candidates_per_skeleton=1, max_variants_per_question=1),
            target_qa_count=16,
            diversity_key="job_generation",
        )

        candidates = GenerationService().instantiate_candidates_from_specs(
            self.schema,
            specs,
            GenerationLimits(max_skeletons=16, max_candidates_per_skeleton=1, max_variants_per_question=1),
        )

        classifier = DifficultyService()
        for candidate in candidates:
            self.assertEqual(
                candidate.difficulty,
                classifier.classify(candidate.cypher),
                msg=f"{candidate.difficulty} candidate classified incorrectly: {candidate.cypher}",
            )

    def test_generated_cypher_edges_follow_declared_schema_direction(self) -> None:
        specs = CoverageService().build_specs(
            schema=self.schema,
            limits=GenerationLimits(max_skeletons=16, max_candidates_per_skeleton=1, max_variants_per_question=1),
            target_qa_count=8,
            diversity_key="job_generation",
        )

        candidates = GenerationService().instantiate_candidates_from_specs(
            self.schema,
            specs,
            GenerationLimits(max_skeletons=16, max_candidates_per_skeleton=1, max_variants_per_question=1),
        )

        constraints = {
            edge: {tuple(pair) for pair in pairs}
            for edge, pairs in self.schema.edge_constraints.items()
        }
        for candidate in candidates:
            for edge, source, target in self._actual_edge_triplets(candidate.cypher):
                self.assertIn(
                    (source, target),
                    constraints[edge],
                    msg=f"{candidate.difficulty} generated invalid edge direction {source}-[:{edge}]->{target}: {candidate.cypher}",
                )

    def test_generation_uses_template_variants_when_target_exceeds_level_count(self) -> None:
        specs = CoverageService().build_specs(
            schema=self.schema,
            limits=GenerationLimits(max_skeletons=16, max_candidates_per_skeleton=1, max_variants_per_question=1),
            target_qa_count=16,
            diversity_key="job_generation",
        )

        candidates = GenerationService().instantiate_candidates_from_specs(
            self.schema,
            specs,
            GenerationLimits(max_skeletons=16, max_candidates_per_skeleton=1, max_variants_per_question=1),
        )

        by_level: dict[str, set[str]] = {}
        for candidate in candidates:
            by_level.setdefault(candidate.difficulty, set()).add(candidate.cypher)

        self.assertEqual(len(candidates), 16)
        self.assertEqual(set(by_level), {f"L{level}" for level in range(1, 9)})
        self.assertTrue(all(len(cyphers) >= 2 for cyphers in by_level.values()))

    def test_coverage_templates_do_not_add_blanket_limits(self) -> None:
        specs = CoverageService().build_specs(
            schema=self.schema,
            limits=GenerationLimits(max_skeletons=16, max_candidates_per_skeleton=1, max_variants_per_question=1),
            target_qa_count=16,
            diversity_key="job_generation",
        )

        candidates = GenerationService().instantiate_candidates_from_specs(
            self.schema,
            specs,
            GenerationLimits(max_skeletons=16, max_candidates_per_skeleton=1, max_variants_per_question=1),
        )

        non_topk_levels = {"L1", "L2", "L3", "L4", "L5", "L7"}
        for candidate in candidates:
            if candidate.difficulty in non_topk_levels:
                self.assertNotRegex(candidate.cypher, r"\bLIMIT\b", msg=candidate.cypher)

        limited_candidates = [candidate for candidate in candidates if re.search(r"\bLIMIT\s+\d+\b", candidate.cypher)]
        self.assertTrue(limited_candidates)
        self.assertLess(len(limited_candidates), len(candidates))
        self.assertTrue(all(candidate.difficulty in {"L6", "L8"} for candidate in limited_candidates))
        self.assertGreater(
            len({re.search(r"\bLIMIT\s+(\d+)\b", candidate.cypher).group(1) for candidate in limited_candidates}),
            1,
        )
        self.assertGreater(
            len({re.search(r"\bORDER BY\b.+\b(ASC|DESC)\b", candidate.cypher).group(1) for candidate in limited_candidates}),
            1,
        )

    def test_coverage_generation_uses_llm_candidates_when_model_config_is_present(self) -> None:
        specs = CoverageService().build_specs(
            schema=self.schema,
            limits=GenerationLimits(max_skeletons=1, max_candidates_per_skeleton=1, max_variants_per_question=1),
            target_qa_count=1,
            diversity_key="job_generation",
        )
        gateway = CoverageLLMGateway("MATCH (n:NetworkElement) RETURN n.id AS value")

        candidates = GenerationService(model_gateway=gateway).instantiate_candidates_from_specs(
            self.schema,
            specs,
            GenerationLimits(max_skeletons=1, max_candidates_per_skeleton=1, max_variants_per_question=1),
            model_config=ModelConfig(),
        )

        self.assertEqual(gateway.calls, ["cypher_candidate_batch"])
        self.assertIn("few_shots", gateway.last_requests[0])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].generation_mode, "llm_direct")
        self.assertEqual(candidates[0].cypher, "MATCH (n:NetworkElement) RETURN n.id AS value")

    def test_coverage_generation_splits_large_llm_batches_into_parallel_requests(self) -> None:
        specs = CoverageService().build_specs(
            schema=self.schema,
            limits=GenerationLimits(max_skeletons=7, max_candidates_per_skeleton=1, max_variants_per_question=1),
            target_qa_count=7,
            diversity_key="job_generation_parallel",
        )
        gateway = CoverageLLMGateway("MATCH (n:NetworkElement) RETURN n.id AS value")

        candidates = GenerationService(model_gateway=gateway).instantiate_candidates_from_specs(
            self.schema,
            specs,
            GenerationLimits(max_skeletons=7, max_candidates_per_skeleton=1, max_variants_per_question=1),
            model_config=ModelConfig(),
        )

        self.assertEqual(gateway.calls, ["cypher_candidate_batch", "cypher_candidate_batch", "cypher_candidate_batch"])
        self.assertCountEqual([len(batch) for batch in gateway.request_batches], [3, 3, 1])
        self.assertGreater(gateway.max_active, 1)
        self.assertEqual(len(candidates), 7)

    def test_coverage_generation_rejects_invalid_llm_candidates_and_falls_back_to_template(self) -> None:
        specs = CoverageService().build_specs(
            schema=self.schema,
            limits=GenerationLimits(max_skeletons=1, max_candidates_per_skeleton=1, max_variants_per_question=1),
            target_qa_count=1,
            diversity_key="job_generation",
        )
        gateway = CoverageLLMGateway("MATCH (n:MissingLabel) RETURN n")

        candidates = GenerationService(model_gateway=gateway).instantiate_candidates_from_specs(
            self.schema,
            specs,
            GenerationLimits(max_skeletons=1, max_candidates_per_skeleton=1, max_variants_per_question=1),
            model_config=ModelConfig(),
        )

        self.assertEqual(gateway.calls, ["cypher_candidate_batch"])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].generation_mode, "template")
        self.assertNotIn("MissingLabel", candidates[0].cypher)

    def test_coverage_bindings_follow_declared_edge_direction(self) -> None:
        specs = CoverageService().build_specs(
            schema=self.schema,
            limits=GenerationLimits(max_skeletons=16, max_candidates_per_skeleton=1, max_variants_per_question=1),
            target_qa_count=8,
            diversity_key="job_generation",
        )

        constraints = {
            edge: {tuple(pair) for pair in pairs}
            for edge, pairs in self.schema.edge_constraints.items()
        }
        for spec in specs:
            nodes = [spec.bindings.get(name) for name in ("node", "target", "target2", "target3") if spec.bindings.get(name)]
            edges = [spec.bindings.get(name) for name in ("edge", "edge2", "edge3") if spec.bindings.get(name)]
            for index, edge in enumerate(edges):
                if index + 1 >= len(nodes):
                    continue
                self.assertIn((nodes[index], nodes[index + 1]), constraints[edge])

    def test_validation_rejects_edge_direction_mismatch(self) -> None:
        candidate = CypherCandidate(
            skeleton_id="wrong_direction",
            cypher=(
                "MATCH (a:Tunnel)-[:SERVES]->(:Service)-[:HAS_PORT]->(c:Port) "
                "WHERE a.id = 'sample' RETURN c.id AS key, count(*) AS total ORDER BY total DESC LIMIT 5"
            ),
            query_types=["MULTI_HOP"],
            structure_family="two_hop_filtered",
            generation_mode="template",
            bound_schema_items={
                "nodes": ["Tunnel", "Service", "Port"],
                "edges": ["SERVES", "HAS_PORT"],
                "properties": ["id"],
            },
            bound_values={"id": "sample"},
            difficulty="L6",
        )

        sample = ValidationService().validate(
            candidate,
            self.schema,
            ValidationConfig(require_runtime_validation=False),
            tugraph_config=None,
        )

        self.assertFalse(sample.validation.schema_valid)

    def _actual_edge_triplets(self, cypher: str) -> list[tuple[str, str, str]]:
        output: list[tuple[str, str, str]] = []
        variable_labels = {
            match.group("var"): match.group("label")
            for match in re.finditer(r"\(\s*(?P<var>[a-zA-Z]\w*)\s*:\s*(?P<label>\w+)\s*\)", cypher)
        }
        pattern = re.compile(
            r"\(\s*(?:(?P<src_var>[a-zA-Z]\w*)\s*)?(?::\s*(?P<src_label>\w+)\s*)?\)"
            r"\s*-\s*\[:(?P<edge>\w+)\]\s*->\s*"
            r"\(\s*(?:(?P<dst_var>[a-zA-Z]\w*)\s*)?(?::\s*(?P<dst_label>\w+)\s*)?\)"
        )
        for match in pattern.finditer(cypher):
            source = match.group("src_label") or variable_labels.get(match.group("src_var") or "")
            target = match.group("dst_label") or variable_labels.get(match.group("dst_var") or "")
            if source and target:
                output.append((match.group("edge"), source, target))
        return output
