import json
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import httpx
import yaml

from services.cypher_generator_agent.app.intent_recognition import (
    EmbeddingIntentRecognizer,
    HybridIntentRecognizer,
    InMemoryEmbeddingStore,
    IntentEmbeddingSample,
    JsonlEmbeddingStore,
    LocalTextEmbedder,
    RuleBasedIntentRecognizer,
    SentenceTransformerTextEmbedder,
    build_text_embedder,
    extract_query_structural_features,
    write_embedding_index,
    get_hybrid_intent_recognizer,
)
from services.cypher_generator_agent.app.intent_vector_store import (
    FallbackEmbeddingStore,
    RagIntentEmbeddingStore,
    RagIntentSearchError,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
INTENT_RESOURCE_DIR = REPO_ROOT / "services/cypher_generator_agent/resources/intent"


class RuleBasedIntentRecognizerTest(unittest.TestCase):
    def test_recognize_returns_best_rule_match_from_resource_files(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        result = recognizer.recognize("查询服务所使用隧道的 ID 和名称")

        self.assertEqual("record_retrieval_query", result.primary_intent)
        self.assertEqual("related_record_query", result.secondary_intent)
        self.assertEqual(0.835, result.confidence)
        self.assertEqual("rule", result.source)
        self.assertEqual("accept", result.decision)

    def test_recognize_returns_fallback_when_no_rule_matches(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        result = recognizer.recognize("帮我看看这个业务是不是正常")

        self.assertIsNone(result.primary_intent)
        self.assertIsNone(result.secondary_intent)
        self.assertEqual(0.0, result.confidence)
        self.assertEqual("rule", result.source)
        self.assertEqual("fallback_embedding", result.decision)

    def test_rule_stage_rejects_broad_lookup_patterns(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        broad_questions = [
            "查看前5条链路的管理状态",
            "查询前5条链路的标识、名称和带宽。",
            "网络中有哪些设备？",
        ]

        for question in broad_questions:
            with self.subTest(question=question):
                result = recognizer.recognize(question)
                self.assertIsNone(result.primary_intent)
                self.assertIsNone(result.secondary_intent)
                self.assertEqual("rule", result.source)
                self.assertEqual("fallback_embedding", result.decision)

    def test_rule_stage_keeps_distinct_count_as_metric(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        result = recognizer.recognize("统计不同厂商数量")

        self.assertEqual("metric_query", result.primary_intent)
        self.assertEqual("count_metric_query", result.secondary_intent)
        self.assertEqual("rule", result.source)
        self.assertEqual("accept", result.decision)

    def test_rule_stage_accepts_stable_related_count_as_metric(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        questions = [
            "统计服务使用的隧道数量",
            "查询服务使用的隧道数量",
        ]

        for question in questions:
            with self.subTest(question=question):
                result = recognizer.recognize(question)
                self.assertEqual("metric_query", result.primary_intent)
                self.assertEqual("count_metric_query", result.secondary_intent)
                self.assertEqual("rule", result.source)
                self.assertEqual("accept", result.decision)

    def test_rule_stage_prefers_numeric_metric_for_scalar_maximum(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        questions = [
            "查询隧道最大时延",
            "统计端口最大速率",
            "查询链路最小长度",
        ]

        for question in questions:
            with self.subTest(question=question):
                result = recognizer.recognize(question)
                self.assertEqual("metric_query", result.primary_intent)
                self.assertEqual("numeric_metric_query", result.secondary_intent)
                self.assertEqual("rule", result.source)
                self.assertEqual("accept", result.decision)

    def test_rule_stage_prefers_multi_metric_over_single_numeric_metric(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        questions = [
            "查询服务数量和平均带宽",
            "返回链路数量、最大带宽和平均带宽",
            "查询服务数量、平均带宽和最大带宽",
            "返回端口总数、up 端口数量和 down 端口数量",
        ]

        for question in questions:
            with self.subTest(question=question):
                result = recognizer.recognize(question)
                self.assertEqual("metric_query", result.primary_intent)
                self.assertEqual("multi_metric_query", result.secondary_intent)
                self.assertEqual("rule", result.source)
                self.assertEqual("accept", result.decision)

    def test_rule_stage_prefers_derived_metric_ranking_over_trend_when_ordered(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        result = recognizer.recognize("查询增长率最高的业务类型")

        self.assertEqual("ranking_query", result.primary_intent)
        self.assertEqual("derived_metric_ranking_query", result.secondary_intent)
        self.assertEqual("rule", result.source)
        self.assertEqual("accept", result.decision)

    def test_rule_stage_rejects_complex_detail_with_relation_filters(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        result = recognizer.recognize(
            "查询使用某种协议且被隧道穿过的网络元素的详细信息，包括标识、IP地址、位置、型号、名称、软件版本、类型和厂商，限制最多返回5条。"
        )

        self.assertIsNone(result.primary_intent)
        self.assertIsNone(result.secondary_intent)
        self.assertEqual("rule", result.source)
        self.assertEqual("fallback_embedding", result.decision)

    def test_rule_stage_prefers_set_difference_for_unassigned_relation(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        result = recognizer.recognize("查询未被任何服务使用的隧道")

        self.assertEqual("set_operation_query", result.primary_intent)
        self.assertEqual("set_difference_query", result.secondary_intent)
        self.assertEqual("rule", result.source)
        self.assertEqual("accept", result.decision)

    def test_rule_stage_accepts_named_entity_related_record_template(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        result = recognizer.recognize("查询设备A连接的端口")

        self.assertEqual("record_retrieval_query", result.primary_intent)
        self.assertEqual("related_record_query", result.secondary_intent)
        self.assertEqual("rule", result.source)
        self.assertEqual("accept", result.decision)

    def test_rule_stage_accepts_clear_field_detail_relation_and_path_templates(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )
        cases = [
            (
                "查看设备 Device_A 的完整配置信息",
                "record_retrieval_query",
                "entity_detail_query",
            ),
            (
                "查询端口的速率和运行状态字段",
                "record_retrieval_query",
                "attribute_projection_query",
            ),
            (
                "查询服务 S 使用了哪些隧道",
                "record_retrieval_query",
                "related_record_query",
            ),
            (
                "查询服务 S 到端口 P 的完整经过路径",
                "relationship_path_query",
                "path_trace_query",
            ),
            (
                "列出当前网络中的所有服务",
                "record_retrieval_query",
                "entity_list_query",
            ),
            (
                "返回所有链路的名称和管理状态",
                "record_retrieval_query",
                "attribute_projection_query",
            ),
        ]

        for question, primary_intent, secondary_intent in cases:
            with self.subTest(question=question):
                result = recognizer.recognize(question)
                self.assertEqual(primary_intent, result.primary_intent)
                self.assertEqual(secondary_intent, result.secondary_intent)
                self.assertEqual("rule", result.source)
                self.assertEqual("accept", result.decision)

    def test_rule_stage_rejects_complex_breakdown_with_relation_conditions(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        result = recognizer.recognize(
            "每种业务类型下，使用了至少一条隧道且配置了端口的所有业务，其端口总数分别是多少？"
        )

        self.assertIsNone(result.primary_intent)
        self.assertIsNone(result.secondary_intent)
        self.assertEqual("rule", result.source)
        self.assertEqual("fallback_embedding", result.decision)

    def test_rule_stage_rejects_complex_ranking_with_relation_conditions(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        result = recognizer.recognize(
            "查询ID为sample的网元，其速率等于1的端口所关联的光纤源隧道中，按带宽降序排列的前5个隧道的名称和带宽是多少？"
        )

        self.assertIsNone(result.primary_intent)
        self.assertIsNone(result.secondary_intent)
        self.assertEqual("rule", result.source)
        self.assertEqual("fallback_embedding", result.decision)

    def test_rule_stage_rejects_complex_path_with_relation_conditions(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        result = recognizer.recognize("查询前5条从网络设备经过端口连接到状态为down的光纤端口的路径。")

        self.assertIsNone(result.primary_intent)
        self.assertIsNone(result.secondary_intent)
        self.assertEqual("rule", result.source)
        self.assertEqual("fallback_embedding", result.decision)

    def test_rule_stage_accepts_stable_multi_metric_breakdown_template(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        result = recognizer.recognize("查询每个网络设备拥有的端口总数，以及它作为源端所连接的光纤端口数量。")

        self.assertEqual("breakdown_query", result.primary_intent)
        self.assertEqual("multi_metric_breakdown_query", result.secondary_intent)
        self.assertEqual("rule", result.source)
        self.assertEqual("accept", result.decision)

    def test_rule_stage_accepts_stable_grouped_multi_metric_template(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        result = recognizer.recognize(
            "按光纤长度分组，统计每种长度下光纤连接的目的端口总数，同时这些光纤的源端口数量各有多少？"
        )

        self.assertEqual("breakdown_query", result.primary_intent)
        self.assertEqual("multi_metric_breakdown_query", result.secondary_intent)
        self.assertEqual("rule", result.source)
        self.assertEqual("accept", result.decision)

    def test_rule_stage_prefers_share_breakdown_for_grouped_ratio(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        result = recognizer.recognize("按厂商统计设备占比")

        self.assertEqual("composition_query", result.primary_intent)
        self.assertEqual("share_breakdown_query", result.secondary_intent)
        self.assertEqual("rule", result.source)
        self.assertEqual("accept", result.decision)

    def test_rule_stage_prefers_segment_comparison_for_named_segments(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        result = recognizer.recognize("比较核心网和接入网的平均链路带宽")

        self.assertEqual("comparison_query", result.primary_intent)
        self.assertEqual("segment_comparison_query", result.secondary_intent)
        self.assertEqual("rule", result.source)
        self.assertEqual("accept", result.decision)

    def test_rule_stage_prefers_time_series_for_hourly_statistics(self) -> None:
        recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )

        result = recognizer.recognize("按小时统计端口 down 数量")

        self.assertEqual("trend_query", result.primary_intent)
        self.assertEqual("time_series_metric_query", result.secondary_intent)
        self.assertEqual("rule", result.source)
        self.assertEqual("accept", result.decision)

    def test_recognize_returns_fallback_when_top_rules_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            taxonomy_path = temp_path / "taxonomy.yaml"
            rules_path = temp_path / "rules.yaml"
            taxonomy_path.write_text(
                yaml.safe_dump(
                    {
                        "intents": [
                            {
                                "primary_intent": "first_primary",
                                "secondary_intents": [{"secondary_intent": "first_secondary"}],
                            },
                            {
                                "primary_intent": "second_primary",
                                "secondary_intents": [{"secondary_intent": "second_secondary"}],
                            },
                        ]
                    },
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
            rules_path.write_text(
                yaml.safe_dump(
                    {
                        "defaults": {
                            "source": "rule",
                            "decision": "accept",
                            "conflict_decision": "fallback_llm",
                        },
                        "rules": [
                            {
                                "rule_id": "first",
                                "primary_intent": "first_primary",
                                "secondary_intent": "first_secondary",
                                "confidence": 0.9,
                                "include_any": ["查询"],
                            },
                            {
                                "rule_id": "second",
                                "primary_intent": "second_primary",
                                "secondary_intent": "second_secondary",
                                "confidence": 0.9,
                                "include_any": ["查询"],
                            },
                        ],
                    },
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
            recognizer = RuleBasedIntentRecognizer.from_files(
                taxonomy_path=taxonomy_path,
                rules_path=rules_path,
            )

            result = recognizer.recognize("查询服务")

        self.assertIsNone(result.primary_intent)
        self.assertIsNone(result.secondary_intent)
        self.assertEqual(0.9, result.confidence)
        self.assertEqual("rule", result.source)
        self.assertEqual("fallback_llm", result.decision)

    def test_llm_fewshots_reference_known_taxonomy_intents(self) -> None:
        taxonomy = yaml.safe_load((INTENT_RESOURCE_DIR / "taxonomy.yaml").read_text(encoding="utf-8"))
        fewshots = yaml.safe_load((INTENT_RESOURCE_DIR / "llm_fewshots.yaml").read_text(encoding="utf-8"))
        known_intents = {
            (primary["primary_intent"], secondary["secondary_intent"])
            for primary in taxonomy["intents"]
            for secondary in primary["secondary_intents"]
        }

        referenced_intents = {
            (case["primary_intent"], case["secondary_intent"])
            for case in fewshots["few_shot_reasoning_examples"]
        }

        self.assertTrue(referenced_intents)
        self.assertLessEqual(referenced_intents, known_intents)

    def test_embedding_corpus_has_minimum_samples_per_secondary_intent(self) -> None:
        taxonomy = yaml.safe_load((INTENT_RESOURCE_DIR / "taxonomy.yaml").read_text(encoding="utf-8"))
        known_intents = {
            (primary["primary_intent"], secondary["secondary_intent"])
            for primary in taxonomy["intents"]
            for secondary in primary["secondary_intents"]
        }
        sample_counts = {intent: 0 for intent in known_intents}
        for line in (INTENT_RESOURCE_DIR / "embedding_corpus.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            sample = yaml.safe_load(line)
            intent = (sample["primary_intent"], sample["secondary_intent"])
            if intent in sample_counts:
                sample_counts[intent] += 1

        undersampled = {
            f"{primary}.{secondary}": count
            for (primary, secondary), count in sample_counts.items()
            if count < 5
        }

        self.assertEqual({}, undersampled)


class EmbeddingIntentRecognizerTest(unittest.TestCase):
    def test_extract_query_structural_features_distinguishes_limit_from_ranking(self) -> None:
        features = extract_query_structural_features("查询前5条链路的标识、名称和带宽。")
        ranking_features = extract_query_structural_features("按链路长度从长到短返回前 10 条链路")

        self.assertTrue(features.has_limit)
        self.assertFalse(features.has_order_signal)
        self.assertTrue(features.has_projection_fields)
        self.assertFalse(features.has_aggregation_signal)
        self.assertTrue(ranking_features.has_limit)
        self.assertTrue(ranking_features.has_order_signal)

    def test_embedding_gate_rejects_ranking_when_question_only_has_limit(self) -> None:
        recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "ranking_001",
                    "primary_intent": "ranking_query",
                    "secondary_intent": "attribute_ranking_query",
                    "text": "查询延迟最高的前 5 个隧道",
                },
                {
                    "id": "projection_001",
                    "primary_intent": "record_retrieval_query",
                    "secondary_intent": "attribute_projection_query",
                    "text": "查询隧道的时延字段",
                },
            ],
            valid_intents={
                ("ranking_query", "attribute_ranking_query"),
                ("record_retrieval_query", "attribute_projection_query"),
            },
            embedder=_StaticEmbedder(
                {
                    "查询前5条隧道的时延值": (1.0, 0.0),
                    "查询延迟最高的前 5 个隧道": (1.0, 0.0),
                    "查询隧道的时延字段": (0.96, 0.0),
                }
            ),
            accept_threshold=0.5,
            margin_threshold=0.0,
        )

        result = recognizer.recognize("查询前5条隧道的时延值")

        self.assertEqual("record_retrieval_query", result.primary_intent)
        self.assertEqual("attribute_projection_query", result.secondary_intent)
        self.assertEqual("embedding", result.source)
        self.assertEqual("accept", result.decision)

    def test_embedding_gate_rejects_numeric_metric_for_filter_condition(self) -> None:
        recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "numeric_metric_001",
                    "primary_intent": "metric_query",
                    "secondary_intent": "numeric_metric_query",
                    "text": "查询隧道最大时延",
                }
            ],
            valid_intents={("metric_query", "numeric_metric_query")},
            embedder=_StaticEmbedder(
                {
                    "查询时延等于1的隧道，最多返回10条记录": (1.0, 0.0),
                    "查询隧道最大时延": (1.0, 0.0),
                }
            ),
            accept_threshold=0.5,
        )

        result = recognizer.recognize("查询时延等于1的隧道，最多返回10条记录")
        diagnostic = recognizer.diagnose("查询时延等于1的隧道，最多返回10条记录")

        self.assertIsNone(result.primary_intent)
        self.assertIsNone(result.secondary_intent)
        self.assertEqual("embedding", result.source)
        self.assertEqual("fallback_llm", result.decision)
        self.assertEqual("structural_gate_rejected", diagnostic.reason)

    def test_embedding_gate_prefers_path_candidate_over_breakdown_for_path_question(self) -> None:
        recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "breakdown_001",
                    "primary_intent": "breakdown_query",
                    "secondary_intent": "multi_metric_breakdown_query",
                    "text": "查询每个设备拥有的端口总数和连接端口数量",
                },
                {
                    "id": "path_001",
                    "primary_intent": "relationship_path_query",
                    "secondary_intent": "path_trace_query",
                    "text": "查询网络设备经过端口连接到光纤端口的路径",
                },
            ],
            valid_intents={
                ("breakdown_query", "multi_metric_breakdown_query"),
                ("relationship_path_query", "path_trace_query"),
            },
            embedder=_StaticEmbedder(
                {
                    "查询前5条从网络设备经过端口连接到状态为down的光纤端口的路径": (1.0, 0.0),
                    "查询每个设备拥有的端口总数和连接端口数量": (1.0, 0.0),
                    "查询网络设备经过端口连接到光纤端口的路径": (0.97, 0.0),
                }
            ),
            accept_threshold=0.5,
            margin_threshold=0.0,
        )

        result = recognizer.recognize("查询前5条从网络设备经过端口连接到状态为down的光纤端口的路径")

        self.assertEqual("relationship_path_query", result.primary_intent)
        self.assertEqual("path_trace_query", result.secondary_intent)
        self.assertEqual("embedding", result.source)
        self.assertEqual("accept", result.decision)

    def test_embedding_gate_rejects_set_operation_for_plain_filter_condition(self) -> None:
        recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "set_membership_001",
                    "primary_intent": "set_operation_query",
                    "secondary_intent": "set_membership_filter_query",
                    "text": "查询使用高带宽隧道集合中任意隧道的服务",
                }
            ],
            valid_intents={("set_operation_query", "set_membership_filter_query")},
            embedder=_StaticEmbedder(
                {
                    "查询类型为MPLS-VPN的隧道信息，最多返回10条记录": (1.0, 0.0),
                    "查询使用高带宽隧道集合中任意隧道的服务": (1.0, 0.0),
                }
            ),
            accept_threshold=0.5,
        )

        result = recognizer.recognize("查询类型为MPLS-VPN的隧道信息，最多返回10条记录")

        self.assertIsNone(result.primary_intent)
        self.assertIsNone(result.secondary_intent)
        self.assertEqual("embedding", result.source)
        self.assertEqual("fallback_llm", result.decision)

    def test_embedding_gate_rejects_related_record_for_explicit_path_answer(self) -> None:
        recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "related_001",
                    "primary_intent": "record_retrieval_query",
                    "secondary_intent": "related_record_query",
                    "text": "查询光纤连接的目的端口",
                },
                {
                    "id": "path_001",
                    "primary_intent": "relationship_path_query",
                    "secondary_intent": "path_trace_query",
                    "text": "查询设备经过端口连接到光纤端口的路径",
                },
            ],
            valid_intents={
                ("record_retrieval_query", "related_record_query"),
                ("relationship_path_query", "path_trace_query"),
            },
            embedder=_StaticEmbedder(
                {
                    "查询前5条从网络设备经过端口连接到状态为down的光纤端口的路径": (1.0, 0.0),
                    "查询光纤连接的目的端口": (1.0, 0.0),
                    "查询设备经过端口连接到光纤端口的路径": (0.96, 0.0),
                }
            ),
            accept_threshold=0.5,
            margin_threshold=0.0,
        )

        result = recognizer.recognize("查询前5条从网络设备经过端口连接到状态为down的光纤端口的路径")

        self.assertEqual("relationship_path_query", result.primary_intent)
        self.assertEqual("path_trace_query", result.secondary_intent)
        self.assertEqual("embedding", result.source)
        self.assertEqual("accept", result.decision)

    def test_embedding_gate_rejects_trend_for_non_temporal_filter(self) -> None:
        recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "trend_001",
                    "primary_intent": "trend_query",
                    "secondary_intent": "time_series_metric_query",
                    "text": "按天统计链路数量趋势",
                }
            ],
            valid_intents={("trend_query", "time_series_metric_query")},
            embedder=_StaticEmbedder(
                {
                    "查找ID大于等于'sample'的隧道，限制返回10条记录": (1.0, 0.0),
                    "按天统计链路数量趋势": (1.0, 0.0),
                }
            ),
            accept_threshold=0.5,
        )

        result = recognizer.recognize("查找ID大于等于'sample'的隧道，限制返回10条记录")

        self.assertIsNone(result.primary_intent)
        self.assertIsNone(result.secondary_intent)
        self.assertEqual("embedding", result.source)
        self.assertEqual("fallback_llm", result.decision)

    def test_embedding_gate_accepts_union_when_question_asks_all_members_across_segments(self) -> None:
        recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "set_union_001",
                    "primary_intent": "set_operation_query",
                    "secondary_intent": "set_union_query",
                    "text": "查询两个厂商设备关联的所有端口",
                }
            ],
            valid_intents={("set_operation_query", "set_union_query")},
            embedder=_StaticEmbedder(
                {
                    "查询两个厂商设备关联的所有端口": (1.0, 0.0),
                }
            ),
            accept_threshold=0.5,
        )

        result = recognizer.recognize("查询两个厂商设备关联的所有端口")

        self.assertEqual("set_operation_query", result.primary_intent)
        self.assertEqual("set_union_query", result.secondary_intent)
        self.assertEqual("embedding", result.source)
        self.assertEqual("accept", result.decision)

    def test_embedding_gate_accepts_metric_comparison_for_who_has_more_question(self) -> None:
        recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "metric_comparison_001",
                    "primary_intent": "comparison_query",
                    "secondary_intent": "metric_comparison_query",
                    "text": "服务 A 和服务 B 谁使用的隧道更多",
                }
            ],
            valid_intents={("comparison_query", "metric_comparison_query")},
            embedder=_StaticEmbedder(
                {
                    "服务 A 和服务 B 谁使用的隧道更多": (1.0, 0.0),
                }
            ),
            accept_threshold=0.5,
        )

        result = recognizer.recognize("服务 A 和服务 B 谁使用的隧道更多")

        self.assertEqual("comparison_query", result.primary_intent)
        self.assertEqual("metric_comparison_query", result.secondary_intent)
        self.assertEqual("embedding", result.source)
        self.assertEqual("accept", result.decision)

    def test_embedding_gate_does_not_treat_order_by_as_breakdown_grouping(self) -> None:
        features = extract_query_structural_features("查询隧道按带宽降序排列的前5个名称和带宽")
        recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "breakdown_001",
                    "primary_intent": "breakdown_query",
                    "secondary_intent": "multi_metric_breakdown_query",
                    "text": "按光纤长度分组，统计目的端口总数和源端口数量",
                }
            ],
            valid_intents={("breakdown_query", "multi_metric_breakdown_query")},
            embedder=_StaticEmbedder(
                {
                    "查询隧道按带宽降序排列的前5个名称和带宽": (1.0, 0.0),
                    "按光纤长度分组，统计目的端口总数和源端口数量": (1.0, 0.0),
                }
            ),
            accept_threshold=0.5,
        )

        result = recognizer.recognize("查询隧道按带宽降序排列的前5个名称和带宽")

        self.assertTrue(features.has_order_signal)
        self.assertFalse(features.has_group_signal)
        self.assertIsNone(result.primary_intent)
        self.assertIsNone(result.secondary_intent)
        self.assertEqual("embedding", result.source)
        self.assertEqual("fallback_llm", result.decision)

    def test_build_text_embedder_returns_local_hash_embedder_by_default(self) -> None:
        embedder = build_text_embedder(provider="local_hash", dimensions=16)

        self.assertIsInstance(embedder, LocalTextEmbedder)
        self.assertEqual(16, embedder.dimensions)

    def test_sentence_transformer_embedder_uses_injected_model_encode_api(self) -> None:
        model = _FakeSentenceTransformerModel()
        embedder = SentenceTransformerTextEmbedder(model_name="fake-model", model=model)

        vector = embedder.embed("查询服务数量")

        self.assertEqual((0.6, 0.8), vector)
        self.assertEqual([("查询服务数量", True)], model.calls)

    def test_in_memory_embedding_store_returns_ranked_samples(self) -> None:
        samples = [
            IntentEmbeddingSample(
                id="count_metric_001",
                primary_intent="metric_query",
                secondary_intent="count_metric_query",
                text="count sample",
            ),
            IntentEmbeddingSample(
                id="breakdown_001",
                primary_intent="breakdown_query",
                secondary_intent="single_dimension_breakdown_query",
                text="breakdown sample",
            ),
        ]
        embedder = _StaticEmbedder(
            {
                "query": (1.0, 0.0),
                "count sample": (1.0, 0.0),
                "breakdown sample": (0.0, 1.0),
            }
        )
        store = InMemoryEmbeddingStore(samples=samples, embedder=embedder)

        matches = store.search(embedder.embed("query"), top_k=1)

        self.assertEqual(1, len(matches))
        self.assertEqual("count_metric_001", matches[0][0].id)
        self.assertEqual(1.0, matches[0][1])

    def test_embedding_recognizer_can_use_injected_embedding_store(self) -> None:
        samples = [
            IntentEmbeddingSample(
                id="count_metric_001",
                primary_intent="metric_query",
                secondary_intent="count_metric_query",
                text="count sample",
            )
        ]
        embedder = _StaticEmbedder({"query": (1.0, 0.0), "count sample": (1.0, 0.0)})
        store = InMemoryEmbeddingStore(samples=samples, embedder=embedder)
        recognizer = EmbeddingIntentRecognizer(
            samples=samples,
            valid_intents={("metric_query", "count_metric_query")},
            embedder=embedder,
            store=store,
            accept_threshold=0.5,
        )

        candidates = recognizer.retrieve_candidates("query", top_k=1)

        self.assertEqual(1, len(candidates))
        self.assertEqual("count_metric_001", candidates[0].sample_id)
        self.assertEqual(1.0, candidates[0].score)

    def test_embedding_recognizer_passes_question_text_to_embedding_store(self) -> None:
        sample = IntentEmbeddingSample(
            id="count_metric_001",
            primary_intent="metric_query",
            secondary_intent="count_metric_query",
            text="count sample",
        )
        embedder = _StaticEmbedder({"query text": (1.0, 0.0)})
        store = _QuestionAwareStore(sample)
        recognizer = EmbeddingIntentRecognizer(
            samples=[],
            valid_intents={("metric_query", "count_metric_query")},
            embedder=embedder,
            store=store,
            accept_threshold=0.5,
        )

        candidates = recognizer.retrieve_candidates("query text", top_k=1)

        self.assertEqual("query text", store.seen_query_text)
        self.assertEqual("count_metric_001", candidates[0].sample_id)

    def test_rag_intent_embedding_store_posts_question_and_returns_ranked_samples(self) -> None:
        requests: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(
                200,
                json={
                    "hits": [
                        {
                            "id": "related_record_001",
                            "text": "查询服务所使用隧道的 ID 和名称",
                            "primary_intent": "record_retrieval_query",
                            "secondary_intent": "related_record_query",
                            "score": 0.91,
                        }
                    ]
                },
            )

        store = RagIntentEmbeddingStore(
            base_url="http://rag-service",
            collection="nl2cypher_intent_examples_v1",
            taxonomy_version="v1",
            transport=httpx.MockTransport(handler),
        )

        matches = store.search((0.1, 0.2), top_k=3, query_text="服务使用了哪些隧道")

        self.assertEqual("服务使用了哪些隧道", requests[0]["question"])
        self.assertEqual(3, requests[0]["top_k"])
        self.assertEqual("nl2cypher_intent_examples_v1", requests[0]["collection"])
        self.assertEqual({"enabled": True, "taxonomy_version": "v1"}, requests[0]["filters"])
        self.assertEqual("related_record_001", matches[0][0].id)
        self.assertEqual("record_retrieval_query", matches[0][0].primary_intent)
        self.assertEqual("related_record_query", matches[0][0].secondary_intent)
        self.assertEqual(0.91, matches[0][1])

    def test_fallback_embedding_store_uses_local_store_when_rag_search_fails(self) -> None:
        sample = IntentEmbeddingSample(
            id="count_metric_001",
            primary_intent="metric_query",
            secondary_intent="count_metric_query",
            text="count sample",
        )
        fallback = InMemoryEmbeddingStore(
            samples=[sample],
            embedder=_StaticEmbedder({"query": (1.0, 0.0), "count sample": (1.0, 0.0)}),
        )
        store = FallbackEmbeddingStore(
            primary=_FailingEmbeddingStore(),
            fallback=fallback,
        )

        matches = store.search((1.0, 0.0), top_k=1, query_text="query")

        self.assertEqual("count_metric_001", matches[0][0].id)
        self.assertEqual(1.0, matches[0][1])

    def test_write_embedding_index_and_jsonl_store_round_trip_vectors(self) -> None:
        samples = [
            IntentEmbeddingSample(
                id="count_metric_001",
                primary_intent="metric_query",
                secondary_intent="count_metric_query",
                text="count sample",
            ),
            IntentEmbeddingSample(
                id="breakdown_001",
                primary_intent="breakdown_query",
                secondary_intent="single_dimension_breakdown_query",
                text="breakdown sample",
            ),
        ]
        embedder = _StaticEmbedder(
            {
                "query": (1.0, 0.0),
                "count sample": (1.0, 0.0),
                "breakdown sample": (0.0, 1.0),
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "intent_embedding_index.jsonl"

            written_count = write_embedding_index(
                samples=samples,
                embedder=embedder,
                output_path=index_path,
                provider="fake",
                model_name="fake-model",
            )
            records = [
                json.loads(line)
                for line in index_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            store = JsonlEmbeddingStore.from_path(index_path)

        matches = store.search(embedder.embed("query"), top_k=1)

        self.assertEqual(2, written_count)
        self.assertEqual("fake", records[0]["embedding_provider"])
        self.assertEqual("fake-model", records[0]["embedding_model"])
        self.assertEqual([1.0, 0.0], records[0]["vector"])
        self.assertEqual("count_metric_001", matches[0][0].id)
        self.assertEqual(1.0, matches[0][1])

    def test_retrieve_candidates_returns_ranked_top_k_evidence(self) -> None:
        recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "related_record_001",
                    "primary_intent": "record_retrieval_query",
                    "secondary_intent": "related_record_query",
                    "text": "查询服务所使用隧道的 ID 和名称",
                },
                {
                    "id": "path_trace_001",
                    "primary_intent": "relationship_path_query",
                    "secondary_intent": "path_trace_query",
                    "text": "查询服务到端口的完整路径",
                },
                {
                    "id": "count_metric_001",
                    "primary_intent": "metric_query",
                    "secondary_intent": "count_metric_query",
                    "text": "统计服务数量",
                },
            ],
            valid_intents={
                ("record_retrieval_query", "related_record_query"),
                ("relationship_path_query", "path_trace_query"),
                ("metric_query", "count_metric_query"),
            },
        )

        candidates = recognizer.retrieve_candidates("服务使用了哪些隧道", top_k=2)

        self.assertEqual(2, len(candidates))
        self.assertGreaterEqual(candidates[0].score, candidates[1].score)
        self.assertEqual("related_record_001", candidates[0].sample_id)
        self.assertEqual("record_retrieval_query", candidates[0].primary_intent)
        self.assertEqual("related_record_query", candidates[0].secondary_intent)
        self.assertEqual("查询服务所使用隧道的 ID 和名称", candidates[0].sample_text)

    def test_recognize_falls_back_when_top_candidates_are_close_and_disagree(self) -> None:
        recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "count_metric_001",
                    "primary_intent": "metric_query",
                    "secondary_intent": "count_metric_query",
                    "text": "count sample",
                },
                {
                    "id": "breakdown_001",
                    "primary_intent": "breakdown_query",
                    "secondary_intent": "single_dimension_breakdown_query",
                    "text": "breakdown sample",
                },
            ],
            valid_intents={
                ("metric_query", "count_metric_query"),
                ("breakdown_query", "single_dimension_breakdown_query"),
            },
            embedder=_StaticEmbedder(
                {
                    "ambiguous": (1.0, 0.0),
                    "count sample": (1.0, 0.0),
                    "breakdown sample": (0.99, 0.0),
                }
            ),
            accept_threshold=0.5,
            margin_threshold=0.05,
            consensus_top_k=3,
            consensus_min_count=2,
        )

        result = recognizer.recognize("ambiguous")

        self.assertIsNone(result.primary_intent)
        self.assertIsNone(result.secondary_intent)
        self.assertEqual("embedding", result.source)
        self.assertEqual("fallback_llm", result.decision)

    def test_diagnose_reports_top_k_margin_consensus_and_reason(self) -> None:
        recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "count_metric_001",
                    "primary_intent": "metric_query",
                    "secondary_intent": "count_metric_query",
                    "text": "count sample",
                },
                {
                    "id": "breakdown_001",
                    "primary_intent": "breakdown_query",
                    "secondary_intent": "single_dimension_breakdown_query",
                    "text": "breakdown sample",
                },
            ],
            valid_intents={
                ("metric_query", "count_metric_query"),
                ("breakdown_query", "single_dimension_breakdown_query"),
            },
            embedder=_StaticEmbedder(
                {
                    "ambiguous": (1.0, 0.0),
                    "count sample": (1.0, 0.0),
                    "breakdown sample": (0.99, 0.0),
                }
            ),
            accept_threshold=0.5,
            margin_threshold=0.05,
            consensus_top_k=3,
            consensus_min_count=2,
        )

        diagnostic = recognizer.diagnose("ambiguous", top_k=2)

        self.assertFalse(diagnostic.accepted)
        self.assertEqual("fallback_llm", diagnostic.decision)
        self.assertEqual("ambiguous_candidates", diagnostic.reason)
        self.assertEqual(1.0, diagnostic.top_score)
        self.assertEqual(0.01, diagnostic.margin)
        self.assertEqual(1, diagnostic.consensus_count)
        self.assertEqual("count_metric_001", diagnostic.candidates[0].sample_id)
        self.assertEqual("breakdown_001", diagnostic.candidates[1].sample_id)
        self.assertEqual(
            {
                "question": "ambiguous",
                "top_score": 1.0,
                "margin": 0.01,
                "consensus_count": 1,
                "consensus_min_count": 2,
                "accepted": False,
                "decision": "fallback_llm",
                "reason": "ambiguous_candidates",
                "candidates": [candidate.to_dict() for candidate in diagnostic.candidates],
            },
            diagnostic.to_dict(),
        )

    def test_recognize_accepts_close_candidates_when_they_support_same_intent(self) -> None:
        recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "count_metric_001",
                    "primary_intent": "metric_query",
                    "secondary_intent": "count_metric_query",
                    "text": "count sample 1",
                },
                {
                    "id": "count_metric_002",
                    "primary_intent": "metric_query",
                    "secondary_intent": "count_metric_query",
                    "text": "count sample 2",
                },
                {
                    "id": "breakdown_001",
                    "primary_intent": "breakdown_query",
                    "secondary_intent": "single_dimension_breakdown_query",
                    "text": "breakdown sample",
                },
            ],
            valid_intents={
                ("metric_query", "count_metric_query"),
                ("breakdown_query", "single_dimension_breakdown_query"),
            },
            embedder=_StaticEmbedder(
                {
                    "related count": (1.0, 0.0),
                    "count sample 1": (1.0, 0.0),
                    "count sample 2": (0.99, 0.0),
                    "breakdown sample": (0.98, 0.0),
                }
            ),
            accept_threshold=0.5,
            margin_threshold=0.05,
            consensus_top_k=3,
            consensus_min_count=2,
        )

        result = recognizer.recognize("related count")

        self.assertEqual("metric_query", result.primary_intent)
        self.assertEqual("count_metric_query", result.secondary_intent)
        self.assertEqual("embedding", result.source)
        self.assertEqual("accept", result.decision)

    def test_recognize_returns_intent_from_most_similar_local_sample(self) -> None:
        recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "related_record_001",
                    "primary_intent": "record_retrieval_query",
                    "secondary_intent": "related_record_query",
                    "text": "查询服务所使用隧道的 ID 和名称",
                },
                {
                    "id": "path_trace_001",
                    "primary_intent": "relationship_path_query",
                    "secondary_intent": "path_trace_query",
                    "text": "查询服务到端口的完整路径",
                },
            ],
            valid_intents={
                ("record_retrieval_query", "related_record_query"),
                ("relationship_path_query", "path_trace_query"),
            },
            accept_threshold=0.3,
        )

        result = recognizer.recognize("服务下面挂着哪些隧道的名称")

        self.assertEqual("record_retrieval_query", result.primary_intent)
        self.assertEqual("related_record_query", result.secondary_intent)
        self.assertEqual("embedding", result.source)
        self.assertEqual("accept", result.decision)

    def test_recognize_falls_back_to_llm_when_similarity_is_low(self) -> None:
        recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "related_record_001",
                    "primary_intent": "record_retrieval_query",
                    "secondary_intent": "related_record_query",
                    "text": "查询服务所使用隧道的 ID 和名称",
                }
            ],
            valid_intents={("record_retrieval_query", "related_record_query")},
            accept_threshold=0.95,
        )

        result = recognizer.recognize("帮我分析一下这个业务是不是正常")

        self.assertIsNone(result.primary_intent)
        self.assertIsNone(result.secondary_intent)
        self.assertEqual("embedding", result.source)
        self.assertEqual("fallback_llm", result.decision)


