from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from app.domain.generation.service import GenerationService
from app.domain.models import CypherCandidate, JobRequest, ResultSignature, RuntimeMeta, ValidatedSample, ValidationResult
from app.domain.questioning.service import QUESTION_VARIANT_STYLES, QuestionService, build_result_summary, is_natural_language_question
from app.domain.roundtrip.service import RoundtripService
from app.domain.schema.compatibility_service import SchemaCompatibilityService
from app.domain.validation.service import ValidationService
from app.integrations.openai.model_gateway import ModelGateway
from app.integrations.qa_dispatcher import QADispatcher
from app.orchestrator.service import Orchestrator
from app.storage.artifact_store import ArtifactStore
from app.storage.job_store import JobStore


class FakeModelGateway(ModelGateway):
    def __init__(self) -> None:
        self.calls = []

    def generate_text(self, prompt_name, model_config, **kwargs):
        self.calls.append(prompt_name)
        if prompt_name == "cypher_candidate_bundle":
            template = kwargs.get("template_cypher", "")
            return json.dumps(
                {
                    "candidates": [
                        {
                            "mode": "llm_direct",
                            "cypher": "MATCH (n:Person) RETURN n.name AS value LIMIT 5",
                        },
                        {
                            "mode": "llm_refine",
                            "cypher": template or "MATCH (n:Person) RETURN n LIMIT 5",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        if prompt_name == "cypher_canonical_question":
            cypher = kwargs.get("cypher", "")
            return "网络中前5个设备有哪些？" if "LIMIT 5" in cypher.upper() else "网络中有哪些设备？"
        if prompt_name == "question_variants":
            cypher = kwargs.get("cypher", "")
            if "LIMIT 5" in cypher.upper():
                return "网络中前5个设备有哪些？\n请列出网络中的前5个设备。\n帮我找出前5个网络设备。"
            return "网络中有哪些设备？\n请列出网络中的设备。\n帮我找出所有网络设备。"
        if prompt_name == "question_bundle":
            cypher = kwargs.get("cypher", "")
            if "LIMIT 5" in cypher.upper():
                return json.dumps(
                    {
                        "canonical_question": "网络中前5个设备有哪些？",
                        "variants": [
                            {"style": "natural_short", "question": "列出网络里的前5个设备"},
                            {"style": "spoken_query", "question": "网络里前5个设备都有啥？"},
                            {"style": "business_term", "question": "查询前5个网络设备"},
                            {"style": "ellipsis_query", "question": "前5个网络设备有哪些？"},
                            {"style": "task_oriented", "question": "帮我把网络中的前5个设备找出来"},
                        ],
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "canonical_question": "网络中有哪些设备？",
                    "variants": [
                        {"style": "natural_short", "question": "列出网络里的设备"},
                        {"style": "spoken_query", "question": "网络里都有哪些设备啊？"},
                        {"style": "business_term", "question": "查询网络设备清单"},
                        {"style": "ellipsis_query", "question": "网络设备有哪些？"},
                        {"style": "task_oriented", "question": "帮我把网络中的设备找出来"},
                    ],
                },
                ensure_ascii=False,
            )
        if prompt_name == "question_bundle_consistency":
            return json.dumps(
                {
                    "canonical_pass": True,
                    "approved_styles": QUESTION_VARIANT_STYLES,
                },
                ensure_ascii=False,
            )
        if prompt_name == "question_cypher_consistency":
            return "PASS"
        if prompt_name == "roundtrip_text2cypher":
            return kwargs.get("target_cypher", "")
        return "PASS"


class InspectingRoundtripGateway(FakeModelGateway):
    def __init__(self) -> None:
        super().__init__()
        self.last_kwargs = {}

    def generate_text(self, prompt_name, model_config, **kwargs):
        if prompt_name == "question_bundle_consistency":
            self.last_kwargs = kwargs
        return super().generate_text(prompt_name, model_config, **kwargs)


class UniqueQuestionGateway(FakeModelGateway):
    def generate_text(self, prompt_name, model_config, **kwargs):
        if prompt_name == "question_bundle":
            cypher = kwargs.get("cypher", "")
            fingerprint = abs(hash(cypher))
            limit_match = None
            import re
            limit_match = re.search(r"\bLIMIT\s+(\d+)\b", cypher, flags=re.IGNORECASE)
            limit_value = limit_match.group(1) if limit_match else None
            if "count(" in cypher.lower():
                canonical_options = [
                    "网络元素总共有多少个？",
                    "当前一共有多少个网络元素？",
                    "网络元素的总数是多少？",
                    "请统计网络元素的数量。",
                ]
                variant_options = [
                    "帮我统计网络元素总数",
                    "网络元素有多少个？",
                    "查一下网络元素数量",
                    "请给我网络元素数量统计",
                ]
            elif limit_value:
                canonical_options = [
                    f"列出前{limit_value}个网络设备。",
                    f"请给我前{limit_value}个网络设备。",
                    f"展示前{limit_value}个网络设备。",
                    f"帮我找出前{limit_value}个网络设备。",
                ]
                variant_options = [
                    f"前{limit_value}个网络设备有哪些？",
                    f"查一下前{limit_value}个网络设备",
                    f"给我看前{limit_value}个网络设备",
                    f"请列出前{limit_value}个网络设备",
                ]
            else:
                canonical_options = [
                    "列出网络中的设备。",
                    "请给我网络设备清单。",
                    "展示网络里的设备。",
                    "帮我找出网络设备。",
                ]
                variant_options = [
                    "网络设备有哪些？",
                    "查一下网络里的设备",
                    "给我看网络设备列表",
                    "请列出网络设备",
                ]
            canonical = canonical_options[fingerprint % len(canonical_options)]
            return json.dumps(
                {
                    "canonical_question": canonical,
                    "variants": [
                        {"style": "natural_short", "question": variant_options[(fingerprint + 1) % len(variant_options)]},
                        {"style": "spoken_query", "question": variant_options[(fingerprint + 2) % len(variant_options)]},
                        {"style": "business_term", "question": variant_options[(fingerprint + 3) % len(variant_options)]},
                        {"style": "ellipsis_query", "question": variant_options[(fingerprint + 4) % len(variant_options)]},
                        {"style": "task_oriented", "question": variant_options[(fingerprint + 5) % len(variant_options)]},
                    ],
                },
                ensure_ascii=False,
            )
        return super().generate_text(prompt_name, model_config, **kwargs)


class CypherLeakingQuestionGateway(FakeModelGateway):
    def generate_text(self, prompt_name, model_config, **kwargs):
        if prompt_name == "question_bundle":
            return json.dumps(
                {
                    "canonical_question": "MATCH (n:Person) RETURN n LIMIT 5",
                    "variants": [
                        {"style": "natural_short", "question": "RETURN n LIMIT 5"},
                        {"style": "spoken_query", "question": "MATCH (n:Person) RETURN n"},
                    ],
                },
                ensure_ascii=False,
            )
        return super().generate_text(prompt_name, model_config, **kwargs)


class FakeDispatcher:
    def __init__(self) -> None:
        self.calls = []
        self.row_calls = []

    def dispatch_samples(self, samples):
        self.calls.append([sample.id for sample in samples])
        return {
            "enabled": True,
            "status": "success",
            "host": "http://fake-host",
            "total": len(samples),
            "success": len(samples),
            "partial": 0,
            "failed": 0,
            "results": [{"id": sample.id, "status": "success"} for sample in samples],
        }

    def dispatch_release_rows(self, rows):
        self.row_calls.append([row["id"] for row in rows])
        return {
            "enabled": True,
            "status": "success",
            "host": "http://fake-host",
            "total": len(rows),
            "success": len(rows),
            "partial": 0,
            "failed": 0,
            "results": [{"id": row["id"], "status": "success"} for row in rows],
        }


class FakeDispatchClient:
    def __init__(self, responses) -> None:
        self.responses = responses
        self.calls = []

    def post(self, url, json):
        self.calls.append((url, json))
        response = self.responses[len(self.calls) - 1]
        if isinstance(response, Exception):
            raise response
        return response


class FakeGraphExecutor:
    def fetch_labels(self, config):
        return {
            "vertex": ["Person", "Project"],
            "edge": ["WORKS_ON"],
            "planner": "fake-graph",
        }

    def execute(self, cypher, config):
        normalized = cypher.lower()
        if "count(" in normalized:
            return (
                RuntimeMeta(latency_ms=3, planner="fake-graph"),
                ResultSignature(columns=["total"], column_types=["integer"], row_count=1, result_preview=[{"total": 1}]),
                True,
            )
        return (
            RuntimeMeta(latency_ms=3, planner="fake-graph"),
            ResultSignature(columns=["value"], column_types=["string"], row_count=1, result_preview=[{"value": "ok"}]),
            True,
        )

    def test_connection(self, config):
        return {
            "ok": True,
            "runtime_meta": RuntimeMeta(latency_ms=1, planner="fake-graph").model_dump(),
            "result_signature": ResultSignature(columns=["total"], column_types=["integer"], row_count=1, result_preview=[{"total": 1}]).model_dump(),
        }


class MismatchGraphExecutor(FakeGraphExecutor):
    def fetch_labels(self, config):
        return {
            "vertex": ["NetworkElement", "Port"],
            "edge": ["HAS_PORT"],
            "planner": "fake-graph",
        }


class RetryAwareGenerationService(GenerationService):
    def __init__(self, fail_attempts: int = 1, model_gateway=None) -> None:
        super().__init__(model_gateway=model_gateway)
        self.fail_attempts = fail_attempts
        self.instantiate_calls = 0
        self.diversity_keys = []

    def build_skeletons(self, schema, limits, diversity_key=None):
        self.diversity_keys.append(diversity_key)
        return super().build_skeletons(schema, limits, diversity_key=diversity_key)

    def instantiate_candidates(self, schema, skeletons, limits, llm_config=None):
        self.instantiate_calls += 1
        candidates = super().instantiate_candidates(schema, skeletons, limits, llm_config)
        if self.instantiate_calls <= self.fail_attempts:
            for candidate in candidates:
                candidate.cypher = "BROKEN"
        return candidates


class PipelineTest(unittest.TestCase):
    def test_question_bundle_prompt_renders(self) -> None:
        gateway = ModelGateway()
        rendered = gateway.render_prompt(
            "question_bundle",
            schema_summary="节点 NetworkElement",
            cypher="MATCH (n:NetworkElement) RETURN n LIMIT 5",
            query_types="LOOKUP",
            return_semantics="n",
            result_summary='{"columns":["n"]}',
            requested_styles=", ".join(QUESTION_VARIANT_STYLES),
        )
        self.assertIn("canonical_question", rendered)
        self.assertIn("MATCH (n:NetworkElement) RETURN n LIMIT 5", rendered)
        self.assertIn("natural_short", rendered)
        self.assertIn("语义", rendered)

    def test_cypher_candidate_bundle_prompt_renders(self) -> None:
        gateway = ModelGateway()
        rendered = gateway.render_prompt(
            "cypher_candidate_bundle",
            schema_summary="节点 Person: 属性 name, title\n关系 WORKS_ON: 约束 Person->Project",
            query_type="LOOKUP",
            structure_family="lookup_node_return",
            family_description="Return a matched node.",
            difficulty="L1",
            template_cypher="MATCH (n:Person) RETURN n LIMIT 5",
            slot_bindings=json.dumps({"node": "Person", "edge": "WORKS_ON"}, ensure_ascii=False),
        )
        self.assertIn("lookup_node_return", rendered)
        self.assertIn("MATCH (n:Person) RETURN n LIMIT 5", rendered)
        self.assertIn("只输出 JSON", rendered)

    def test_is_natural_language_question_rejects_cypher_like_text(self) -> None:
        self.assertFalse(is_natural_language_question("MATCH (n:Person) RETURN n LIMIT 5"))
        self.assertFalse(is_natural_language_question("请执行 RETURN n.name AS value"))
        self.assertTrue(is_natural_language_question("请列出前5个网络设备。"))

    def test_question_service_rejects_cypher_like_question_output(self) -> None:
        service = QuestionService(model_gateway=CypherLeakingQuestionGateway())
        validated_sample = ValidatedSample(
            candidate=CypherCandidate(
                skeleton_id="lookup_01",
                cypher="MATCH (n:Person) RETURN n LIMIT 5",
                query_types=["LOOKUP"],
                structure_family="lookup_node_return",
                bound_schema_items={"nodes": ["Person"], "edges": [], "properties": ["name"]},
                bound_values={"name": "'alice'"},
                difficulty="L1",
            ),
            validation=ValidationResult(
                syntax=True,
                schema=True,
                type_value=True,
                query_type_valid=True,
                family_valid=True,
                runtime=True,
                result_sanity=True,
                difficulty_valid=True,
            ),
            runtime_meta=RuntimeMeta(latency_ms=3, planner="fake-graph"),
            result_signature=ResultSignature(
                columns=["n"],
                column_types=["node"],
                row_count=1,
                result_preview=[{"name": "alice"}],
            ),
        )
        from app.domain.models import CanonicalSchemaSpec

        schema = CanonicalSchemaSpec(node_types=["Person"], node_properties={"Person": {"name": "STRING"}})
        with self.assertRaises(Exception):
            service.generate(validated_sample, schema, fake_gateway_config(), 3)

    def test_job_runs_to_completion(self) -> None:
        schema_path = Path(__file__).parent / "fixtures" / "schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        fake_gateway = FakeModelGateway()
        with TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            orchestrator = Orchestrator(
                job_store=JobStore(root=temp_root / "job-reports"),
                artifact_store=ArtifactStore(root=temp_root / "artifacts"),
                schema_compatibility_service=SchemaCompatibilityService(graph_executor=FakeGraphExecutor()),
                generation_service=GenerationService(model_gateway=fake_gateway),
                validation_service=ValidationService(graph_executor=FakeGraphExecutor()),
                question_service=QuestionService(model_gateway=fake_gateway),
                roundtrip_service=RoundtripService(model_gateway=fake_gateway),
            )
            job = orchestrator.create_job(
                JobRequest(
                    schema_input=schema,
                    tugraph_source={"type": "inline"},
                    tugraph_config={"base_url": None, "username": None, "password": None, "graph": None},
                )
            )
            completed = orchestrator.run_job(job.job_id)

            self.assertEqual(completed.status.value, "completed")
            self.assertTrue(completed.artifacts["schema"])
            self.assertTrue(completed.artifacts["report"])
            self.assertGreaterEqual(completed.metrics.get("sample_count", 0), 1)
            self.assertIn("language_coverage", completed.metrics)
            self.assertTrue(completed.metrics["language_coverage"]["covered_styles"])
            self.assertIn("natural_short", completed.metrics["language_coverage"]["covered_styles"])
            releases_path = Path(completed.artifacts["releases"])
            export_rows = [json.loads(line) for line in releases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(export_rows)
            for row in export_rows:
                self.assertEqual(set(row.keys()), {"id", "question", "cypher", "answer", "difficulty"})
            self.assertIn("difficulty_coverage", completed.metrics)
            self.assertTrue(completed.metrics["difficulty_coverage"]["covered_levels"])
            self.assertIn("query_type_coverage", completed.metrics)
            self.assertIn("structure_family_coverage", completed.metrics)
            self.assertTrue(completed.metrics["query_type_coverage"]["covered_query_types"])
            self.assertTrue(completed.metrics["structure_family_coverage"]["covered_families"])
            qa_rows = [json.loads(line) for line in Path(completed.artifacts["qa"]).read_text(encoding="utf-8").splitlines() if line.strip()]
            seen_questions = {}
            duplicate_questions = []
            for row in qa_rows:
                question = row["question_canonical_zh"]
                cypher = row["cypher"]
                if question in seen_questions and seen_questions[question] != cypher:
                    duplicate_questions.append(question)
                seen_questions.setdefault(question, cypher)
            self.assertFalse(duplicate_questions)

    def test_job_retries_when_first_attempt_generates_zero_final_samples(self) -> None:
        schema_path = Path(__file__).parent / "fixtures" / "schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        gateway = UniqueQuestionGateway()
        generation_service = RetryAwareGenerationService(fail_attempts=1, model_gateway=gateway)
        with TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            orchestrator = Orchestrator(
                job_store=JobStore(root=temp_root / "job-reports"),
                artifact_store=ArtifactStore(root=temp_root / "artifacts"),
                schema_compatibility_service=SchemaCompatibilityService(graph_executor=FakeGraphExecutor()),
                generation_service=generation_service,
                validation_service=ValidationService(graph_executor=FakeGraphExecutor()),
                question_service=QuestionService(model_gateway=gateway),
                roundtrip_service=RoundtripService(model_gateway=gateway),
            )
            job = orchestrator.create_job(
                JobRequest(
                    mode="online",
                    schema_input=schema,
                    output_config={"target_qa_count": 1},
                    tugraph_source={"type": "inline"},
                    tugraph_config={"base_url": None, "username": None, "password": None, "graph": None},
                )
            )

            completed = orchestrator.run_job(job.job_id)

            self.assertEqual(completed.status.value, "completed")
            self.assertGreaterEqual(completed.metrics.get("sample_count", 0), 1)
            self.assertGreaterEqual(generation_service.instantiate_calls, 2)
            self.assertGreaterEqual(len(generation_service.diversity_keys), 2)
            self.assertNotEqual(generation_service.diversity_keys[0], generation_service.diversity_keys[1])

    def test_job_fails_when_all_retry_attempts_produce_zero_samples(self) -> None:
        schema_path = Path(__file__).parent / "fixtures" / "schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        gateway = UniqueQuestionGateway()
        generation_service = RetryAwareGenerationService(fail_attempts=10, model_gateway=gateway)
        with TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            orchestrator = Orchestrator(
                job_store=JobStore(root=temp_root / "job-reports"),
                artifact_store=ArtifactStore(root=temp_root / "artifacts"),
                schema_compatibility_service=SchemaCompatibilityService(graph_executor=FakeGraphExecutor()),
                generation_service=generation_service,
                validation_service=ValidationService(graph_executor=FakeGraphExecutor()),
                question_service=QuestionService(model_gateway=gateway),
                roundtrip_service=RoundtripService(model_gateway=gateway),
            )
            job = orchestrator.create_job(
                JobRequest(
                    mode="online",
                    schema_input=schema,
                    output_config={"target_qa_count": 1},
                    tugraph_source={"type": "inline"},
                    tugraph_config={"base_url": None, "username": None, "password": None, "graph": None},
                )
            )

            failed = orchestrator.run_job(job.job_id)

            self.assertEqual(failed.status.value, "failed")
            self.assertTrue(failed.errors)
            self.assertEqual(failed.errors[-1]["code"], "NO_VALID_QA_GENERATED")

    def test_job_fails_fast_when_schema_does_not_match_tugraph_labels(self) -> None:
        schema_path = Path(__file__).parent / "fixtures" / "schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        gateway = UniqueQuestionGateway()
        with TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            orchestrator = Orchestrator(
                job_store=JobStore(root=temp_root / "job-reports"),
                artifact_store=ArtifactStore(root=temp_root / "artifacts"),
                schema_compatibility_service=SchemaCompatibilityService(graph_executor=MismatchGraphExecutor()),
                generation_service=GenerationService(model_gateway=gateway),
                validation_service=ValidationService(graph_executor=FakeGraphExecutor()),
                question_service=QuestionService(model_gateway=gateway),
                roundtrip_service=RoundtripService(model_gateway=gateway),
            )
            job = orchestrator.create_job(
                JobRequest(
                    mode="online",
                    schema_input=schema,
                    tugraph_source={"type": "inline"},
                    tugraph_config={"base_url": "http://fake", "username": "admin", "password": "x", "graph": "g"},
                )
            )

            failed = orchestrator.run_job(job.job_id)

            self.assertEqual(failed.status.value, "failed")
            self.assertTrue(failed.errors)
            self.assertEqual(failed.errors[-1]["code"], "SCHEMA_VALIDATION_ERROR")

    def test_roundtrip_sends_schema_and_result_context_to_llm(self) -> None:
        gateway = InspectingRoundtripGateway()
        service = RoundtripService(model_gateway=gateway)
        sample = fake_qa_sample()

        ok, variants, styles = service.check(sample, fake_gateway_config())

        self.assertTrue(ok)
        self.assertEqual(variants, sample.question_variants_zh)
        self.assertEqual(styles, sample.question_variant_styles)
        self.assertIn("schema_summary", gateway.last_kwargs)
        self.assertIn("return_semantics", gateway.last_kwargs)
        self.assertIn("result_summary", gateway.last_kwargs)
        self.assertIn("节点", gateway.last_kwargs["schema_summary"])
        self.assertIn("total", gateway.last_kwargs["return_semantics"])

    def test_roundtrip_uses_structured_canonical_checks(self) -> None:
        service = RoundtripService(model_gateway=FakeModelGateway())
        sample = fake_qa_sample()

        ok, variants, styles = service._parse_bundle_result(
            json.dumps(
                {
                    "canonical_pass": True,
                    "canonical_checks": {
                        "filters": True,
                        "ordering": False,
                        "topk_limit": True,
                        "aggregation_grouping": True,
                        "return_target": True,
                    },
                    "approved_styles": ["natural_short"],
                },
                ensure_ascii=False,
            ),
            sample,
        )

        self.assertFalse(ok)
        self.assertEqual(variants, ["列出网络里的设备"])
        self.assertEqual(styles, ["natural_short"])

    def test_roundtrip_rejects_question_that_omits_limit(self) -> None:
        service = RoundtripService(model_gateway=FakeModelGateway())
        sample = fake_qa_sample()
        sample.question_canonical_zh = "列出网络中的设备"
        sample.cypher = "MATCH (n:NetworkElement) RETURN n LIMIT 5"

        ok, _, _ = service._parse_bundle_result(
            json.dumps(
                {
                    "canonical_pass": True,
                    "canonical_checks": {
                        "filters": True,
                        "ordering": True,
                        "topk_limit": True,
                        "aggregation_grouping": True,
                        "return_target": True,
                    },
                    "approved_styles": QUESTION_VARIANT_STYLES,
                },
                ensure_ascii=False,
            ),
            sample,
        )

        self.assertFalse(ok)

    def test_roundtrip_rejects_question_that_contains_cypher(self) -> None:
        service = RoundtripService(model_gateway=FakeModelGateway())
        sample = fake_qa_sample()
        sample.question_canonical_zh = "MATCH (n:NetworkElement) RETURN n LIMIT 5"
        sample.cypher = "MATCH (n:NetworkElement) RETURN n LIMIT 5"

        ok, _, _ = service._parse_bundle_result(
            json.dumps(
                {
                    "canonical_pass": True,
                    "canonical_checks": {
                        "filters": True,
                        "ordering": True,
                        "topk_limit": True,
                        "aggregation_grouping": True,
                        "return_target": True,
                    },
                    "approved_styles": QUESTION_VARIANT_STYLES,
                },
                ensure_ascii=False,
            ),
            sample,
        )

        self.assertFalse(ok)

    def test_job_respects_target_qa_count_and_dispatches_only_final_batch(self) -> None:
        schema_path = Path(__file__).parent / "fixtures" / "schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        gateway = UniqueQuestionGateway()
        dispatcher = FakeDispatcher()
        with TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            orchestrator = Orchestrator(
                job_store=JobStore(root=temp_root / "job-reports"),
                artifact_store=ArtifactStore(root=temp_root / "artifacts"),
                schema_compatibility_service=SchemaCompatibilityService(graph_executor=FakeGraphExecutor()),
                generation_service=GenerationService(model_gateway=gateway),
                validation_service=ValidationService(graph_executor=FakeGraphExecutor()),
                question_service=QuestionService(model_gateway=gateway),
                roundtrip_service=RoundtripService(model_gateway=gateway),
                qa_dispatcher=dispatcher,
            )
            job = orchestrator.create_job(
                JobRequest(
                    mode="offline",
                    schema_input=schema,
                    output_config={"target_qa_count": 3},
                    tugraph_source={"type": "inline"},
                    tugraph_config={"base_url": None, "username": None, "password": None, "graph": None},
                )
            )

            completed = orchestrator.run_job(job.job_id)
            releases_path = Path(completed.artifacts["releases"])
            export_rows = [json.loads(line) for line in releases_path.read_text(encoding="utf-8").splitlines() if line.strip()]

            self.assertEqual(len(export_rows), 3)
            self.assertEqual(completed.metrics["sample_count"], 3)
            self.assertEqual(len(dispatcher.calls), 1)
            self.assertEqual(len(dispatcher.calls[0]), 3)
            self.assertEqual(dispatcher.calls[0], [row["id"] for row in export_rows])

    def test_job_avoids_reusing_previous_releases_when_enough_candidates_exist(self) -> None:
        schema_path = Path(__file__).parent / "fixtures" / "schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        gateway = UniqueQuestionGateway()
        with TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            orchestrator = Orchestrator(
                job_store=JobStore(root=temp_root / "job-reports"),
                artifact_store=ArtifactStore(root=temp_root / "artifacts"),
                schema_compatibility_service=SchemaCompatibilityService(graph_executor=FakeGraphExecutor()),
                generation_service=GenerationService(model_gateway=gateway),
                validation_service=ValidationService(graph_executor=FakeGraphExecutor()),
                question_service=QuestionService(model_gateway=gateway),
                roundtrip_service=RoundtripService(model_gateway=gateway),
            )

            first = orchestrator.create_and_run_job(
                JobRequest(
                    mode="offline",
                    schema_input=schema,
                    output_config={"target_qa_count": 2},
                    tugraph_source={"type": "inline"},
                    tugraph_config={"base_url": None, "username": None, "password": None, "graph": None},
                )
            )
            second = orchestrator.create_and_run_job(
                JobRequest(
                    mode="offline",
                    schema_input=schema,
                    output_config={"target_qa_count": 2},
                    tugraph_source={"type": "inline"},
                    tugraph_config={"base_url": None, "username": None, "password": None, "graph": None},
                )
            )

            first_rows = [json.loads(line) for line in Path(first.artifacts["releases"]).read_text(encoding="utf-8").splitlines() if line.strip()]
            second_rows = [json.loads(line) for line in Path(second.artifacts["releases"]).read_text(encoding="utf-8").splitlines() if line.strip()]

        self.assertEqual(len(first_rows), 2)
        self.assertEqual(len(second_rows), 2)
        self.assertTrue({row["question"] for row in first_rows}.isdisjoint({row["question"] for row in second_rows}))
        self.assertTrue({row["cypher"] for row in first_rows}.isdisjoint({row["cypher"] for row in second_rows}))

    def test_job_can_redispatch_final_release_batch_and_record_history(self) -> None:
        schema_path = Path(__file__).parent / "fixtures" / "schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        gateway = UniqueQuestionGateway()
        dispatcher = FakeDispatcher()
        with TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            orchestrator = Orchestrator(
                job_store=JobStore(root=temp_root / "job-reports"),
                artifact_store=ArtifactStore(root=temp_root / "artifacts"),
                schema_compatibility_service=SchemaCompatibilityService(graph_executor=FakeGraphExecutor()),
                generation_service=GenerationService(model_gateway=gateway),
                validation_service=ValidationService(graph_executor=FakeGraphExecutor()),
                question_service=QuestionService(model_gateway=gateway),
                roundtrip_service=RoundtripService(model_gateway=gateway),
                qa_dispatcher=dispatcher,
            )
            completed = orchestrator.create_and_run_job(
                JobRequest(
                    mode="offline",
                    schema_input=schema,
                    output_config={"target_qa_count": 2},
                    tugraph_source={"type": "inline"},
                    tugraph_config={"base_url": None, "username": None, "password": None, "graph": None},
                )
            )

            redispatched = orchestrator.redispatch_job(completed.job_id)

            self.assertEqual(len(dispatcher.row_calls), 1)
            self.assertEqual(len(dispatcher.row_calls[0]), 2)
            self.assertIn("dispatch_history", redispatched.metrics)
            self.assertEqual(len(redispatched.metrics["dispatch_history"]), 1)
            self.assertEqual(redispatched.metrics["dispatch_history"][0]["trigger"], "manual")
            self.assertEqual(redispatched.metrics["dispatch"]["status"], "success")

    def test_job_delete_removes_report_and_artifacts(self) -> None:
        schema_path = Path(__file__).parent / "fixtures" / "schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        gateway = UniqueQuestionGateway()
        with TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            job_store = JobStore(root=temp_root / "job-reports")
            artifact_store = ArtifactStore(root=temp_root / "artifacts")
            orchestrator = Orchestrator(
                job_store=job_store,
                artifact_store=artifact_store,
                schema_compatibility_service=SchemaCompatibilityService(graph_executor=FakeGraphExecutor()),
                generation_service=GenerationService(model_gateway=gateway),
                validation_service=ValidationService(graph_executor=FakeGraphExecutor()),
                question_service=QuestionService(model_gateway=gateway),
                roundtrip_service=RoundtripService(model_gateway=gateway),
            )

            completed = orchestrator.create_and_run_job(
                JobRequest(
                    mode="offline",
                    schema_input=schema,
                    output_config={"target_qa_count": 2},
                    tugraph_source={"type": "inline"},
                    tugraph_config={"base_url": None, "username": None, "password": None, "graph": None},
                )
            )

            report_path = Path(completed.artifacts["report"])
            release_path = Path(completed.artifacts["releases"])
            self.assertTrue(report_path.exists())
            self.assertTrue(release_path.exists())

            orchestrator.delete_job(completed.job_id)

            self.assertFalse(job_store.path_for(completed.job_id).exists())
            self.assertFalse(report_path.exists())
            self.assertFalse(release_path.exists())

    def test_online_mode_uses_single_question_generation_call_per_validated_sample(self) -> None:
        schema_path = Path(__file__).parent / "fixtures" / "schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        fake_gateway = FakeModelGateway()
        with TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            orchestrator = Orchestrator(
                job_store=JobStore(root=temp_root / "job-reports"),
                artifact_store=ArtifactStore(root=temp_root / "artifacts"),
                schema_compatibility_service=SchemaCompatibilityService(graph_executor=FakeGraphExecutor()),
                generation_service=GenerationService(model_gateway=fake_gateway),
                validation_service=ValidationService(graph_executor=FakeGraphExecutor()),
                question_service=QuestionService(model_gateway=fake_gateway),
                roundtrip_service=RoundtripService(model_gateway=fake_gateway),
            )
            job = orchestrator.create_job(
                JobRequest(
                    mode="online",
                    schema_input=schema,
                    tugraph_source={"type": "inline"},
                    tugraph_config={"base_url": None, "username": None, "password": None, "graph": None},
                )
            )

            completed = orchestrator.run_job(job.job_id)

            self.assertEqual(completed.status.value, "completed")
            question_bundle_calls = fake_gateway.calls.count("question_bundle")
            consistency_calls = fake_gateway.calls.count("question_bundle_consistency")
            self.assertGreaterEqual(question_bundle_calls, completed.metrics.get("sample_count"))
            self.assertGreaterEqual(consistency_calls, completed.metrics.get("sample_count"))
            self.assertNotIn("question_variants", fake_gateway.calls)
            self.assertNotIn("cypher_canonical_question", fake_gateway.calls)

    def test_model_gateway_retries_on_rate_limit(self) -> None:
        gateway = ModelGateway()

        class FakeClient:
            def __init__(self) -> None:
                self.calls = 0

            def post(self, *args, **kwargs):
                self.calls += 1
                request = httpx.Request("POST", "https://example.com/chat/completions")
                if self.calls == 1:
                    return httpx.Response(429, request=request, json={"error": "rate limit"})
                return httpx.Response(
                    200,
                    request=request,
                    json={"choices": [{"message": {"content": "ok"}}]},
                )

        fake_client = FakeClient()
        gateway._client = fake_client

        output = gateway.generate_text(
            "question_bundle",
            fake_gateway_config(),
            schema_summary="节点 NetworkElement",
            cypher="MATCH (n:NetworkElement) RETURN n LIMIT 5",
            query_types="LOOKUP",
            return_semantics="n",
            result_summary='{"columns":["n"]}',
            requested_styles=", ".join(QUESTION_VARIANT_STYLES),
        )

        self.assertEqual(output, "ok")
        self.assertEqual(fake_client.calls, 2)

    def test_model_gateway_retries_on_timeout(self) -> None:
        gateway = ModelGateway()

        class FakeClient:
            def __init__(self) -> None:
                self.calls = 0

            def post(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise httpx.ReadTimeout("timed out")
                request = httpx.Request("POST", "https://example.com/chat/completions")
                return httpx.Response(
                    200,
                    request=request,
                    json={"choices": [{"message": {"content": "ok"}}]},
                )

        fake_client = FakeClient()
        gateway._client = fake_client

        output = gateway.generate_text(
            "question_bundle",
            fake_gateway_config(),
            schema_summary="节点 NetworkElement",
            cypher="MATCH (n:NetworkElement) RETURN n LIMIT 5",
            query_types="LOOKUP",
            return_semantics="n",
            result_summary='{"columns":["n"]}',
            requested_styles=", ".join(QUESTION_VARIANT_STYLES),
        )

        self.assertEqual(output, "ok")
        self.assertEqual(fake_client.calls, 2)

    def test_model_gateway_disables_thinking_for_question_generation(self) -> None:
        gateway = ModelGateway()

        class FakeClient:
            def __init__(self) -> None:
                self.payloads = []

            def post(self, *args, **kwargs):
                self.payloads.append(kwargs["json"])
                request = httpx.Request("POST", "https://example.com/chat/completions")
                return httpx.Response(
                    200,
                    request=request,
                    json={"choices": [{"message": {"content": "ok"}}]},
                )

        fake_client = FakeClient()
        gateway._client = fake_client

        gateway.generate_text(
            "question_bundle",
            fake_gateway_config(),
            schema_summary="节点 NetworkElement",
            cypher="MATCH (n:NetworkElement) RETURN n LIMIT 5",
            query_types="LOOKUP",
            return_semantics="n",
            result_summary='{"columns":["n"]}',
            requested_styles=", ".join(QUESTION_VARIANT_STYLES),
        )

        self.assertEqual(fake_client.payloads[0]["thinking"]["type"], "disabled")

    def test_dispatcher_records_response_body_and_writes_log(self) -> None:
        request = httpx.Request("POST", "http://example.com/api/v1/qa/goldens")
        client = FakeDispatchClient(
            [
                httpx.Response(422, request=request, text='{"detail":"answer must be string"}'),
                httpx.Response(422, request=request, text='{"detail":"answer must be string"}'),
                httpx.Response(422, request=request, text='{"detail":"answer must be string"}'),
            ]
        )
        with TemporaryDirectory() as tempdir:
            dispatcher = QADispatcher(client=client, log_root=Path(tempdir))
            result = dispatcher._post_with_retry(
                "http://example.com/api/v1/qa/goldens",
                {"id": "qa_1", "cypher": "MATCH (n) RETURN n LIMIT 1", "answer": [], "difficulty": "L1"},
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["status_code"], 422)
            self.assertIn("answer must be string", result["response_body"])
            log_path = Path(tempdir) / "dispatch.log"
            self.assertTrue(log_path.exists())
            log_lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(log_lines), 1)
            self.assertIn("answer must be string", log_lines[0]["result"]["response_body"])

    def test_result_summary_truncates_large_runtime_preview(self) -> None:
        sample = ValidatedSample(
            candidate=CypherCandidate(
                skeleton_id="lookup_01",
                cypher="MATCH (n:NetworkElement) RETURN n LIMIT 5",
                query_types=["LOOKUP"],
                structure_family="lookup_node_return",
                bound_schema_items={"nodes": ["NetworkElement"], "edges": [], "properties": ["id"]},
                bound_values={"id": "ne-1001"},
                difficulty="L1",
            ),
            validation=ValidationResult(
                syntax=True,
                schema=True,
                type_value=True,
                runtime=True,
                result_sanity=True,
            ),
            runtime_meta=RuntimeMeta(latency_ms=12, planner="mock"),
            result_signature=ResultSignature(
                columns=["n"],
                column_types=["string"],
                row_count=5,
                result_preview=[
                    {"col_0": "x" * 1200},
                    {"col_0": "y" * 1200},
                ],
            ),
        )
        summary = build_result_summary(sample)

        self.assertLess(len(summary), 500)

    def test_manual_import_persists_qa_assets(self) -> None:
        from app.domain.importing.service import QAImportService

        artifact_store = ArtifactStore()
        service = QAImportService(artifact_store=artifact_store)
        payload = "\n".join(
            [
                json.dumps(
                    {
                        "question_canonical_zh": "网络元素总共有多少个？",
                        "question_variants_zh": ["网络元素总共有多少个？", "一共有多少个网络元素？"],
                        "cypher": "MATCH (n:NetworkElement) RETURN count(n) AS total",
                        "query_types": ["AGGREGATION"],
                        "difficulty": "L4",
                        "answer": [{"total": 40}],
                        "validation": {
                            "syntax": True,
                            "schema": True,
                            "type_value": True,
                            "difficulty_valid": True,
                            "runtime": True,
                            "result_sanity": True,
                            "roundtrip_check": True,
                        },
                        "result_signature": {
                            "columns": ["total"],
                            "column_types": ["0"],
                            "row_count": 1,
                            "result_preview": [{"col_0": 40}],
                        },
                    },
                    ensure_ascii=False,
                )
            ]
        )

        record = service.import_payload(payload, source_type="inline")

        self.assertEqual(record.status, "completed")
        self.assertEqual(record.sample_count, 1)
        self.assertIn("qa", record.artifacts)
        self.assertIn("report", record.artifacts)
        self.assertIn("language_coverage", record.report)
        imported_rows = Path(record.artifacts["qa"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(imported_rows), 1)

    def test_generation_service_uses_query_type_registry_families(self) -> None:
        from app.domain.generation.service import GenerationService
        from app.domain.generation.registry import QUERY_TYPE_REGISTRY
        from app.domain.models import CanonicalSchemaSpec, GenerationLimits

        schema = CanonicalSchemaSpec(
            node_types=["NetworkElement", "Port", "Card", "Site"],
            edge_types=["HAS_PORT", "HAS_CARD", "LOCATED_IN"],
            node_properties={"NetworkElement": {"id": "string"}},
            edge_constraints={
                "HAS_PORT": [["NetworkElement", "Port"]],
                "HAS_CARD": [["Port", "Card"]],
                "LOCATED_IN": [["Card", "Site"]],
            },
            value_catalog={"NetworkElement.id": ["ne-1001"]},
        )
        service = GenerationService()
        total_skeletons = sum(len(family["difficulty_band"]) for families in QUERY_TYPE_REGISTRY.values() for family in families)
        limits = GenerationLimits(max_skeletons=total_skeletons, max_candidates_per_skeleton=1, max_variants_per_question=1)
        skeletons = service.build_skeletons(schema, limits)
        candidates = service.instantiate_candidates(schema, skeletons, limits)
        self.assertEqual(len(skeletons), total_skeletons)
        self.assertEqual({item.query_types[0] for item in skeletons}, set(QUERY_TYPE_REGISTRY.keys()))

        family_to_query_type = {
            family["family"]: query_type
            for query_type, families in QUERY_TYPE_REGISTRY.items()
            for family in families
        }
        family_to_band = {
            family["family"]: set(family["difficulty_band"])
            for query_type, families in QUERY_TYPE_REGISTRY.items()
            for family in families
        }

        skeleton_family_set = {item.structure_family for item in skeletons}
        self.assertEqual(skeleton_family_set, set(family_to_query_type.keys()))

        for skeleton in skeletons:
            self.assertEqual(skeleton.query_types, [family_to_query_type[skeleton.structure_family]])
            self.assertIn(skeleton.difficulty_floor, family_to_band[skeleton.structure_family])

        for candidate in candidates:
            self.assertEqual(candidate.query_types, [family_to_query_type[candidate.structure_family]])
            self.assertIn(candidate.difficulty, family_to_band[candidate.structure_family])

        family_to_cypher = {candidate.structure_family: candidate.cypher for candidate in candidates}
        self.assertIn(" OR ", family_to_cypher["filter_boolean_combo"].upper())
        self.assertIn("STARTS WITH", family_to_cypher["filter_boolean_combo"].upper())
        self.assertIn("MATCH (A:", family_to_cypher["attribute_comparison"].upper())
        self.assertIn("<>", family_to_cypher["attribute_comparison"].upper())
        self.assertIn(" WITH ", family_to_cypher["aggregate_comparison"].upper())
        self.assertIn("TOTAL >=", family_to_cypher["aggregate_comparison"].upper())
        self.assertIn("MATCH P=", family_to_cypher["variable_length_path"].upper())
        self.assertIn("WHERE C.", family_to_cypher["path_constrained_target"].upper())
        self.assertIn(" UNION MATCH ", family_to_cypher["set_like_union_projection"].upper())
        self.assertIn(" WITH ", family_to_cypher["with_stage_aggregate"].upper())

    def test_generation_service_rotates_online_skeleton_window_by_job_key(self) -> None:
        from app.domain.generation.service import GenerationService
        from app.domain.models import CanonicalSchemaSpec, GenerationLimits

        schema = CanonicalSchemaSpec(
            node_types=["NetworkElement", "Port", "Card", "Site"],
            edge_types=["HAS_PORT", "HAS_CARD", "LOCATED_IN"],
            node_properties={"NetworkElement": {"id": "string"}},
            edge_constraints={
                "HAS_PORT": [["NetworkElement", "Port"]],
                "HAS_CARD": [["Port", "Card"]],
                "LOCATED_IN": [["Card", "Site"]],
            },
            value_catalog={"NetworkElement.id": ["ne-1001"]},
        )
        service = GenerationService()
        limits = GenerationLimits(max_skeletons=8, max_candidates_per_skeleton=1, max_variants_per_question=1)

        first = service.build_skeletons(schema, limits, diversity_key="job_alpha")
        second = service.build_skeletons(schema, limits, diversity_key="job_beta")

        self.assertEqual(len(first), 8)
        self.assertEqual(len(second), 8)
        self.assertNotEqual(
            [item.structure_family for item in first],
            [item.structure_family for item in second],
        )

    def test_generation_service_adds_llm_candidates(self) -> None:
        from app.domain.generation.service import GenerationService
        from app.domain.models import CanonicalSchemaSpec, CypherSkeleton, GenerationLimits

        schema = CanonicalSchemaSpec(
            node_types=["Person", "Project"],
            edge_types=["WORKS_ON"],
            node_properties={"Person": {"name": "string", "title": "string"}},
            edge_properties={"WORKS_ON": {"role": "string"}},
            edge_constraints={"WORKS_ON": [["Person", "Project"]]},
            value_catalog={"Person.name": ["Alice"]},
        )
        skeleton = CypherSkeleton(
            skeleton_id="lookup_node_return_l1_01",
            query_types=["LOOKUP"],
            structure_family="lookup_node_return",
            pattern_template="MATCH (n:{node}) RETURN n LIMIT 5",
            slots={
                "node_slots": ["node"],
                "edge_slots": [],
                "property_slots": [],
                "filter_slots": [],
                "agg_slots": [],
                "order_slots": [],
                "return_slots": ["n"],
            },
            difficulty_floor="L1",
        )
        service = GenerationService(model_gateway=FakeModelGateway())

        candidates = service.instantiate_candidates(
            schema,
            [skeleton],
            GenerationLimits(max_skeletons=1, max_candidates_per_skeleton=1, max_variants_per_question=1),
            fake_gateway_config(),
        )

        generation_modes = {candidate.generation_mode for candidate in candidates}
        self.assertIn("template", generation_modes)
        self.assertIn("llm_direct", generation_modes)
        self.assertIn("llm_refine", generation_modes)

    def test_generation_service_varies_template_candidates(self) -> None:
        from app.domain.generation.service import GenerationService
        from app.domain.models import CanonicalSchemaSpec, CypherSkeleton, GenerationLimits

        schema = CanonicalSchemaSpec(
            node_types=["Person", "Project", "Department"],
            edge_types=["WORKS_ON", "BELONGS_TO"],
            node_properties={
                "Person": {"name": "string", "title": "string", "level": "string"},
                "Project": {"name": "string"},
                "Department": {"name": "string"},
            },
            edge_constraints={
                "WORKS_ON": [["Person", "Project"]],
                "BELONGS_TO": [["Project", "Department"]],
            },
            value_catalog={
                "Person.name": ["Alice", "Bob"],
                "Person.title": ["Researcher", "Architect"],
                "Person.level": ["L1", "L2"],
            },
        )
        skeleton = CypherSkeleton(
            skeleton_id="filter_single_condition_l2_01",
            query_types=["FILTER"],
            structure_family="filter_single_condition",
            pattern_template="MATCH (n:{node}) WHERE n.{property} = '{value}' RETURN n LIMIT 10",
            slots={
                "node_slots": ["node"],
                "edge_slots": [],
                "property_slots": ["property"],
                "filter_slots": ["value"],
                "agg_slots": [],
                "order_slots": [],
                "return_slots": ["n"],
            },
            difficulty_floor="L2",
        )
        service = GenerationService(model_gateway=FakeModelGateway())

        candidates = service.instantiate_candidates(
            schema,
            [skeleton],
            GenerationLimits(max_skeletons=1, max_candidates_per_skeleton=3, max_variants_per_question=1),
        )

        template_cyphers = {
            candidate.cypher
            for candidate in candidates
            if candidate.generation_mode == "template"
        }
        self.assertGreaterEqual(len(template_cyphers), 2)

    def test_generation_service_formats_numeric_literals_without_quotes(self) -> None:
        from app.domain.generation.service import GenerationService
        from app.domain.models import CanonicalSchemaSpec, CypherSkeleton, GenerationLimits

        schema = CanonicalSchemaSpec(
            node_types=["Port"],
            node_properties={"Port": {"speed": "INT64"}},
            value_catalog={"Port.speed": ["1000", "10000"]},
        )
        skeleton = CypherSkeleton(
            skeleton_id="filter_range_condition_l2_01",
            query_types=["FILTER"],
            structure_family="filter_range_condition",
            pattern_template="MATCH (n:{node}) WHERE n.{property} >= {value} RETURN n LIMIT 10",
            slots={
                "node_slots": ["node"],
                "edge_slots": [],
                "property_slots": ["property"],
                "filter_slots": ["value"],
                "agg_slots": [],
                "order_slots": [],
                "return_slots": ["n"],
            },
            difficulty_floor="L2",
        )
        service = GenerationService(model_gateway=FakeModelGateway())

        candidates = service.instantiate_candidates(
            schema,
            [skeleton],
            GenerationLimits(max_skeletons=1, max_candidates_per_skeleton=1, max_variants_per_question=1),
        )

        self.assertIn(">= 1000", candidates[0].cypher)
        self.assertNotIn(">= '1000'", candidates[0].cypher)

    def test_graph_executor_uses_header_names_for_list_rows(self) -> None:
        from app.integrations.tugraph.graph_executor import GraphExecutor

        executor = GraphExecutor()
        rows = executor._normalize_rows(
            [[40, "core"]],
            ["total", "category"],
        )

        self.assertEqual(rows, [{"total": 40, "category": "core"}])


def fake_gateway_config():
    from app.domain.models import ModelConfig

    return ModelConfig(model="glm-5", temperature=0.2, max_output_tokens=200)


def fake_qa_sample():
    from app.domain.models import QASample

    return QASample.model_validate(
        {
            "question_canonical_zh": "网络元素总共有多少个？",
            "question_variants_zh": ["列出网络里的设备", "网络里都有哪些设备啊？"],
            "question_variant_styles": ["natural_short", "spoken_query"],
            "cypher": "MATCH (n:NetworkElement) RETURN count(n) AS total",
            "cypher_normalized": "match (n:networkelement) return count(n) as total",
            "query_types": ["AGGREGATION"],
            "difficulty": "L4",
            "answer": [{"total": 40}],
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
            "result_signature": {
                "columns": ["total"],
                "column_types": ["int"],
                "row_count": 1,
                "result_preview": [{"total": 40}],
            },
            "split": "silver",
            "provenance": {
                "structure_family": "aggregate_global_count",
                "generation_mode": "llm_refine",
                "schema_summary": "节点 NetworkElement: 属性 id, vendor",
                "return_semantics": "total",
                "result_summary": "{\"columns\":[\"total\"],\"row_count\":1}",
            },
        }
    )


if __name__ == "__main__":
    unittest.main()
