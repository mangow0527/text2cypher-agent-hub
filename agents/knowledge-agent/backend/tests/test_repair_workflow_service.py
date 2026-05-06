from __future__ import annotations

import unittest

from app.domain.knowledge.repair_workflow_service import RepairWorkflowService


class FakeRepairService:
    def __init__(self) -> None:
        self.calls = []

    def apply(self, suggestion: str, knowledge_types: list[str] | None) -> list[dict[str, str]]:
        self.calls.append((suggestion, knowledge_types))
        return [
            {
                "doc_type": "business_knowledge",
                "section": "Terminology Mapping",
                "before": "old",
                "after": "new",
            }
        ]


class FakeQARedispatchGateway:
    def __init__(self) -> None:
        self.calls = []

    def redispatch(self, qa_id: str) -> dict[str, object]:
        raise AssertionError("knowledge repair must not redispatch QA cases")


class RepairWorkflowServiceTest(unittest.TestCase):
    def test_apply_skips_qa_redispatch(self) -> None:
        repair_service = FakeRepairService()
        gateway = FakeQARedispatchGateway()
        service = RepairWorkflowService(repair_service, gateway)

        result = service.apply("qa_001", "补充协议版本映射", ["business_knowledge"])

        self.assertEqual(repair_service.calls, [("补充协议版本映射", ["business_knowledge"])])
        self.assertEqual(gateway.calls, [])
        self.assertEqual(result["changes"][0]["doc_type"], "business_knowledge")
        self.assertEqual(result["redispatch"]["trace_id"], "qa_001")
        self.assertEqual(result["redispatch"]["status"], "skipped")
        self.assertEqual(result["redispatch"]["dispatch"]["reason"], "knowledge_agent_no_longer_redispatches_qa")


if __name__ == "__main__":
    unittest.main()
