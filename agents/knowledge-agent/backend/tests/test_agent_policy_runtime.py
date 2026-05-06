import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel

from app.domain.agent.memory import MemoryManager
from app.domain.agent.models import AgentAction, AgentConstraints, AgentRunStatus, CandidateChange, RootCause, ValidationSummary
from app.domain.agent.policy import PolicyGuard
from app.domain.agent.run_store import AgentRunStore
from app.domain.agent.runtime import RepairAgentRuntime
from app.domain.agent.tool_registry import AgentTool, ToolRegistry
from app.errors import AppError


class FakeController:
    def __init__(self, action: AgentAction) -> None:
        self.action = action

    def decide_next_action(self, context, memory, tools):
        return self.action


class PatchInput(BaseModel):
    suggestion: str


class PatchTool(AgentTool):
    name = "propose_patch"
    description = "Return a candidate patch for runtime mapping tests."
    input_model = PatchInput

    def execute(self, arguments: PatchInput) -> dict:
        return {
            "candidate_changes": [
                {
                    "doc_type": "few_shot",
                    "section": "Reference Examples",
                    "target_key": "k",
                    "new_content": "Question: q\nCypher: MATCH (n) RETURN n",
                    "risk": "low",
                    "confidence": 0.95,
                    "duplicate_checked": True,
                    "conflict_checked": True,
                }
            ]
        }


class PolicyRuntimeTest(unittest.TestCase):
    def test_auto_apply_guard_differs_from_human_approval_guard(self) -> None:
        guard = PolicyGuard()
        run = self._run(auto_apply=False)
        change = CandidateChange(
            doc_type="few_shot",
            section="Reference Examples",
            target_key="k",
            new_content="Question: q\nCypher: MATCH (n) RETURN n",
            risk="low",
            confidence=0.95,
            duplicate_checked=True,
            conflict_checked=True,
        )
        validation = ValidationSummary(before_after_improved=True)

        with self.assertRaises(AppError):
            guard.assert_can_auto_apply(run, [change], validation)
        guard.assert_can_apply_after_human_approval(run, [change], validation)

    def test_runtime_maps_candidate_observation_into_run(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            registry = ToolRegistry()
            registry.register(PatchTool())
            runtime = RepairAgentRuntime(
                AgentRunStore(Path(tmp_dir) / "runs"),
                FakeController(
                    AgentAction(
                        action="tool_call",
                        tool_name="propose_patch",
                        arguments={"suggestion": "fix"},
                        reason_summary="生成候选 patch",
                    )
                ),
                registry,
                MemoryManager(Path(tmp_dir) / "memory"),
                PolicyGuard(),
            )
            run = runtime.create_run(
                "qa_001",
                "repair",
                RootCause(type="missing_few_shot", summary="s", suggested_fix="f"),
                AgentConstraints(allowed_tools=["propose_patch"]),
            )

            stepped = runtime.step(run.run_id)

            self.assertEqual(stepped.status, AgentRunStatus.RUNNING)
            self.assertEqual(stepped.candidate_changes[0].target_key, "k")

    def _run(self, auto_apply: bool):
        from app.domain.agent.models import AgentRun

        return AgentRun(
            qa_id="qa_001",
            goal="repair",
            root_cause=RootCause(type="missing_few_shot", summary="s", suggested_fix="f"),
            constraints=AgentConstraints(auto_apply=auto_apply),
        )


if __name__ == "__main__":
    unittest.main()
