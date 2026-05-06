import unittest

from pydantic import ValidationError

from app.domain.agent.models import (
    AgentAction,
    AgentConstraints,
    AgentDecision,
    AgentRun,
    AgentRunStatus,
    CandidateChange,
    RootCause,
)


class AgentModelsTest(unittest.TestCase):
    def test_agent_action_requires_tool_for_tool_call(self) -> None:
        action = AgentAction(
            action="tool_call",
            tool_name="retrieve_knowledge",
            arguments={"query": "协议版本 所属网元"},
            reason_summary="先检索相关知识",
        )
        self.assertEqual(action.tool_name, "retrieve_knowledge")

        with self.assertRaises(ValidationError):
            AgentAction(action="tool_call", reason_summary="missing tool")

    def test_final_action_cannot_claim_completed(self) -> None:
        with self.assertRaises(ValidationError):
            AgentAction(action="final", status="completed", reason_summary="unsafe")

    def test_candidate_change_doc_type_is_constrained(self) -> None:
        CandidateChange(
            doc_type="few_shot",
            section="Reference Examples",
            target_key="k",
            new_content="Question: q\nCypher: MATCH (n) RETURN n",
        )
        with self.assertRaises(ValidationError):
            CandidateChange(doc_type="not_a_doc", section="s", target_key="k", new_content="x")

    def test_agent_run_holds_structured_outputs(self) -> None:
        run = AgentRun(
            qa_id="qa_001",
            goal="repair",
            root_cause=RootCause(type="missing_path_rule", summary="s", suggested_fix="f"),
            constraints=AgentConstraints(max_steps=8),
            status=AgentRunStatus.CREATED,
            decision=AgentDecision(action="continue", reason="new run"),
        )
        self.assertEqual(run.qa_id, "qa_001")
        self.assertEqual(run.status, AgentRunStatus.CREATED)


if __name__ == "__main__":
    unittest.main()
