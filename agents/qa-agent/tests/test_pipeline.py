from __future__ import annotations

import json
import hashlib
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
import requests

from app.domain.generation.service import GenerationService
from app.domain.models import CypherCandidate, CypherSkeleton, JobRequest, QueryPlan, ResultSignature, RuntimeMeta, TuGraphConfig, ValidatedSample, ValidationResult
from app.domain.questioning.service import QUESTION_VARIANT_STYLES, QuestionService, build_result_summary, is_natural_language_question
from app.domain.roundtrip.service import RoundtripService
from app.domain.schema.compatibility_service import SchemaCompatibilityService
from app.domain.validation.service import ValidationService
from app.logging import ModuleLogStore
from app.integrations.openai.model_gateway import ModelGateway
from app.integrations.qa_dispatcher import QADispatcher
from app.integrations.tugraph.graph_executor import GraphExecutor
from app.orchestrator.service import Orchestrator
from app.storage.artifact_store import ArtifactStore
from app.storage.job_store import JobStore
from tugraph_http_ops import TuGraphHttpOps


class FakeModelGateway(ModelGateway):
    def __init__(self) -> None:
        self.calls = []

    def _question_payload_for_cypher(self, cypher: str) -> dict:
        if "LIMIT 5" in cypher.upper():
            return {
                "canonical_question": "网络中前5个设备有哪些？",
                "variants": [
                    {"style": "natural_short", "question": "列出网络里的前5个设备"},
                    {"style": "spoken_query", "question": "网络里前5个设备都有啥？"},
                    {"style": "business_term", "question": "查询前5个网络设备"},
                    {"style": "ellipsis_query", "question": "前5个网络设备有哪些？"},
                    {"style": "task_oriented", "question": "帮我把网络中的前5个设备找出来"},
                ],
                "canonical_pass": True,
                "canonical_checks": {
                    "filters": True,
                    "temporal": True,
                    "ordering": True,
                    "topk_limit": True,
                    "aggregation_grouping": True,
                    "path_hops": True,
                    "comparison": True,
                    "return_target": True,
                },
                "approved_styles": QUESTION_VARIANT_STYLES,
            }
        return {
            "canonical_question": "网络中有哪些设备？",
            "variants": [
                {"style": "natural_short", "question": "列出网络里的设备"},
                {"style": "spoken_query", "question": "网络里都有哪些设备啊？"},
                {"style": "business_term", "question": "查询网络设备清单"},
                {"style": "ellipsis_query", "question": "网络设备有哪些？"},
                {"style": "task_oriented", "question": "帮我把网络中的设备找出来"},
            ],
            "canonical_pass": True,
            "canonical_checks": {
                "filters": True,
                "temporal": True,
                "ordering": True,
                "topk_limit": True,
                "aggregation_grouping": True,
                "path_hops": True,
                "comparison": True,
                "return_target": True,
            },
            "approved_styles": QUESTION_VARIANT_STYLES,
        }

    def generate_text(self, prompt_name, model_config, **kwargs):
        self.calls.append(prompt_name)
        if prompt_name == "cypher_candidate_batch":
            requests = json.loads(kwargs.get("requests_json", "[]"))
            return json.dumps(
                {
                    "items": [
                        {
                            "request_id": item["request_id"],
                            "candidates": [
                                {"mode": "llm_direct", "cypher": "MATCH (n:Person) RETURN n.name AS value LIMIT 5"},
                                {"mode": "llm_refine", "cypher": item.get("template_cypher") or "MATCH (n:Person) RETURN n LIMIT 5"},
                            ],
                        }
                        for item in requests
                    ]
                },
                ensure_ascii=False,
            )
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
        if prompt_name == "question_bundle_batch":
            requests = json.loads(kwargs.get("requests_json", "[]"))
            return json.dumps(
                {
                    "items": [
                        {"request_id": item["request_id"], **self._question_payload_for_cypher(item.get("cypher", ""))}
                        for item in requests
                    ]
                },
                ensure_ascii=False,
            )
        if prompt_name == "question_bundle":
            cypher = kwargs.get("cypher", "")
            return json.dumps(self._question_payload_for_cypher(cypher), ensure_ascii=False)
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
        if prompt_name == "question_cypher_consistency":
            self.last_kwargs = kwargs
        return super().generate_text(prompt_name, model_config, **kwargs)


class RejectingConsistencyGateway(FakeModelGateway):
    def generate_text(self, prompt_name, model_config, **kwargs):
        if prompt_name == "question_cypher_consistency":
            return "FAIL"
        return super().generate_text(prompt_name, model_config, **kwargs)


class InspectingBatchGateway(FakeModelGateway):
    def __init__(self) -> None:
        super().__init__()
        self.last_batch_kwargs = {}

    def generate_text(self, prompt_name, model_config, **kwargs):
        if prompt_name == "cypher_candidate_batch":
            self.last_batch_kwargs = kwargs
        return super().generate_text(prompt_name, model_config, **kwargs)


