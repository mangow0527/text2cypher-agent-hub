from __future__ import annotations

import unittest

from app.domain.agent.models import AgentDecision, AgentRun, AgentRunStatus, CandidateChange, RootCause, ValidationSummary
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


class FakeRepairAgentRuntime:
    def __init__(self) -> None:
        self.calls = []

    def create_review_run_from_legacy_apply(
        self,
        qa_id: str,
        suggestion: str,
        knowledge_types: list[str] | None,
    ) -> AgentRun:
        self.calls.append((qa_id, suggestion, knowledge_types))
        return AgentRun(
            run_id="krun_legacy_001",
            qa_id=qa_id,
            goal="Review legacy knowledge repair request before applying it.",
            root_cause=RootCause(
                type="legacy_repair_apply",
                summary=suggestion,
                suggested_fix=suggestion,
                evidence=["Created from /api/knowledge/repairs/apply"],
            ),
            status=AgentRunStatus.NEEDS_REVIEW,
            candidate_changes=[
                CandidateChange(
                    doc_type="business_knowledge",
                    section="Terminology Mapping",
                    target_key="legacy_business",
                    new_content="- 补充协议版本映射",
                    confidence=0.9,
                    duplicate_checked=True,
                    conflict_checked=True,
                )
            ],
            validation=ValidationSummary(
                prompt_package_built=True,
                before_after_improved=True,
                remaining_risks=["等待人工批准后落库"],
            ),
            decision=AgentDecision(action="human_review", reason="Converted from legacy apply path."),
        )


class RepairWorkflowServiceTest(unittest.TestCase):
    def test_apply_creates_agent_review_run_and_skips_direct_write(self) -> None:
        repair_service = FakeRepairService()
        gateway = FakeQARedispatchGateway()
        runtime = FakeRepairAgentRuntime()
        service = RepairWorkflowService(repair_service, gateway, repair_agent_runtime=runtime)

        result = service.apply("qa_001", "补充协议版本映射", ["business_knowledge"])

        self.assertEqual(repair_service.calls, [])
        self.assertEqual(runtime.calls, [("qa_001", "补充协议版本映射", ["business_knowledge"])])
        self.assertEqual(gateway.calls, [])
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["agent_run"].run_id, "krun_legacy_001")
        self.assertEqual(result["agent_run"].status, AgentRunStatus.NEEDS_REVIEW)
        self.assertEqual(result["redispatch"]["trace_id"], "qa_001")
        self.assertEqual(result["redispatch"]["status"], "skipped")
        self.assertEqual(result["redispatch"]["dispatch"]["reason"], "knowledge_agent_no_longer_redispatches_qa")


if __name__ == "__main__":
    unittest.main()