class _StaticEmbedder:
    def __init__(self, vectors: dict[str, tuple[float, ...]]) -> None:
        self.vectors = vectors

    def embed(self, text: str) -> tuple[float, ...]:
        return self.vectors[text]


class _QuestionAwareStore:
    def __init__(self, sample: IntentEmbeddingSample) -> None:
        self.sample = sample
        self.seen_query_text: Optional[str] = None

    def search(
        self,
        query_vector: tuple[float, ...],
        *,
        top_k: int,
        query_text: Optional[str] = None,
    ) -> list[tuple[IntentEmbeddingSample, float]]:
        self.seen_query_text = query_text
        return [(self.sample, 1.0)]


class _FailingEmbeddingStore:
    def search(
        self,
        query_vector: tuple[float, ...],
        *,
        top_k: int,
        query_text: Optional[str] = None,
    ) -> list[tuple[IntentEmbeddingSample, float]]:
        raise RagIntentSearchError("rag unavailable")


class _FakeSentenceTransformerModel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def encode(self, text: str, *, normalize_embeddings: bool) -> list[float]:
        self.calls.append((text, normalize_embeddings))
        return [0.6, 0.8]


class HybridIntentRecognizerTest(unittest.TestCase):
    def test_recognize_uses_embedding_when_rule_stage_has_no_match(self) -> None:
        rule_recognizer = RuleBasedIntentRecognizer.from_files(
            taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
            rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
        )
        embedding_recognizer = EmbeddingIntentRecognizer.from_samples(
            [
                {
                    "id": "related_record_001",
                    "primary_intent": "record_retrieval_query",
                    "secondary_intent": "related_record_query",
                    "text": "服务下面挂着哪些隧道的名称",
                }
            ],
            valid_intents={("record_retrieval_query", "related_record_query")},
            accept_threshold=0.3,
        )
        recognizer = HybridIntentRecognizer(
            rule_recognizer=rule_recognizer,
            embedding_recognizer=embedding_recognizer,
        )

        result = recognizer.recognize("服务下面挂着哪些隧道的名称")

        self.assertEqual("record_retrieval_query", result.primary_intent)
        self.assertEqual("related_record_query", result.secondary_intent)
        self.assertEqual("embedding", result.source)
        self.assertEqual("accept", result.decision)

    def test_configured_recognizer_handles_resource_property_variants(self) -> None:
        recognizer = self._configured_hybrid_recognizer()

        result = recognizer.recognize("帮我查一下所有服务的ID和名称。")

        self.assertEqual("record_retrieval_query", result.primary_intent)
        self.assertEqual("attribute_projection_query", result.secondary_intent)
        self.assertEqual("accept", result.decision)

    def test_configured_recognizer_handles_filtered_resource_property_variants(self) -> None:
        recognizer = self._configured_hybrid_recognizer()

        result = recognizer.recognize("Gold服务的ID和QoS。")

        self.assertEqual("record_retrieval_query", result.primary_intent)
        self.assertEqual("attribute_projection_query", result.secondary_intent)
        self.assertEqual("accept", result.decision)

    def test_configured_recognizer_handles_related_record_variants(self) -> None:
        recognizer = self._configured_hybrid_recognizer()

        result = recognizer.recognize("查询业务使用隧道对应的源网元设备。")

        self.assertEqual("record_retrieval_query", result.primary_intent)
        self.assertEqual("related_record_query", result.secondary_intent)
        self.assertEqual("accept", result.decision)

    def test_configured_recognizer_handles_path_trace_variants(self) -> None:
        recognizer = self._configured_hybrid_recognizer()

        result = recognizer.recognize("列出服务到端口的路径，包含服务名、隧道名、厂商和MAC地址。")

        self.assertEqual("relationship_path_query", result.primary_intent)
        self.assertEqual("path_trace_query", result.secondary_intent)
        self.assertEqual("accept", result.decision)

    def test_configured_recognizer_handles_spoken_path_trace_variants(self) -> None:
        recognizer = self._configured_hybrid_recognizer()

        result = recognizer.recognize("查询业务经过隧道和网元到达端口的路径详情。")

        self.assertEqual("relationship_path_query", result.primary_intent)
        self.assertEqual("path_trace_query", result.secondary_intent)
        self.assertEqual("accept", result.decision)

    def test_configured_recognizer_handles_topology_subgraph_variants(self) -> None:
        recognizer = self._configured_hybrid_recognizer()

        result = recognizer.recognize("查询设备A周边两跳拓扑。")

        self.assertEqual("relationship_path_query", result.primary_intent)
        self.assertEqual("topology_subgraph_query", result.secondary_intent)
        self.assertEqual("accept", result.decision)

    def test_configured_recognizer_handles_set_operation_variants(self) -> None:
        recognizer = self._configured_hybrid_recognizer()

        result = recognizer.recognize("查询服务A和服务B共同使用的隧道。")

        self.assertEqual("set_operation_query", result.primary_intent)
        self.assertEqual("set_intersection_query", result.secondary_intent)
        self.assertEqual("accept", result.decision)

    def test_configured_recognizer_handles_relation_existence_variants(self) -> None:
        recognizer = self._configured_hybrid_recognizer()

        result = recognizer.recognize("服务A是否使用了隧道B。")

        self.assertEqual("existence_query", result.primary_intent)
        self.assertEqual("relationship_existence_query", result.secondary_intent)
        self.assertEqual("accept", result.decision)

    def test_cached_hybrid_recognizer_uses_environment_embedding_settings(self) -> None:
        get_hybrid_intent_recognizer.cache_clear()
        with patch.dict(
            "os.environ",
            {
                "NL2CYPHER_INTENT_EMBEDDER_PROVIDER": "local_hash",
                "NL2CYPHER_INTENT_LOCAL_EMBEDDING_DIMENSIONS": "16",
                "NL2CYPHER_INTENT_ACCEPT_THRESHOLD": "0.4",
                "NL2CYPHER_INTENT_MARGIN_THRESHOLD": "0.07",
            },
        ):
            recognizer = get_hybrid_intent_recognizer()
        get_hybrid_intent_recognizer.cache_clear()

        self.assertIsInstance(recognizer.embedding_recognizer.embedder, LocalTextEmbedder)
        self.assertEqual(16, recognizer.embedding_recognizer.embedder.dimensions)
        self.assertEqual(0.4, recognizer.embedding_recognizer.accept_threshold)
        self.assertEqual(0.07, recognizer.embedding_recognizer.margin_threshold)

    def test_cached_hybrid_recognizer_can_use_prebuilt_embedding_index_from_environment(self) -> None:
        get_hybrid_intent_recognizer.cache_clear()
        samples = [
            IntentEmbeddingSample(
                id="count_metric_001",
                primary_intent="metric_query",
                secondary_intent="count_metric_query",
                text="count sample",
            )
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "intent_embedding_index.jsonl"
            write_embedding_index(
                samples=samples,
                embedder=_StaticEmbedder({"count sample": (1.0, 0.0)}),
                output_path=index_path,
                provider="fake",
                model_name="fake-model",
            )
            with patch.dict(
                "os.environ",
                {
                    "NL2CYPHER_INTENT_EMBEDDER_PROVIDER": "local_hash",
                    "NL2CYPHER_INTENT_LOCAL_EMBEDDING_DIMENSIONS": "2",
                    "NL2CYPHER_INTENT_EMBEDDING_INDEX": str(index_path),
                },
            ):
                recognizer = get_hybrid_intent_recognizer()
        get_hybrid_intent_recognizer.cache_clear()

        self.assertIsInstance(recognizer.embedding_recognizer.store, JsonlEmbeddingStore)
        self.assertEqual(["count_metric_001"], [sample.id for sample in recognizer.embedding_recognizer.samples])

    def test_cached_hybrid_recognizer_can_use_rag_vector_store_from_environment(self) -> None:
        get_hybrid_intent_recognizer.cache_clear()
        with patch.dict(
            "os.environ",
            {
                "NL2CYPHER_INTENT_EMBEDDING_STORE": "rag_vector",
                "NL2CYPHER_INTENT_RAG_SERVICE_URL": "http://rag-service",
                "NL2CYPHER_INTENT_RAG_COLLECTION": "nl2cypher_intent_examples_v1",
                "NL2CYPHER_INTENT_TAXONOMY_VERSION": "v1",
            },
        ):
            recognizer = get_hybrid_intent_recognizer()
        get_hybrid_intent_recognizer.cache_clear()

        self.assertIsInstance(recognizer.embedding_recognizer.store, FallbackEmbeddingStore)
        self.assertEqual([], recognizer.embedding_recognizer.samples)

    def _configured_hybrid_recognizer(self) -> HybridIntentRecognizer:
        return HybridIntentRecognizer(
            rule_recognizer=RuleBasedIntentRecognizer.from_files(
                taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
                rules_path=INTENT_RESOURCE_DIR / "rules.yaml",
            ),
            embedding_recognizer=EmbeddingIntentRecognizer.from_files(
                taxonomy_path=INTENT_RESOURCE_DIR / "taxonomy.yaml",
                corpus_path=INTENT_RESOURCE_DIR / "embedding_corpus.jsonl",
            ),
        )


if __name__ == "__main__":
    unittest.main()