class UniqueQuestionGateway(FakeModelGateway):
    @staticmethod
    def _stable_fingerprint(cypher: str) -> int:
        digest = hashlib.sha1(cypher.encode("utf-8")).hexdigest()
        return int(digest[:8], 16)

    @staticmethod
    def _question_suffix(cypher: str) -> str:
        digest = hashlib.sha1(cypher.encode("utf-8")).hexdigest()
        return digest[:4]

    def generate_text(self, prompt_name, model_config, **kwargs):
        if prompt_name == "question_bundle_batch":
            requests = json.loads(kwargs.get("requests_json", "[]"))
            items = []
            for item in requests:
                cypher = item.get("cypher", "")
                fingerprint = self._stable_fingerprint(cypher)
                suffix = self._question_suffix(cypher)
                import re
                limit_match = re.search(r"\bLIMIT\s+(\d+)\b", cypher, flags=re.IGNORECASE)
                limit_value = limit_match.group(1) if limit_match else None
                if "count(" in cypher.lower():
                    canonical_options = [
                        f"网络元素总共有多少个？（{suffix}）",
                        f"当前一共有多少个网络元素？（{suffix}）",
                        f"网络元素的总数是多少？（{suffix}）",
                        f"请统计网络元素的数量。（{suffix}）",
                    ]
                    variant_options = [
                        f"帮我统计网络元素总数（{suffix}）",
                        f"网络元素有多少个？（{suffix}）",
                        f"查一下网络元素数量（{suffix}）",
                        f"请给我网络元素数量统计（{suffix}）",
                    ]
                elif limit_value:
                    canonical_options = [
                        f"列出前{limit_value}个网络设备。（{suffix}）",
                        f"请给我前{limit_value}个网络设备。（{suffix}）",
                        f"展示前{limit_value}个网络设备。（{suffix}）",
                        f"帮我找出前{limit_value}个网络设备。（{suffix}）",
                    ]
                    variant_options = [
                        f"前{limit_value}个网络设备有哪些？（{suffix}）",
                        f"查一下前{limit_value}个网络设备（{suffix}）",
                        f"给我看前{limit_value}个网络设备（{suffix}）",
                        f"请列出前{limit_value}个网络设备（{suffix}）",
                    ]
                else:
                    canonical_options = [
                        f"列出网络中的设备。（{suffix}）",
                        f"请给我网络设备清单。（{suffix}）",
                        f"展示网络里的设备。（{suffix}）",
                        f"帮我找出网络设备。（{suffix}）",
                    ]
                    variant_options = [
                        f"网络设备有哪些？（{suffix}）",
                        f"查一下网络里的设备（{suffix}）",
                        f"给我看网络设备列表（{suffix}）",
                        f"请列出网络设备（{suffix}）",
                    ]
                canonical = canonical_options[fingerprint % len(canonical_options)]
                items.append(
                    {
                        "request_id": item["request_id"],
                        "canonical_question": canonical,
                        "variants": [
                            {"style": "natural_short", "question": variant_options[(fingerprint + 1) % len(variant_options)]},
                            {"style": "spoken_query", "question": variant_options[(fingerprint + 2) % len(variant_options)]},
                            {"style": "business_term", "question": variant_options[(fingerprint + 3) % len(variant_options)]},
                            {"style": "ellipsis_query", "question": variant_options[(fingerprint + 4) % len(variant_options)]},
                            {"style": "task_oriented", "question": variant_options[(fingerprint + 5) % len(variant_options)]},
                        ],
                        "canonical_pass": True,
                        "canonical_checks": {
                            "filters": True,
                            "temporal": True,
                            "ordering": True,
                            "topk_limit": True,
                            "aggregation_grouping": True,
                            "path_hops": True,
                            "comparison": True,
                            "return_target": True,
                        },
                        "approved_styles": QUESTION_VARIANT_STYLES,
                    }
                )
            return json.dumps({"items": items}, ensure_ascii=False)
        if prompt_name == "question_bundle":
            cypher = kwargs.get("cypher", "")
            fingerprint = self._stable_fingerprint(cypher)
            suffix = self._question_suffix(cypher)
            limit_match = None
            import re
            limit_match = re.search(r"\bLIMIT\s+(\d+)\b", cypher, flags=re.IGNORECASE)
            limit_value = limit_match.group(1) if limit_match else None
            if "count(" in cypher.lower():
                canonical_options = [
                    f"网络元素总共有多少个？（{suffix}）",
                    f"当前一共有多少个网络元素？（{suffix}）",
                    f"网络元素的总数是多少？（{suffix}）",
                    f"请统计网络元素的数量。（{suffix}）",
                ]
                variant_options = [
                    f"帮我统计网络元素总数（{suffix}）",
                    f"网络元素有多少个？（{suffix}）",
                    f"查一下网络元素数量（{suffix}）",
                    f"请给我网络元素数量统计（{suffix}）",
                ]
            elif limit_value:
                canonical_options = [
                    f"列出前{limit_value}个网络设备。（{suffix}）",
                    f"请给我前{limit_value}个网络设备。（{suffix}）",
                    f"展示前{limit_value}个网络设备。（{suffix}）",
                    f"帮我找出前{limit_value}个网络设备。（{suffix}）",
                ]
                variant_options = [
                    f"前{limit_value}个网络设备有哪些？（{suffix}）",
                    f"查一下前{limit_value}个网络设备（{suffix}）",
                    f"给我看前{limit_value}个网络设备（{suffix}）",
                    f"请列出前{limit_value}个网络设备（{suffix}）",
                ]
            else:
                canonical_options = [
                    f"列出网络中的设备。（{suffix}）",
                    f"请给我网络设备清单。（{suffix}）",
                    f"展示网络里的设备。（{suffix}）",
                    f"帮我找出网络设备。（{suffix}）",
                ]
                variant_options = [
                    f"网络设备有哪些？（{suffix}）",
                    f"查一下网络里的设备（{suffix}）",
                    f"给我看网络设备列表（{suffix}）",
                    f"请列出网络设备（{suffix}）",
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
                    "canonical_pass": True,
                    "canonical_checks": {
                        "filters": True,
                        "temporal": True,
                        "ordering": True,
                        "topk_limit": True,
                        "aggregation_grouping": True,
                        "path_hops": True,
                        "comparison": True,
                        "return_target": True,
                    },
                    "approved_styles": QUESTION_VARIANT_STYLES,
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


class _FakeTuGraphResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)
        self.content = self.text.encode("utf-8")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class _RefreshingTuGraphSession:
    def __init__(self) -> None:
        self.login_calls = 0
        self.cypher_auth_headers = []

    def request(self, method, url, timeout=None, **kwargs):
        if url.endswith("/login"):
            self.login_calls += 1
            return _FakeTuGraphResponse(200, {"jwt": f"jwt-{self.login_calls}"})
        if url.endswith("/cypher"):
            headers = kwargs.get("headers") or {}
            self.cypher_auth_headers.append(headers.get("Authorization"))
            if len(self.cypher_auth_headers) == 1:
                return _FakeTuGraphResponse(401, {"error_message": "Unauthorized"})
            return _FakeTuGraphResponse(
                200,
                {
                    "header": [{"name": "total", "type": "integer"}],
                    "result": [[1]],
                    "size": 1,
                },
            )
        return _FakeTuGraphResponse(404, {})


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

    def instantiate_candidates_from_specs(self, schema, specs, limits, model_config=None):
        self.instantiate_calls += 1
        candidates = super().instantiate_candidates_from_specs(schema, specs, limits, model_config=model_config)
        if self.instantiate_calls <= self.fail_attempts:
            for candidate in candidates:
                candidate.cypher = "BROKEN"
        return candidates

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
            query_plan="{}",
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

    def test_question_service_uses_complete_result_rows_as_answer(self) -> None:
        full_rows = [{"name": f"service_{index}"} for index in range(8)]
        validated_sample = ValidatedSample(
            candidate=CypherCandidate(
                skeleton_id="lookup_full",
                cypher="MATCH (n:Service) RETURN n.name AS name",
                query_types=["LOOKUP"],
                structure_family="lookup_node_return",
                bound_schema_items={"nodes": ["Service"], "edges": [], "properties": ["name"]},
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
                columns=["name"],
                column_types=["string"],
                row_count=len(full_rows),
                result_preview=full_rows[:5],
                result_rows=full_rows,
            ),
        )
        from app.domain.models import CanonicalSchemaSpec

        schema = CanonicalSchemaSpec(node_types=["Service"], node_properties={"Service": {"name": "STRING"}})

        sample = QuestionService(model_gateway=FakeModelGateway()).generate(validated_sample, schema, fake_gateway_config(), 3)

        self.assertEqual(sample.answer, full_rows)
        self.assertEqual(len(sample.result_signature.result_preview), 5)

    def test_question_service_splits_large_batches_into_parallel_llm_requests(self) -> None:
        from app.domain.models import CanonicalSchemaSpec

        class ConcurrentQuestionBatchGateway:
            def __init__(self) -> None:
                self.calls: list[str] = []
                self.request_batch_sizes: list[int] = []
                self.active = 0
                self.max_active = 0
                self.lock = threading.Lock()

            def generate_text(self, prompt_name, model_config, **kwargs):
                self.calls.append(prompt_name)
                requests = json.loads(kwargs.get("requests_json", "[]"))
                self.request_batch_sizes.append(len(requests))
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                try:
                    time.sleep(0.05)
                    return json.dumps(
                        {
                            "items": [
                                {
                                    "request_id": item["request_id"],
                                    "canonical_question": f"请查询样本{item['request_id']}对应的结果？",
                                    "variants": [
                                        {
                                            "style": "natural_short",
                                            "question": f"样本{item['request_id']}的结果是什么？",
                                        }
                                    ],
                                    "canonical_pass": True,
                                    "canonical_checks": {"return_target": True},
                                    "approved_styles": ["natural_short"],
                                }
                                for item in requests
                            ]
                        },
                        ensure_ascii=False,
                    )
                finally:
                    with self.lock:
                        self.active -= 1

        schema = CanonicalSchemaSpec(node_types=["Person"], node_properties={"Person": {"name": "STRING"}})
        validated = [
            ValidatedSample(
                sample_id=f"val_{idx}",
                candidate=CypherCandidate(
                    candidate_id=f"cand_{idx}",
                    skeleton_id=f"sk_{idx}",
                    cypher=f"MATCH (n:Person) RETURN n.name AS name LIMIT {idx + 1}",
                    query_types=["LOOKUP"],
                    structure_family="lookup_property_projection",
                    generation_mode="llm_refine",
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
                    plan_valid=True,
                ),
                result_signature=ResultSignature(
                    columns=["name"],
                    column_types=["string"],
                    row_count=1,
                    result_preview=[{"name": f"person_{idx}"}],
                    result_rows=[{"name": f"person_{idx}"}],
                ),
                classified_difficulty="L1",
            )
            for idx in range(7)
        ]
        gateway = ConcurrentQuestionBatchGateway()

        samples = QuestionService(model_gateway=gateway).generate_batch(validated, schema, fake_gateway_config(), 1)

        self.assertEqual(len(samples), 7)
        self.assertEqual(gateway.calls, ["question_bundle_batch", "question_bundle_batch", "question_bundle_batch"])
        self.assertCountEqual(gateway.request_batch_sizes, [3, 3, 1])
        self.assertGreater(gateway.max_active, 1)

    def test_graph_executor_keeps_full_rows_separate_from_preview(self) -> None:
        class FakeClient:
            def call_cypher(self, cypher):
                return {
                    "header": [{"name": "name", "type": "string"}],
                    "result": [[f"service_{index}"] for index in range(8)],
                }

        executor = GraphExecutor()
        executor._get_client = lambda config: FakeClient()

        _, signature, ok = executor.execute("MATCH (n:Service) RETURN n.name AS name", TuGraphConfig(base_url="http://fake"))

        self.assertTrue(ok)
        self.assertEqual(signature.row_count, 8)
        self.assertEqual(len(signature.result_preview), 5)
        self.assertEqual(len(signature.result_rows), 8)

    @patch("tugraph_http_ops.requests.Session")
    def test_tugraph_client_refreshes_jwt_once_after_cypher_unauthorized(self, mock_session) -> None:
        session = _RefreshingTuGraphSession()
        mock_session.return_value = session

        payload = TuGraphHttpOps(
            base_url="http://fake-tugraph:7070",
            user="admin",
            password="secret",
            graph="network_schema_v10",
        ).call_cypher("MATCH (n) RETURN count(n) AS total")

        self.assertEqual(payload["size"], 1)
        self.assertEqual(session.login_calls, 2)
        self.assertEqual(session.cypher_auth_headers, ["Bearer jwt-1", "Bearer jwt-2"])

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
            self.assertEqual(completed.metrics.get("sample_count"), job.request.output_config.target_qa_count)
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
            self.assertIn("business_stages", completed.metrics)
            self.assertEqual(
                [stage["key"] for stage in completed.metrics["business_stages"]],
                [
                    "ground_schema",
                    "spec_coverage",
                    "generate_cypher",
                    "tugraph_validate",
                    "generate_qa",
                    "roundtrip_check",
                    "release_dispatch",
                ],
            )
            self.assertIn("performance", completed.metrics)
            qa_rows = [json.loads(line) for line in Path(completed.artifacts["qa"]).read_text(encoding="utf-8").splitlines() if line.strip()]
            seen_questions = {}
            duplicate_questions = []
            for row in qa_rows:
                question = row["question_canonical_zh"]
                cypher = row["cypher"]
                self.assertIn("query_plan", row)
                if question in seen_questions and seen_questions[question] != cypher:
                    duplicate_questions.append(question)
                seen_questions.setdefault(question, cypher)
            self.assertFalse(duplicate_questions)

    def test_completed_release_covers_all_l1_to_l8(self) -> None:
        schema = {
            "node_types": ["NetworkElement", "Port", "Tunnel", "Service"],
            "edge_types": ["HAS_PORT", "FIBER_SRC", "SERVES"],
            "node_properties": {
                "NetworkElement": {"id": "string", "vendor": "string", "created_at": "int"},
                "Port": {"id": "string", "admin_status": "string"},
                "Tunnel": {"id": "string", "latency": "int"},
                "Service": {"id": "string", "priority": "int"},
            },
            "edge_constraints": {
                "HAS_PORT": [["NetworkElement", "Port"]],
                "FIBER_SRC": [["Port", "Tunnel"]],
                "SERVES": [["Tunnel", "Service"]],
            },
            "value_catalog": {
                "NetworkElement.id": ["ne-1"],
                "NetworkElement.vendor": ["VendorA"],
                "NetworkElement.created_at": [20240101],
                "Port.id": ["port-1"],
                "Port.admin_status": ["UP"],
                "Tunnel.id": ["tunnel-1"],
                "Tunnel.latency": [12],
                "Service.id": ["svc-1"],
                "Service.priority": [3],
            },
        }
        fake_gateway = UniqueQuestionGateway()
        class CoverageQuestionGateway(UniqueQuestionGateway):
            def generate_text(self, prompt_name, model_config, **kwargs):
                if prompt_name == "question_bundle_batch":
                    requests = json.loads(kwargs.get("requests_json", "[]"))
                    import re

                    def canonical_for(item):
                        cypher = item.get("cypher", "")
                        request_id = item["request_id"]
                        limit_match = re.search(r"\bLIMIT\s+(\d+)\b", cypher, flags=re.IGNORECASE)
                        if "count(" in cypher.lower():
                            if limit_match:
                                return f"请统计样本{request_id}对应的数量总数，并返回前{limit_match.group(1)}个结果？"
                            return f"请统计样本{request_id}对应的数量总数？"
                        if limit_match:
                            return f"请列出样本{request_id}对应的前{limit_match.group(1)}个结果？"
                        return f"请查询样本{request_id}对应的图结果？"

                    return json.dumps(
                        {
                            "items": [
                                {
                                    "request_id": item["request_id"],
                                    "canonical_question": canonical_for(item),
                                    "variants": [
                                        {"style": "natural_short", "question": canonical_for(item)},
                                        {"style": "spoken_query", "question": canonical_for(item)},
                                    ],
                                    "canonical_pass": True,
                                    "canonical_checks": {
                                        "filters": True,
                                        "temporal": True,
                                        "ordering": True,
                                        "topk_limit": True,
                                        "aggregation_grouping": True,
                                        "path_hops": True,
                                        "comparison": True,
                                        "return_target": True,
                                    },
                                    "approved_styles": QUESTION_VARIANT_STYLES,
                                }
                                for item in requests
                            ]
                        },
                        ensure_ascii=False,
                    )
                return super().generate_text(prompt_name, model_config, **kwargs)

        fake_gateway = CoverageQuestionGateway()
        class NetworkGraphExecutor(FakeGraphExecutor):
            def fetch_labels(self, config):
                return {
                    "vertex": ["NetworkElement", "Port", "Tunnel", "Service"],
                    "edge": ["HAS_PORT", "FIBER_SRC", "SERVES"],
                    "planner": "fake-graph",
                }

        graph_executor = NetworkGraphExecutor()
        with TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            orchestrator = Orchestrator(
                job_store=JobStore(root=temp_root / "job-reports"),
                artifact_store=ArtifactStore(root=temp_root / "artifacts"),
                schema_compatibility_service=SchemaCompatibilityService(graph_executor=graph_executor),
                generation_service=GenerationService(model_gateway=fake_gateway),
                validation_service=ValidationService(graph_executor=graph_executor),
                question_service=QuestionService(model_gateway=fake_gateway),
                roundtrip_service=RoundtripService(model_gateway=fake_gateway),
            )
            job = orchestrator.create_job(
                JobRequest(
                    schema_input=schema,
                    output_config={"target_qa_count": 8},
                    tugraph_source={"type": "inline"},
                    tugraph_config={"base_url": None, "username": None, "password": None, "graph": None},
                )
            )

            completed = orchestrator.run_job(job.job_id)

            self.assertEqual(completed.status.value, "completed")
            release_rows = [
                json.loads(line)
                for line in Path(completed.artifacts["releases"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual({row["difficulty"] for row in release_rows}, {f"L{level}" for level in range(1, 9)})
            self.assertEqual(set(completed.metrics["difficulty_coverage"]["covered_levels"]), {f"L{level}" for level in range(1, 9)})
            log_path = temp_root / "artifacts" / "logs" / "jobs" / f"{completed.job_id}.log"
            self.assertTrue(log_path.exists())
            self.assertIn("generate_cypher", log_path.read_text(encoding="utf-8"))

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

    def test_online_job_retries_until_difficulty_targets_are_satisfied(self) -> None:
        schema_path = Path(__file__).parent / "fixtures" / "schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        class TargetShortfallGenerationService:
            def __init__(self) -> None:
                self.instantiate_calls = 0

            def instantiate_candidates_from_specs(self, schema, specs, limits, model_config=None):
                self.instantiate_calls += 1
                count = 1 if self.instantiate_calls == 1 else 2
                return [
                    CypherCandidate(
                        candidate_id=f"cand_l8_{idx}",
                        skeleton_id=f"sk_l8_{idx}",
                        cypher=f"MATCH (n:Person) WITH n MATCH (n)-[:WORKS_ON]->(p:Project) RETURN n.name AS name, count(p) AS total LIMIT {idx + 1}",
                        query_types=["SUBQUERY"],
                        structure_family="with_stage_aggregate",
                        generation_mode="llm_refine",
                        difficulty="L8",
                    )
                    for idx in range(count)
                ]

        class PassingValidationService:
            def validate(self, candidate, schema, config, tugraph_config):
                return ValidatedSample(
                    candidate=candidate,
                    validation=ValidationResult(
                        syntax=True,
                        schema=True,
                        type_value=True,
                        query_type_valid=True,
                        family_valid=True,
                        runtime=True,
                        result_sanity=True,
                        difficulty_valid=True,
                        plan_valid=True,
                    ),
                    result_signature=ResultSignature(
                        columns=["name", "total"],
                        column_types=["string", "integer"],
                        row_count=1,
                        result_preview=[{"name": candidate.candidate_id, "total": 1}],
                        result_rows=[{"name": candidate.candidate_id, "total": 1}],
                    ),
                    classified_difficulty="L8",
                )

        class PassingQuestionService:
            def generate_batch(self, validated, schema, llm_config, max_variants):
                samples = []
                for item in validated:
                    sample = fake_qa_sample()
                    sample.id = f"qa_{item.candidate.candidate_id}"
                    sample.question_canonical_zh = f"{item.candidate.candidate_id} 对应的 L8 问题？"
                    sample.question_variants_zh = [sample.question_canonical_zh]
                    sample.question_variant_styles = ["natural_short"]
                    sample.cypher = item.candidate.cypher
                    sample.cypher_normalized = item.candidate.cypher.lower()
                    sample.query_types = item.candidate.query_types
                    sample.difficulty = "L8"
                    sample.answer = [{"name": item.candidate.candidate_id, "total": 1}]
                    sample.result_signature = item.result_signature
                    sample.provenance["generation_mode"] = item.candidate.generation_mode
                    sample.provenance["structure_family"] = item.candidate.structure_family
                    samples.append(sample)
                return samples

        class PassingRoundtripService:
            def check(self, sample, llm_config):
                return True, sample.question_variants_zh, sample.question_variant_styles

        generation_service = TargetShortfallGenerationService()
        with TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            orchestrator = Orchestrator(
                job_store=JobStore(root=temp_root / "job-reports"),
                artifact_store=ArtifactStore(root=temp_root / "artifacts"),
                schema_compatibility_service=SchemaCompatibilityService(graph_executor=FakeGraphExecutor()),
                generation_service=generation_service,
                validation_service=PassingValidationService(),
                question_service=PassingQuestionService(),
                roundtrip_service=PassingRoundtripService(),
            )
            job = orchestrator.create_job(
                JobRequest(
                    mode="online",
                    schema_input=schema,
                    output_config={"target_qa_count": 2, "difficulty_targets": {"L8": 2}},
                    tugraph_source={"type": "inline"},
                    tugraph_config={"base_url": None, "username": None, "password": None, "graph": None},
                )
            )

            completed = orchestrator.run_job(job.job_id)

            self.assertEqual(completed.status.value, "completed")
            self.assertEqual(completed.metrics["selection"]["difficulty_shortfalls"], {})
            self.assertGreaterEqual(generation_service.instantiate_calls, 2)

    def test_job_keeps_retrying_until_requested_difficulty_targets_are_filled(self) -> None:
        schema_path = Path(__file__).parent / "fixtures" / "schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        class DelayedTargetGenerationService:
            def __init__(self) -> None:
                self.instantiate_calls = 0

            def instantiate_candidates_from_specs(self, schema, specs, limits, model_config=None):
                self.instantiate_calls += 1
                if self.instantiate_calls < 4:
                    return [
                        CypherCandidate(
                            candidate_id=f"cand_l1_{self.instantiate_calls}",
                            skeleton_id=f"sk_l1_{self.instantiate_calls}",
                            cypher=(
                                "MATCH (n:Person) "
                                f"RETURN n.name AS name LIMIT {self.instantiate_calls}"
                            ),
                            query_types=["LOOKUP"],
                            structure_family="lookup_node_return",
                            generation_mode="llm_refine",
                            difficulty="L1",
                        )
                    ]
                return [
                    CypherCandidate(
                        candidate_id="cand_l8_final",
                        skeleton_id="sk_l8_final",
                        cypher=(
                            "MATCH (n:Person) WITH n "
                            "MATCH (n)-[:WORKS_ON]->(p:Project) "
                            "RETURN n.name AS name, count(p) AS total LIMIT 1"
                        ),
                        query_types=["SUBQUERY"],
                        structure_family="with_stage_aggregate",
                        generation_mode="llm_refine",
                        difficulty="L8",
                    )
                ]

        class PassingValidationService:
            def validate(self, candidate, schema, config, tugraph_config):
                difficulty = candidate.difficulty
                rows = (
                    [{"name": candidate.candidate_id, "total": 1}]
                    if difficulty == "L8"
                    else [{"name": candidate.candidate_id}]
                )
                return ValidatedSample(
                    candidate=candidate,
                    validation=ValidationResult(
                        syntax=True,
                        schema=True,
                        type_value=True,
                        query_type_valid=True,
                        family_valid=True,
                        runtime=True,
                        result_sanity=True,
                        difficulty_valid=True,
                        plan_valid=True,
                    ),
                    result_signature=ResultSignature(
                        columns=list(rows[0].keys()),
                        column_types=["string", "integer"] if difficulty == "L8" else ["string"],
                        row_count=1,
                        result_preview=rows,
                        result_rows=rows,
                    ),
                    classified_difficulty=difficulty,
                )

        class PassingQuestionService:
            def generate_batch(self, validated, schema, llm_config, max_variants):
                samples = []
                for item in validated:
                    sample = fake_qa_sample()
                    sample.id = f"qa_{item.candidate.candidate_id}"
                    sample.question_canonical_zh = f"{item.candidate.candidate_id} 对应的问题？"
                    sample.question_variants_zh = [sample.question_canonical_zh]
                    sample.question_variant_styles = ["natural_short"]
                    sample.cypher = item.candidate.cypher
                    sample.cypher_normalized = item.candidate.cypher.lower()
                    sample.query_types = item.candidate.query_types
                    sample.difficulty = item.candidate.difficulty
                    sample.answer = item.result_signature.result_rows
                    sample.result_signature = item.result_signature
                    sample.provenance["generation_mode"] = item.candidate.generation_mode
                    sample.provenance["structure_family"] = item.candidate.structure_family
                    samples.append(sample)
                return samples

        class PassingRoundtripService:
            def check(self, sample, llm_config):
                return True, sample.question_variants_zh, sample.question_variant_styles

        generation_service = DelayedTargetGenerationService()
        with TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            orchestrator = Orchestrator(
                job_store=JobStore(root=temp_root / "job-reports"),
                artifact_store=ArtifactStore(root=temp_root / "artifacts"),
                schema_compatibility_service=SchemaCompatibilityService(graph_executor=FakeGraphExecutor()),
                generation_service=generation_service,
                validation_service=PassingValidationService(),
                question_service=PassingQuestionService(),
                roundtrip_service=PassingRoundtripService(),
            )
            job = orchestrator.create_job(
                JobRequest(
                    mode="online",
                    schema_input=schema,
                    output_config={"target_qa_count": 3, "difficulty_targets": {"L1": 2, "L8": 1}},
                    tugraph_source={"type": "inline"},
                    tugraph_config={"base_url": None, "username": None, "password": None, "graph": None},
                )
            )

            completed = orchestrator.run_job(job.job_id)

            self.assertEqual(completed.status.value, "completed")
            self.assertGreaterEqual(generation_service.instantiate_calls, 4)
            self.assertEqual(completed.metrics["sample_count"], 3)
            self.assertEqual(completed.metrics["selection"]["difficulty_shortfalls"], {})
            self.assertEqual(completed.metrics["selection"]["selected_difficulty_counts"], {"L1": 2, "L8": 1})
            release_rows = [
                json.loads(line)
                for line in Path(completed.artifacts["releases"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(release_rows), 3)

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

    def test_failed_job_can_be_rerun_cleanly(self) -> None:
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
            generation_service.fail_attempts = 0
            completed = orchestrator.run_job(failed.job_id)

            self.assertEqual(completed.status.value, "completed")
            self.assertEqual(completed.errors, [])
            self.assertTrue(completed.artifacts["releases"])
            self.assertGreaterEqual(completed.metrics.get("sample_count", 0), 1)

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

    def test_roundtrip_sends_question_and_cypher_to_second_llm_check(self) -> None:
        sample = fake_qa_sample()
        sample.provenance["canonical_pass"] = "true"
        sample.provenance["canonical_checks"] = json.dumps(
            {
                "filters": True,
                "temporal": True,
                "ordering": True,
                "topk_limit": True,
                "aggregation_grouping": True,
                "path_hops": True,
                "comparison": True,
                "return_target": True,
            },
            ensure_ascii=False,
        )
        sample.provenance["approved_styles"] = json.dumps(sample.question_variant_styles, ensure_ascii=False)
        service = RoundtripService(model_gateway=InspectingRoundtripGateway())

        ok, variants, styles = service.check(sample, fake_gateway_config())

        self.assertTrue(ok)
        self.assertEqual(variants, sample.question_variants_zh)
        self.assertEqual(styles, sample.question_variant_styles)
        self.assertEqual(service.model_gateway.calls[-1], "question_cypher_consistency")
        self.assertEqual(service.model_gateway.last_kwargs["question"], sample.question_canonical_zh)
        self.assertEqual(service.model_gateway.last_kwargs["cypher"], sample.cypher)

    def test_roundtrip_rejects_when_second_llm_check_fails(self) -> None:
        service = RoundtripService(model_gateway=RejectingConsistencyGateway())
        sample = fake_qa_sample()
        sample.provenance["canonical_pass"] = "true"
        sample.provenance["canonical_checks"] = json.dumps(
            {
                "filters": True,
                "temporal": True,
                "ordering": True,
                "topk_limit": True,
                "aggregation_grouping": True,
                "path_hops": True,
                "comparison": True,
                "return_target": True,
            },
            ensure_ascii=False,
        )
        sample.provenance["approved_styles"] = json.dumps(sample.question_variant_styles, ensure_ascii=False)

        ok, variants, styles = service.check(sample, fake_gateway_config())

        self.assertFalse(ok)
        self.assertEqual(variants, sample.question_variants_zh)
        self.assertEqual(styles, sample.question_variant_styles)

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
        sample.cypher = "MATCH (n:NetworkElement) RETURN n ORDER BY n.score DESC LIMIT 5"

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

    def test_roundtrip_does_not_force_unordered_limit_into_question(self) -> None:
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

        self.assertTrue(ok)

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

    def test_online_mode_uses_batched_question_generation_calls(self) -> None:
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
            batch_calls = fake_gateway.calls.count("question_bundle_batch")
            consistency_calls = fake_gateway.calls.count("question_bundle_consistency")
            self.assertEqual(question_bundle_calls, 0)
            self.assertGreaterEqual(batch_calls, 1)
            self.assertEqual(consistency_calls, 0)
            self.assertNotIn("question_variants", fake_gateway.calls)
            self.assertNotIn("cypher_canonical_question", fake_gateway.calls)

    def test_online_mode_uses_llm_cypher_generation_with_template_fallback(self) -> None:
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
            self.assertGreaterEqual(fake_gateway.calls.count("cypher_candidate_batch"), 1)
            self.assertEqual(fake_gateway.calls.count("cypher_candidate_bundle"), 0)

    def test_query_plan_budget_stays_tight_for_small_targets(self) -> None:
        orchestrator = Orchestrator()
        self.assertEqual(orchestrator._query_plan_target_count(1, 32), 3)
        self.assertEqual(orchestrator._query_plan_target_count(2, 32), 4)
        self.assertEqual(orchestrator._query_plan_target_count(5, 32), 8)

    def test_online_skeleton_budget_keeps_all_requested_difficulty_targets(self) -> None:
        schema_path = Path(__file__).parent / "fixtures" / "schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        request = JobRequest(
            mode="online",
            schema_input=schema,
            output_config={
                "difficulty_targets": {"L1": 2, "L2": 2, "L3": 1, "L4": 1, "L5": 1, "L6": 1, "L7": 1, "L8": 1}
            },
            tugraph_source={"type": "inline"},
            tugraph_config={"base_url": None, "username": None, "password": None, "graph": None},
        )
        orchestrator = Orchestrator()
        normalized_schema = orchestrator.schema_service.normalize(schema)
        limits = orchestrator._effective_limits(request)

        specs = orchestrator.coverage_service.build_specs(
            schema=normalized_schema,
            limits=limits,
            target_qa_count=orchestrator._query_plan_target_count(request.output_config.target_qa_count, limits.max_skeletons),
            difficulty_targets=request.output_config.difficulty_targets,
            diversity_key="difficulty-target-budget",
        )

        self.assertEqual(len(specs), request.output_config.target_qa_count)
        self.assertEqual(
            {level: sum(1 for spec in specs if spec.target_difficulty == level) for level in request.output_config.difficulty_targets},
            request.output_config.difficulty_targets,
        )

    def test_shortlist_validated_samples_limits_llm_work_for_single_target(self) -> None:
        orchestrator = Orchestrator()
        samples = []
        for idx in range(8):
            candidate = CypherCandidate(
                cypher=f"MATCH (n) RETURN n LIMIT {idx + 1}",
                skeleton_id=f"sk_{idx}",
                query_types=["LOOKUP" if idx < 4 else "FILTER"],
                structure_family="lookup_node_return" if idx < 4 else "filter_single_condition",
                generation_mode="llm_refine" if idx % 3 == 0 else ("llm_direct" if idx % 2 == 0 else "template"),
                difficulty=f"L{min(idx + 1, 8)}",
            )
            samples.append(
                ValidatedSample(
                    candidate=candidate,
                    validation=ValidationResult(
                        syntax=True,
                        schema=True,
                        type_value=True,
                        runtime=True,
                        result_sanity=True,
                        query_type_valid=True,
                        family_valid=True,
                        difficulty_valid=True,
                        plan_valid=True,
                    ),
                    result_signature=ResultSignature(row_count=idx + 1),
                    classified_difficulty=f"L{min(idx + 1, 8)}",
                )
            )

        shortlisted = orchestrator._shortlist_validated_samples(samples, target_qa_count=1)

        self.assertEqual(len(shortlisted), 3)
        self.assertTrue(any(sample.candidate.generation_mode == "llm_refine" for sample in shortlisted))

    def test_release_selection_prefers_non_empty_answers(self) -> None:
        orchestrator = Orchestrator()
        empty = fake_qa_sample()
        empty.answer = []
        empty.result_signature.row_count = 0
        empty.difficulty = "L7"
        empty.provenance["generation_mode"] = "llm_refine"

        full = fake_qa_sample()
        full.id = "qa_non_empty"
        full.question_canonical_zh = "列出前5个网络设备。"
        full.cypher = "MATCH (n:NetworkElement) RETURN n LIMIT 5"
        full.cypher_normalized = "match (n:networkelement) return n limit 5"
        full.answer = [{"n": "device-1"}]
        full.result_signature.row_count = 1
        full.difficulty = "L4"
        full.provenance["generation_mode"] = "template"

        selected, meta = orchestrator._select_release_batch([empty, full], {"questions": set(), "cyphers": set()}, 1)

        self.assertEqual(meta["selected_count"], 1)
        self.assertEqual(selected[0].id, "qa_non_empty")

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

    def test_model_gateway_logs_llm_attempt_duration_without_sensitive_payload(self) -> None:
        with TemporaryDirectory() as tempdir:
            gateway = ModelGateway(module_logs=ModuleLogStore(root=Path(tempdir)))

            class FakeClient:
                def post(self, *args, **kwargs):
                    request = httpx.Request("POST", "https://example.com/chat/completions")
                    return httpx.Response(
                        200,
                        request=request,
                        json={"choices": [{"message": {"content": "ok"}}]},
                    )

            gateway._client = FakeClient()
            output = gateway.generate_text(
                "question_bundle_batch",
                fake_gateway_config(),
                requests_json=json.dumps(
                    [
                        {
                            "request_id": "val_secret",
                            "cypher": "MATCH (n:Secret) RETURN n",
                        }
                    ],
                    ensure_ascii=False,
                ),
            )
            log_path = Path(tempdir) / "openai.log"
            log_lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

        self.assertEqual(output, "ok")
        self.assertEqual([line["operation"] for line in log_lines], ["llm_request_started", "llm_request_completed"])
        completed = log_lines[-1]
        self.assertEqual(completed["status"], "success")
        self.assertEqual(completed["request_body"]["prompt_name"], "question_bundle_batch")
        self.assertEqual(completed["request_body"]["batch_size"], 1)
        self.assertEqual(completed["attempt"], 1)
        self.assertGreaterEqual(completed["duration_ms"], 0)
        raw_log = json.dumps(log_lines, ensure_ascii=False)
        self.assertNotIn("MATCH (n:Secret)", raw_log)
        self.assertNotIn("Authorization", raw_log)

    def test_model_gateway_logs_retryable_attempt_before_success(self) -> None:
        with TemporaryDirectory() as tempdir:
            gateway = ModelGateway(module_logs=ModuleLogStore(root=Path(tempdir)))

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
            log_path = Path(tempdir) / "openai.log"
            log_lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

        self.assertEqual(output, "ok")
        self.assertEqual(fake_client.calls, 2)
        self.assertEqual(
            [line["operation"] for line in log_lines],
            ["llm_request_started", "llm_request_retry", "llm_request_started", "llm_request_completed"],
        )
        retry = log_lines[1]
        self.assertEqual(retry["status"], "retry")
        self.assertEqual(retry["attempt"], 1)
        self.assertEqual(retry["response_body"]["status_code"], 429)
        self.assertEqual(log_lines[-1]["attempt"], 2)

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

    def test_generation_service_does_not_double_quote_string_literals(self) -> None:
        from app.domain.generation.service import GenerationService
        from app.domain.models import CanonicalSchemaSpec, CypherSkeleton, GenerationLimits

        schema = CanonicalSchemaSpec(
            node_types=["NetworkElement"],
            node_properties={"NetworkElement": {"id": "STRING"}},
            value_catalog={"NetworkElement.id": ["sample"]},
        )
        skeleton = CypherSkeleton(
            skeleton_id="lookup_entity_detail_l2_01",
            query_types=["LOOKUP"],
            structure_family="lookup_entity_detail",
            pattern_template="MATCH (n:{node}) WHERE n.{property} = {value} RETURN n LIMIT 5",
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

        self.assertIn("= 'sample'", candidates[0].cypher)
        self.assertNotIn("''sample''", candidates[0].cypher)

    def test_generation_service_batch_prompt_does_not_double_quote_string_literals(self) -> None:
        from app.domain.generation.service import GenerationService
        from app.domain.models import CanonicalSchemaSpec, CypherSkeleton, GenerationLimits, QueryPlan

        schema = CanonicalSchemaSpec(
            node_types=["NetworkElement"],
            node_properties={"NetworkElement": {"id": "STRING"}},
            value_catalog={"NetworkElement.id": ["sample"]},
        )
        skeleton = CypherSkeleton(
            skeleton_id="comparison_l6_01",
            query_types=["COMPARISON"],
            structure_family="attribute_comparison",
            pattern_template="MATCH (a:{node}), (b:{node}) WHERE a.{property} <> b.{property} RETURN a.{property} AS left_value, b.{property} AS right_value LIMIT 5",
            slots={},
            difficulty_floor="L6",
        )
        gateway = InspectingBatchGateway()
        service = GenerationService(model_gateway=gateway)

        service.instantiate_candidates(
            schema,
            [skeleton],
            GenerationLimits(max_skeletons=1, max_candidates_per_skeleton=1, max_variants_per_question=1),
            model_config=fake_gateway_config(),
            query_plans=[
                QueryPlan(
                    query_type="COMPARISON",
                    structure_family="attribute_comparison",
                    difficulty="L6",
                    bindings={"node": "NetworkElement", "target": "NetworkElement", "target2": "NetworkElement", "target3": "NetworkElement", "edge": "RELATED_TO", "edge2": "RELATED_TO", "edge3": "RELATED_TO", "property": "id", "property2": "id", "property3": "id", "value": "sample", "value2": "sample", "node_l": "a", "target_l": "b", "target2_l": "c", "target3_l": "d", "markers": {"comparison": True}},
                    required_semantics={"comparison": True},
                )
            ],
        )

        requests = json.loads(gateway.last_batch_kwargs["requests_json"])
        self.assertEqual(requests[0]["slot_bindings"]["value"], "sample")

    def test_generation_service_uses_llm_only_for_complex_plans(self) -> None:
        service = GenerationService(model_gateway=FakeModelGateway())

        simple_skeleton = CypherSkeleton(
            skeleton_id="lookup_l1_01",
            query_types=["LOOKUP"],
            structure_family="lookup_node_return",
            pattern_template="MATCH (n:{node}) RETURN n LIMIT 5",
            slots={},
            difficulty_floor="L1",
        )
        complex_skeleton = CypherSkeleton(
            skeleton_id="path_l6_01",
            query_types=["PATH"],
            structure_family="variable_length_path",
            pattern_template="MATCH p=({node_l}:{node})-[:{edge}*1..3]->({target_l}:{target}) RETURN p LIMIT 5",
            slots={},
            difficulty_floor="L6",
        )
        simple_plan = QueryPlan(
            query_type="LOOKUP",
            structure_family="lookup_node_return",
            difficulty="L1",
            bindings={},
            required_semantics={},
        )
        complex_plan = QueryPlan(
            query_type="PATH",
            structure_family="variable_length_path",
            difficulty="L6",
            bindings={},
            required_semantics={"variable_length": True, "min_hops": 2},
        )

        self.assertFalse(service._should_use_llm_candidate_generation(simple_skeleton, simple_plan))
        self.assertTrue(service._should_use_llm_candidate_generation(complex_skeleton, complex_plan))

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
