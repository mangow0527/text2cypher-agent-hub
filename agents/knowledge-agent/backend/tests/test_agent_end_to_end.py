import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.agent.controller import LLMController
from app.domain.agent.memory import MemoryManager
from app.domain.agent.models import AgentConstraints, AgentRunStatus, RootCause, ValidationSummary
from app.domain.agent.policy import PolicyGuard
from app.domain.agent.run_store import AgentRunStore
from app.domain.agent.runtime import RepairAgentRuntime
from app.domain.agent.tool_registry import ToolRegistry
from app.domain.agent.tools import ProposePatchTool


class SequenceGateway:
    def __init__(self) -> None:
        self.responses = [
            '{"action":"tool_call","tool_name":"propose_patch","arguments":{"suggestion":"补充 few-shot","knowledge_types":["few_shot"]},"reason_summary":"生成候选 patch"}',
            '{"action":"request_human_review","reason_summary":"候选 patch 已生成，需要人工审核"}',
        ]

    def generate_text(self, prompt_name, model_config, **kwargs):
        return self.responses.pop(0)


class FakeRepairService:
    def propose(self, suggestion, knowledge_types):
        return [
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


class AgentEndToEndTest(unittest.TestCase):
    def test_fake_llm_chooses_tool_then_requests_review(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            registry = ToolRegistry()
            registry.register(ProposePatchTool(FakeRepairService()))
            runtime = RepairAgentRuntime(
                AgentRunStore(Path(tmp_dir) / "runs"),
                LLMController(SequenceGateway()),
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

            first = runtime.step(run.run_id)
            first.validation = ValidationSummary(before_after_improved=True)
            runtime.run_store.save(first)
            second = runtime.step(run.run_id)

            self.assertEqual(first.candidate_changes[0].target_key, "k")
            self.assertEqual(second.status, AgentRunStatus.NEEDS_REVIEW)


if __name__ == "__main__":
    unittest.main()
