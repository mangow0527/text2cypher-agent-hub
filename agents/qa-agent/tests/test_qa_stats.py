from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

import app.entrypoints.api.main as api_main
from app.reports.qa_stats import DIFFICULTY_LEVELS, QAStatsService


class QAStatsServiceTests(unittest.TestCase):
    def test_build_counts_generated_imported_and_difficulty_distribution(self) -> None:
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            self._write_jsonl(
                root / "job_alpha.jsonl",
                [
                    {"id": "qa_1", "difficulty": "L1"},
                    {"id": "qa_2", "difficulty": "L4"},
                ],
            )
            self._write_jsonl(root / "imp_beta.jsonl", [{"id": "qa_3", "difficulty": "L4"}])
            (root / "job_bad.jsonl").write_text('{"difficulty":"L9"}\n{not json}\n', encoding="utf-8")

            stats = QAStatsService(qa_root=root).build()

        self.assertEqual(stats["total_qa_pairs"], 3)
        self.assertEqual(stats["generated_qa_pairs"], 2)
        self.assertEqual(stats["imported_qa_pairs"], 1)
        self.assertEqual(stats["invalid_rows"], 2)
        self.assertEqual(stats["difficulty_distribution"]["L1"], 1)
        self.assertEqual(stats["difficulty_distribution"]["L4"], 2)
        self.assertEqual(set(stats["difficulty_distribution"].keys()), set(DIFFICULTY_LEVELS))
        self.assertEqual(len(stats["difficulty_definitions"]), 8)

    def test_qa_stats_endpoint_returns_service_payload(self) -> None:
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            self._write_jsonl(root / "job_alpha.jsonl", [{"id": "qa_1", "difficulty": "L2"}])
            previous_service = api_main.qa_stats_service
            api_main.qa_stats_service = QAStatsService(qa_root=root)
            try:
                response = TestClient(api_main.app).get("/qa/stats")
            finally:
                api_main.qa_stats_service = previous_service

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_qa_pairs"], 1)
        self.assertEqual(response.json()["difficulty_distribution"]["L2"], 1)

    def test_build_skips_hidden_files_and_counts_unreadable_files_as_invalid(self) -> None:
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            self._write_jsonl(root / "job_alpha.jsonl", [{"id": "qa_1", "difficulty": "L3"}])
            (root / "._job_alpha.jsonl").write_bytes(b"\xa3\x10not-utf8")
            (root / "job_broken.jsonl").write_bytes(b"\xa3\x10not-utf8")

            stats = QAStatsService(qa_root=root).build()

        self.assertEqual(stats["total_qa_pairs"], 1)
        self.assertEqual(stats["generated_qa_pairs"], 1)
        self.assertEqual(stats["files_processed"], 2)
        self.assertEqual(stats["invalid_rows"], 1)

    def _write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
