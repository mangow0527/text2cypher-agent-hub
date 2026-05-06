import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.agent.memory import MemoryManager
from app.domain.agent.models import AgentConstraints, AgentRunStatus, CandidateChange, RootCause, ValidationSummary
from app.domain.agent.policy import PolicyGuard
from app.domain.agent.run_store import AgentRunStore
from app.domain.agent.runtime import RepairAgentRuntime


class FakeRepairService:
    def __init__(self) -> None:
        self.applied = []

    def apply_candidates(self, patches, suggestion):
        self.applied.append((patches, suggestion))
        return [{"doc_type": patches[0]["doc_type"], "section": patches[0]["section"], "before": "old", "after": "new"}]


class FakeRedispatchGateway:
    def __init__(self) -> None:
        self.calls = []

    def redispatch(self, qa_id: str):
        raise AssertionError("knowledge repair approval must not redispatch QA cases")


class ApprovalLifecycleTest(unittest.TestCase):
    def test_approve_applies_writes_memory_and_completes_without_redispatch(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            memory = MemoryManager(Path(tmp_dir) / "memory")
            repair = FakeRepairService()
            redispatch = FakeRedispatchGateway()
            runtime = RepairAgentRuntime(
                AgentRunStore(Path(tmp_dir) / "runs"),
                None,
                None,
                memory,
                PolicyGuard(),
                repair,
            )
            run = runtime.create_run(
                "qa_001",
                "repair",
                RootCause(type="missing_few_shot", summary="s", suggested_fix="f"),
                AgentConstraints(auto_apply=False),
            )
            run.candidate_changes = [
                CandidateChange(
                    doc_type="few_shot",
                    section="Reference Examples",
                    target_key="k",
                    new_content="Question: q\nCypher: MATCH (n) RETURN n",
                    risk="low",
                    confidence=0.95,
                    duplicate_checked=True,
                    conflict_checked=True,
                )
            ]
            run.validation = ValidationSummary(before_after_improved=True)
            runtime.run_store.save(run)

            completed = runtime.approve(run.run_id)

            self.assertEqual(completed.status, AgentRunStatus.COMPLETED)
            self.assertEqual(repair.applied[0][0][0]["target_key"], "k")
            self.assertEqual(redispatch.calls, [])
            self.assertEqual(completed.validation.redispatch_status, "skipped")
            self.assertTrue(memory.search_repair_memory("missing_few_shot"))

    def test_auto_apply_uses_same_apply_memory_completion_path_without_redispatch(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            memory = MemoryManager(Path(tmp_dir) / "memory")
            repair = FakeRepairService()
            redispatch = FakeRedispatchGateway()
            runtime = RepairAgentRuntime(
                AgentRunStore(Path(tmp_dir) / "runs"),
                None,
                None,
                memory,
                PolicyGuard(),
                repair,
            )
            run = runtime.create_run(
                "qa_001",
                "repair",
                RootCause(type="missing_few_shot", summary="s", suggested_fix="f"),
                AgentConstraints(auto_apply=True),
            )
            run.candidate_changes = [
                CandidateChange(
                    doc_type="few_shot",
                    section="Reference Examples",
                    target_key="k",
                    new_content="Question: q\nCypher: MATCH (n) RETURN n",
                    risk="low",
                    confidence=0.95,
                    duplicate_checked=True,
                    conflict_checked=True,
                )
            ]
            run.validation = ValidationSummary(before_after_improved=True)

            completed = runtime.maybe_auto_apply(run)

            self.assertEqual(completed.status, AgentRunStatus.COMPLETED)
            self.assertEqual(redispatch.calls, [])
            self.assertEqual(completed.validation.redispatch_status, "skipped")


if __name__ == "__main__":
    unittest.main()
