import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from services.cypher_generator_agent.app.intent_evaluation import (
    evaluate_intent_recognizer,
    load_intent_eval_items,
    load_intent_pressure_items,
    summarize_intent_pressure,
)
from services.cypher_generator_agent.app.intent_recognition import IntentRecognitionResult


REPO_ROOT = Path(__file__).resolve().parents[3]
INTENT_RESOURCE_DIR = REPO_ROOT / "services/cypher_generator_agent/resources/intent"


class IntentEvaluationTest(unittest.TestCase):
    def test_load_intent_eval_items_accepts_question_and_expected_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = Path(temp_dir) / "intent_eval.jsonl"
            dataset_path.write_text(
                "\n".join(
                    [
                        '{"id":"case_001","question":"网络中有哪些设备","expected_primary_intent":"record_retrieval_query","expected_secondary_intent":"entity_list_query"}',
                        '{"id":"case_002","text":"统计服务数量","primary_intent":"metric_query","secondary_intent":"count_metric_query"}',
                    ]
                ),
                encoding="utf-8",
            )

            items = load_intent_eval_items(dataset_path)

        self.assertEqual(2, len(items))
        self.assertEqual("case_001", items[0].id)
        self.assertEqual("网络中有哪些设备", items[0].text)
        self.assertEqual("record_retrieval_query", items[0].primary_intent)
        self.assertEqual("entity_list_query", items[0].secondary_intent)
        self.assertEqual("统计服务数量", items[1].text)

    def test_evaluate_intent_recognizer_counts_accuracy_sources_and_confusions(self) -> None:
        class DummyRecognizer:
            def recognize(self, question: str) -> IntentRecognitionResult:
                if question == "right":
                    return IntentRecognitionResult(
                        primary_intent="metric_query",
                        secondary_intent="count_metric_query",
                        confidence=0.9,
                        source="rule",
                        decision="accept",
                    )
                return IntentRecognitionResult(
                    primary_intent="breakdown_query",
                    secondary_intent="single_dimension_breakdown_query",
                    confidence=0.8,
                    source="embedding",
                    decision="accept",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = Path(temp_dir) / "intent_eval.jsonl"
            dataset_path.write_text(
                "\n".join(
                    [
                        '{"id":"case_001","text":"right","primary_intent":"metric_query","secondary_intent":"count_metric_query"}',
                        '{"id":"case_002","text":"wrong","primary_intent":"metric_query","secondary_intent":"count_metric_query"}',
                    ]
                ),
                encoding="utf-8",
            )
            items = load_intent_eval_items(dataset_path)

        summary = evaluate_intent_recognizer(items, DummyRecognizer())

        self.assertEqual(2, summary.total)
        self.assertEqual(1, summary.correct)
        self.assertEqual(0.5, summary.accuracy)
        self.assertEqual({"rule": 1, "embedding": 1}, summary.source_counts)
        self.assertEqual({"accept": 2}, summary.decision_counts)
        self.assertEqual(1, len(summary.failures))
        self.assertEqual(
            {
                "expected": "metric_query.count_metric_query",
                "predicted": "breakdown_query.single_dimension_breakdown_query",
                "count": 1,
            },
            summary.confusion_pairs[0],
        )

    def test_resource_intent_eval_set_is_loadable_and_has_unique_ids(self) -> None:
        items = load_intent_eval_items(INTENT_RESOURCE_DIR / "eval_set.jsonl")

        self.assertGreaterEqual(len(items), 50)
        self.assertEqual(len(items), len({item.id for item in items}))

    def test_load_intent_pressure_items_accepts_qa_agent_question_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = Path(temp_dir) / "qa_index.jsonl"
            dataset_path.write_text(
                "\n".join(
                    [
                        '{"qa_id":"qa_001","difficulty":"L1","query_type":"LOOKUP","structure_family":"lookup_property_projection","question":"查看前5条链路的管理状态"}',
                        '{"id":"qa_002","text":"统计服务数量"}',
                    ]
                ),
                encoding="utf-8",
            )

            items = load_intent_pressure_items(dataset_path)

        self.assertEqual(2, len(items))
        self.assertEqual("qa_001", items[0].id)
        self.assertEqual("查看前5条链路的管理状态", items[0].text)
        self.assertEqual({"difficulty": "L1", "query_type": "LOOKUP", "structure_family": "lookup_property_projection"}, items[0].metadata)
        self.assertEqual("qa_002", items[1].id)

    def test_summarize_intent_pressure_counts_decisions_sources_and_intents(self) -> None:
        class DummyRecognizer:
            def recognize(self, question: str) -> IntentRecognitionResult:
                if question == "accepted":
                    return IntentRecognitionResult(
                        primary_intent="metric_query",
                        secondary_intent="count_metric_query",
                        confidence=0.9,
                        source="embedding",
                        decision="accept",
                    )
                return IntentRecognitionResult(
                    primary_intent=None,
                    secondary_intent=None,
                    confidence=0.2,
                    source="embedding",
                    decision="fallback_llm",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = Path(temp_dir) / "qa_index.jsonl"
            dataset_path.write_text(
                "\n".join(
                    [
                        '{"id":"qa_001","question":"accepted"}',
                        '{"id":"qa_002","question":"fallback"}',
                    ]
                ),
                encoding="utf-8",
            )
            items = load_intent_pressure_items(dataset_path)

        summary = summarize_intent_pressure(items, DummyRecognizer())

        self.assertEqual(2, summary.total)
        self.assertEqual({"embedding": 2}, summary.source_counts)
        self.assertEqual({"accept": 1, "fallback_llm": 1}, summary.decision_counts)
        self.assertEqual({"metric_query.count_metric_query": 1}, summary.accepted_intent_counts)
        self.assertEqual(2, len(summary.results))

    def test_evaluation_script_can_emit_embedding_diagnostics_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = Path(temp_dir) / "intent_eval.jsonl"
            dataset_path.write_text(
                '{"id":"case_001","text":"统计服务数量","primary_intent":"metric_query","secondary_intent":"count_metric_query"}',
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "tools/evaluate_intent_recognition.py",
                    "--dataset",
                    str(dataset_path),
                    "--mode",
                    "embedding",
                    "--include-diagnostics",
                    "--diagnostic-top-k",
                    "2",
                    "--json",
                ],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual("", completed.stderr)
        self.assertEqual(0, completed.returncode)
        payload = json.loads(completed.stdout)
        diagnostics = payload["embedding_diagnostics"]
        self.assertEqual(1, len(diagnostics))
        self.assertEqual("case_001", diagnostics[0]["id"])
        self.assertEqual("统计服务数量", diagnostics[0]["question"])
        self.assertLessEqual(len(diagnostics[0]["candidates"]), 2)
        self.assertIn(
            diagnostics[0]["reason"],
            {"accepted", "below_threshold", "ambiguous_candidates", "structural_gate_rejected"},
        )

    def test_evaluation_script_can_run_threshold_sweep_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = Path(temp_dir) / "intent_eval.jsonl"
            dataset_path.write_text(
                '{"id":"case_001","text":"统计服务数量","primary_intent":"metric_query","secondary_intent":"count_metric_query"}',
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "tools/evaluate_intent_recognition.py",
                    "--dataset",
                    str(dataset_path),
                    "--mode",
                    "embedding",
                    "--sweep",
                    "--accept-thresholds",
                    "0.3,0.8",
                    "--margin-thresholds",
                    "0.02",
                    "--json",
                ],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual("", completed.stderr)
        self.assertEqual(0, completed.returncode)
        payload = json.loads(completed.stdout)
        sweep_rows = payload["threshold_sweep"]
        self.assertEqual(2, len(sweep_rows))
        self.assertEqual([0.3, 0.8], [row["accept_threshold"] for row in sweep_rows])
        self.assertTrue(all("labeled_accuracy" in row for row in sweep_rows))

    def test_build_embedding_index_script_writes_local_jsonl_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "intent_embedding_index.jsonl"

            completed = subprocess.run(
                [
                    sys.executable,
                    "tools/build_intent_embedding_index.py",
                    "--embedder-provider",
                    "local_hash",
                    "--local-embedding-dimensions",
                    "16",
                    "--output",
                    str(output_path),
                    "--json",
                ],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            records = [
                json.loads(line)
                for line in output_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual("", completed.stderr)
        self.assertEqual(0, completed.returncode)
        payload = json.loads(completed.stdout)
        self.assertEqual(181, payload["written_count"])
        self.assertEqual(181, len(records))
        self.assertEqual("local_hash", records[0]["embedding_provider"])
        self.assertEqual(16, len(records[0]["vector"]))

    def test_evaluation_script_can_use_prebuilt_embedding_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "intent_embedding_index.jsonl"
            dataset_path = Path(temp_dir) / "intent_eval.jsonl"
            dataset_path.write_text(
                '{"id":"case_001","text":"查询服务所使用隧道的 ID 和名称","primary_intent":"record_retrieval_query","secondary_intent":"related_record_query"}',
                encoding="utf-8",
            )
            build_completed = subprocess.run(
                [
                    sys.executable,
                    "tools/build_intent_embedding_index.py",
                    "--embedder-provider",
                    "local_hash",
                    "--local-embedding-dimensions",
                    "128",
                    "--output",
                    str(index_path),
                    "--json",
                ],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            eval_completed = subprocess.run(
                [
                    sys.executable,
                    "tools/evaluate_intent_recognition.py",
                    "--dataset",
                    str(dataset_path),
                    "--mode",
                    "embedding",
                    "--embedding-index",
                    str(index_path),
                    "--json",
                ],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual("", build_completed.stderr)
        self.assertEqual(0, build_completed.returncode)
        self.assertEqual("", eval_completed.stderr)
        self.assertEqual(0, eval_completed.returncode)
        payload = json.loads(eval_completed.stdout)
        self.assertEqual(1, payload["labeled_eval"]["total"])
        self.assertEqual(1, payload["labeled_eval"]["correct"])


if __name__ == "__main__":
    unittest.main()
