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
        self.calls.append(qa_id)
        return {"qa_id": qa_id, "status": "success", "attempt": 1, "max_attempts": 3, "dispatch": {"status": "success"}}


class ApprovalLifecycleTest(unittest.TestCase):
    def test_approve_applies_redispatches_writes_memory_and_completes(self) -> None:
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
                redispatch,
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
            self.assertEqual(redispatch.calls, ["qa_001"])
            self.assertTrue(memory.search_repair_memory("missing_few_shot"))

    def test_auto_apply_uses_same_apply_redispatch_memory_completion_path(self) -> None:
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
                redispatch,
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
            self.assertEqual(redispatch.calls, ["qa_001"])


if __name__ == "__main__":
    unittest.main()
