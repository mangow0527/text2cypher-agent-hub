from __future__ import annotations

import re
import unittest
import json

from app.domain.coverage.service import CoverageService
from app.domain.difficulty.service import DifficultyService
from app.domain.generation.service import GenerationService
from app.domain.models import CanonicalSchemaSpec, CypherCandidate, GenerationLimits, ModelConfig, ValidationConfig
from app.domain.validation.service import ValidationService


class CoverageLLMGateway:
    def __init__(self, cypher: str) -> None:
        self.cypher = cypher
        self.calls: list[str] = []
        self.last_requests: list[dict] = []

    def generate_text(self, prompt_name, model_config, **kwargs):
        self.calls.append(prompt_name)
        self.last_requests = json.loads(kwargs.get("requests_json", "[]"))
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
