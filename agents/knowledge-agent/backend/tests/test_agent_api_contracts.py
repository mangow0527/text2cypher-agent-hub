import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.domain.agent.models import AgentConstraints, AgentRun, AgentRunStatus, RootCause
from app.entrypoints.api.main import app
from app.integrations.qa_agent.redispatch_gateway import QARedispatchGateway


class FakeHTTPResponse:
    is_success = True
    status_code = 200

    def json(self):
        return {"id": "qa_001", "question": "q", "cypher": "MATCH (n) RETURN n"}

    def raise_for_status(self):
        return None


class FakeHTTPClient:
    def __init__(self) -> None:
        self.urls = []

    def get(self, url: str):
        self.urls.append(url)
        return FakeHTTPResponse()


class FakeRuntime:
    def list_runs(self, status=None):
        return [
            AgentRun(
                run_id="krun_review",
                qa_id="qa_001",
                goal="repair",
                root_cause=RootCause(type="missing_path_rule", summary="s", suggested_fix="f"),
                constraints=AgentConstraints(),
                status=AgentRunStatus.NEEDS_REVIEW,
            )
        ]

    def get_run(self, run_id):
        return AgentRun(
            run_id=run_id,
            qa_id="qa_001",
            goal="repair",
            root_cause=RootCause(type="missing_path_rule", summary="s", suggested_fix="f"),
            constraints=AgentConstraints(),
            status=AgentRunStatus.NEEDS_REVIEW,
        )

    def create_run(self, qa_id, goal, root_cause, constraints):
        return AgentRun(
            run_id="krun_001",
            qa_id=qa_id,
            goal=goal,
            root_cause=root_cause,
            constraints=constraints,
            status=AgentRunStatus.CREATED,
        )

    def step(self, run_id):
        return AgentRun(
            run_id=run_id,
            qa_id="qa_001",
            goal="repair",
            root_cause=RootCause(type="missing_path_rule", summary="s", suggested_fix="f"),
            constraints=AgentConstraints(),
            status=AgentRunStatus.RUNNING,
        )

    def approve(self, run_id):
        return AgentRun(
            run_id=run_id,
            qa_id="qa_001",
            goal="repair",
            root_cause=RootCause(type="missing_path_rule", summary="s", suggested_fix="f"),
            constraints=AgentConstraints(),
            status=AgentRunStatus.COMPLETED,
        )

    def reject(self, run_id, reason):
        return AgentRun(
            run_id=run_id,
            qa_id="qa_001",
            goal="repair",
            root_cause=RootCause(type="missing_path_rule", summary="s", suggested_fix="f"),
            constraints=AgentConstraints(),
            status=AgentRunStatus.REJECTED,
        )


class AgentApiContractsTest(unittest.TestCase):
    def test_qa_redispatch_gateway_get_detail_calls_qa_agent_detail_endpoint(self) -> None:
        client = FakeHTTPClient()
        gateway = QARedispatchGateway(client=client)

        detail = gateway.get_detail("qa_001")

        self.assertEqual(detail["id"], "qa_001")
        self.assertTrue(client.urls[0].endswith("/qa/qa_001"))

    def test_qa_gateway_does_not_expose_redispatch_sender(self) -> None:
        gateway = QARedispatchGateway(client=FakeHTTPClient())

        self.assertFalse(hasattr(gateway, "redispatch"))

    def test_create_step_approve_reject_contracts(self) -> None:
        client = TestClient(app)
        with patch("app.entrypoints.api.main.repair_agent_runtime", FakeRuntime()):
            create_response = client.post(
                "/api/knowledge/agent/repair-runs",
                json={
                    "qa_id": "qa_001",
                    "goal": "根据已知根因修复知识，并验证是否改善",
                    "root_cause": {"type": "missing_path_rule", "summary": "s", "suggested_fix": "f"},
                    "constraints": {"auto_apply": False, "max_steps": 12},
                },
            )
            step_response = client.post("/api/knowledge/agent/repair-runs/krun_001/step")
            approve_response = client.post("/api/knowledge/agent/repair-runs/krun_001/approve")
            reject_response = client.post(
                "/api/knowledge/agent/repair-runs/krun_001/reject",
                json={"reason": "manual"},
            )
            list_response = client.get("/api/knowledge/agent/repair-runs?status=needs_review")
            get_response = client.get("/api/knowledge/agent/repair-runs/krun_review")

        self.assertEqual(create_response.json()["run"]["status"], "created")
        self.assertEqual(step_response.json()["run"]["status"], "running")
        self.assertEqual(approve_response.json()["run"]["status"], "completed")
        self.assertEqual(reject_response.json()["run"]["status"], "rejected")
        self.assertEqual(list_response.json()["runs"][0]["status"], "needs_review")
        self.assertEqual(get_response.json()["run"]["run_id"], "krun_review")


if __name__ == "__main__":
    unittest.main()
