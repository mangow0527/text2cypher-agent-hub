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
        self.calls.append(qa_id)
        return {
            "trace_id": qa_id,
            "qa_id": qa_id,
            "status": "success",
            "attempt": 1,
            "max_attempts": 3,
            "dispatch": {"status": "success"},
        }


class RepairWorkflowServiceTest(unittest.TestCase):
    def test_apply_triggers_qa_redispatch_for_same_id(self) -> None:
        repair_service = FakeRepairService()
        gateway = FakeQARedispatchGateway()
        service = RepairWorkflowService(repair_service, gateway)

        result = service.apply("qa_001", "补充协议版本映射", ["business_knowledge"])

        self.assertEqual(repair_service.calls, [("补充协议版本映射", ["business_knowledge"])])
        self.assertEqual(gateway.calls, ["qa_001"])
        self.assertEqual(result["changes"][0]["doc_type"], "business_knowledge")
        self.assertEqual(result["redispatch"]["trace_id"], "qa_001")
        self.assertEqual(result["redispatch"]["status"], "success")


if __name__ == "__main__":
    unittest.main()
