import unittest
from typing import Optional
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.entrypoints.api.main import app


class FakeRepairService:
    def apply(self, suggestion: str, knowledge_types: Optional[list[str]]) -> list[dict[str, str]]:
        return [
            {
                "doc_type": "business_knowledge",
                "section": "Terminology Mapping",
                "before": "[id: protocol_version_mapping]\n- 旧内容",
                "after": "[id: protocol_version_mapping]\n- 新内容",
            }
        ]


class ApiContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_prompt_package_contract(self) -> None:
        response = self.client.post(
            "/api/knowledge/rag/prompt-package",
            json={"id": "q_001", "question": "查询协议版本为v2.0的隧道所属网元"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["id"], "q_001")
        self.assertIn("prompt", response.json())

    def test_apply_repair_contract(self) -> None:
        with patch("app.entrypoints.api.main.repair_service", FakeRepairService()):
            response = self.client.post(
                "/api/knowledge/repairs/apply",
                json={"id": "q_001", "suggestion": "补充协议版本映射"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["changes"][0]["doc_type"], "business_knowledge")
        self.assertIn("before", response.json()["changes"][0])
        self.assertIn("after", response.json()["changes"][0])
