from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

import app.entrypoints.api.main as api_main
from app.domain.redispatch.service import SingleQARedispatchService
from app.errors import AppError
from app.logging import ModuleLogStore
from app.storage.redispatch_store import RedispatchAttemptStore


class FakeDispatcher:
    def __init__(self) -> None:
        self.row_calls: list[list[str]] = []

    def dispatch_release_rows(self, rows):
        self.row_calls.append([row["id"] for row in rows])
        return {
            "enabled": True,
            "status": "success",
            "host": "http://fake-host",
            "question_host": "http://fake-question-host",
            "golden_host": "http://fake-golden-host",
            "total": len(rows),
            "success": len(rows),
            "partial": 0,
            "failed": 0,
            "results": [{"id": row["id"], "status": "success"} for row in rows],
        }


class SingleQARedispatchServiceTests(unittest.TestCase):
    def test_redispatch_finds_matching_release_row_and_records_attempt(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            releases_root = root / "releases"
            releases_root.mkdir(parents=True, exist_ok=True)
            (releases_root / "job_001.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"id": "qa_001", "question": "q1", "cypher": "c1", "answer": [], "difficulty": "L1"}, ensure_ascii=False),
                        json.dumps({"id": "qa_002", "question": "q2", "cypher": "c2", "answer": [], "difficulty": "L2"}, ensure_ascii=False),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            dispatcher = FakeDispatcher()
            service = SingleQARedispatchService(
                dispatcher=dispatcher,
                releases_root=releases_root,
                attempt_store=RedispatchAttemptStore(root=root / "attempts"),
                module_logs=ModuleLogStore(root=root / "logs"),
            )

            result = service.redispatch("qa_002", trigger="repair")

            self.assertEqual(dispatcher.row_calls, [["qa_002"]])
            self.assertEqual(result["qa_id"], "qa_002")
            self.assertEqual(result["attempt"], 1)
            self.assertEqual(result["dispatch"]["status"], "success")

    def test_redispatch_rejects_fourth_attempt_for_same_qa_id(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            releases_root = root / "releases"
            releases_root.mkdir(parents=True, exist_ok=True)
            (releases_root / "job_001.jsonl").write_text(
                json.dumps({"id": "qa_001", "question": "q1", "cypher": "c1", "answer": [], "difficulty": "L1"}, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            service = SingleQARedispatchService(
                dispatcher=FakeDispatcher(),
                releases_root=releases_root,
                attempt_store=RedispatchAttemptStore(root=root / "attempts"),
                module_logs=ModuleLogStore(root=root / "logs"),
            )

            service.redispatch("qa_001", trigger="repair")
            service.redispatch("qa_001", trigger="repair")
            service.redispatch("qa_001", trigger="repair")

            with self.assertRaises(AppError) as ctx:
                service.redispatch("qa_001", trigger="repair")

        self.assertEqual(ctx.exception.code, "REDISPATCH_LIMIT_REACHED")

    def test_get_release_detail_finds_matching_qa_pair(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            releases_root = root / "releases"
            releases_root.mkdir(parents=True, exist_ok=True)
            (releases_root / "job_001.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"id": "qa_001", "question": "q1", "cypher": "c1", "answer": [], "difficulty": "L1"}, ensure_ascii=False),
                        json.dumps({"id": "qa_002", "question": "q2", "cypher": "c2", "answer": [{"x": 1}], "difficulty": "L2"}, ensure_ascii=False),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            service = SingleQARedispatchService(
                dispatcher=FakeDispatcher(),
                releases_root=releases_root,
                attempt_store=RedispatchAttemptStore(root=root / "attempts"),
                module_logs=ModuleLogStore(root=root / "logs"),
            )

            detail = service.get_detail("qa_002")

            self.assertEqual(detail["id"], "qa_002")
            self.assertEqual(detail["question"], "q2")
            self.assertEqual(detail["answer"], [{"x": 1}])
            self.assertEqual(detail["source_file"], "job_001.jsonl")
            self.assertEqual(detail["job_id"], "job_001")

    def test_delete_release_detail_removes_matching_qa_pair(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            releases_root = root / "releases"
            releases_root.mkdir(parents=True, exist_ok=True)
            release_path = releases_root / "job_001.jsonl"
            release_path.write_text(
                "\n".join(
                    [
                        json.dumps({"id": "qa_001", "question": "q1", "cypher": "c1", "answer": [], "difficulty": "L1"}, ensure_ascii=False),
                        json.dumps({"id": "qa_002", "question": "q2", "cypher": "c2", "answer": [], "difficulty": "L2"}, ensure_ascii=False),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            service = SingleQARedispatchService(
                dispatcher=FakeDispatcher(),
                releases_root=releases_root,
                attempt_store=RedispatchAttemptStore(root=root / "attempts"),
                module_logs=ModuleLogStore(root=root / "logs"),
            )

            result = service.delete("qa_001")

            self.assertEqual(result["qa_id"], "qa_001")
            self.assertEqual(result["deleted"], True)
            rows = [json.loads(line) for line in release_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual([row["id"] for row in rows], ["qa_002"])
            with self.assertRaises(AppError) as ctx:
                service.get_detail("qa_001")
            self.assertEqual(ctx.exception.code, "QA_RELEASE_NOT_FOUND")

    def test_qa_detail_and_delete_endpoints_use_release_store(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            releases_root = root / "releases"
            releases_root.mkdir(parents=True, exist_ok=True)
            (releases_root / "job_001.jsonl").write_text(
                json.dumps({"id": "qa_001", "question": "q1", "cypher": "c1", "answer": [{"x": 1}], "difficulty": "L1"}, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            service = SingleQARedispatchService(
                dispatcher=FakeDispatcher(),
                releases_root=releases_root,
                attempt_store=RedispatchAttemptStore(root=root / "attempts"),
                module_logs=ModuleLogStore(root=root / "logs"),
            )
            previous_service = api_main.single_qa_redispatch_service
            api_main.single_qa_redispatch_service = service
            try:
                client = TestClient(api_main.app)
                detail_response = client.get("/qa/qa_001")
                delete_response = client.delete("/qa/qa_001")
                missing_response = client.get("/qa/qa_001")
            finally:
                api_main.single_qa_redispatch_service = previous_service

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()["id"], "qa_001")
        self.assertEqual(detail_response.json()["job_id"], "job_001")
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()["deleted"], True)
        self.assertEqual(missing_response.status_code, 404)
        self.assertEqual(missing_response.json()["detail"]["code"], "QA_RELEASE_NOT_FOUND")

    def test_module_log_store_writes_module_scoped_file_with_trace_id(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ModuleLogStore(root=Path(tmpdir))

            store.append(
                module="dispatch",
                level="info",
                operation="qa_redispatch_completed",
                trace_id="qa_001",
                status="success",
                request_body={"id": "qa_001"},
                response_body={"status": "success"},
            )

            content = (Path(tmpdir) / "dispatch.log").read_text(encoding="utf-8")

        self.assertIn("qa_001", content)
        self.assertIn("qa_redispatch_completed", content)
        self.assertIn("success", content)


if __name__ == "__main__":
    unittest.main()
