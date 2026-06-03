from __future__ import annotations

import asyncio
from typing import Any

import pytest

from services.testing_agent.app.models import QAGoldenRequest
from services.testing_agent.app.service import TestingAgentService


class _GoldenAfterSubmissionRepository:
    def __init__(self) -> None:
        self.state = "received_submission_only"
        self.golden = None

    def save_golden(self, request: QAGoldenRequest) -> None:
        self.golden = request

    def get_submission(self, qa_id: str) -> dict[str, Any]:
        return {
            "id": qa_id,
            "attempt_no": 1,
            "state": self.state,
            "evaluation": None,
        }

    def update_submission_state(self, qa_id: str, attempt_no: int, state: str) -> None:
        self.state = state

    def get_submission_attempt(self, qa_id: str, attempt_no: int) -> dict[str, Any]:
        return {
            "id": qa_id,
            "attempt_no": attempt_no,
            "state": self.state,
            "evaluation": None,
        }


class _SlowEvaluationService(TestingAgentService):
    def __init__(self, repository: _GoldenAfterSubmissionRepository) -> None:
        super().__init__(
            repository=repository,
            tugraph_client=None,
            grammar_checker=None,
            grammar_explainer=None,
            semantic_reviewer=None,
            settings=object(),
        )
        self.evaluated: list[tuple[str, int]] = []

    async def _evaluate_attempt(self, qa_id: str, attempt_no: int) -> None:
        await asyncio.sleep(0.05)
        self.evaluated.append((qa_id, attempt_no))


@pytest.mark.asyncio
async def test_golden_arriving_after_submission_schedules_evaluation_without_waiting() -> None:
    repository = _GoldenAfterSubmissionRepository()
    service = _SlowEvaluationService(repository)

    result = await service.ingest_golden(
        QAGoldenRequest(
            id="qa-ready",
            cypher="MATCH (n) RETURN n",
            answer=[],
            difficulty="L1",
        )
    )

    key = ("qa-ready", 1)
    assert result.status == "ready_to_evaluate"
    assert key in service._background_tasks
    assert service._background_tasks[key].done() is False

    await service._background_tasks[key]
    assert service.evaluated == [key]


class _FailingEvaluationRepository:
    def __init__(self) -> None:
        self.state = "ready_to_evaluate"
        self.evaluation = None
        self.issue_ticket = None

    def get_golden(self, qa_id: str) -> dict[str, Any]:
        return {
            "id": qa_id,
            "difficulty": "L1",
            "cypher": "MATCH (n) RETURN n",
            "answer": [],
        }

    def get_submission_attempt(self, qa_id: str, attempt_no: int) -> dict[str, Any]:
        return {
            "id": qa_id,
            "attempt_no": attempt_no,
            "state": self.state,
            "question": "查询网元",
            "generated_cypher": "MATCHH (n) RETURN n",
            "generation_run_id": "run-failed",
            "input_prompt_snapshot": "{}",
            "generation_status": "generated",
            "evaluation": self.evaluation,
        }

    def save_evaluation(self, qa_id: str, attempt_no: int, evaluation: Any) -> None:
        self.evaluation = evaluation.model_dump(mode="json")

    def save_issue_ticket(self, ticket: Any, *, attempt_no: int) -> None:
        self.issue_ticket = ticket

    def update_submission_state(self, qa_id: str, attempt_no: int, state: str) -> None:
        self.state = state


class _FailingGrammarChecker:
    def check(self, generated_cypher: str) -> tuple[int, str | None]:
        return 0, "syntax error"


class _GrammarExplainer:
    async def explain(self, generated_cypher: str, parser_error: str) -> str:
        return "语法错误"


@pytest.mark.asyncio
async def test_failed_evaluation_creates_local_issue_without_submitting_repair_ticket() -> None:
    repository = _FailingEvaluationRepository()
    service = TestingAgentService(
        repository=repository,
        tugraph_client=None,
        grammar_checker=_FailingGrammarChecker(),
        grammar_explainer=_GrammarExplainer(),
        semantic_reviewer=None,
        settings=object(),
    )

    await service._evaluate_attempt_unlocked("qa-failed", 1)

    assert repository.state == "issue_ticket_created"
    assert repository.issue_ticket is not None
    assert not hasattr(service, "_repair_tasks")
