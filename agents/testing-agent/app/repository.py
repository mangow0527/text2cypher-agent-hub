from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .models import (
    EvaluationSummary,
    ExecutionResult,
    GeneratedCypherSubmissionRequest,
    ImprovementAssessment,
    IssueTicket,
    QAGoldenRequest,
    SaveSubmissionResult,
    SubmissionRecord,
)
from services.repair_agent.app.models import RepairIssueTicketResponse


@dataclass
class _PathSet:
    goldens: Path
    submissions: Path
    attempts: Path
    tickets: Path


class TestingRepository:
    __test__ = False

    def __init__(self, data_dir: str) -> None:
        paths = _PathSet(
            goldens=Path(data_dir) / "goldens",
            submissions=Path(data_dir) / "submissions",
            attempts=Path(data_dir) / "submission_attempts",
            tickets=Path(data_dir) / "issue_tickets",
        )
        self._paths = paths
        for path in (paths.goldens, paths.submissions, paths.attempts, paths.tickets):
            path.mkdir(parents=True, exist_ok=True)

    def save_golden(self, request: QAGoldenRequest) -> None:
        path = self._paths.goldens / f"{request.id}.json"
        record = {
            "id": request.id,
            "cypher": request.cypher,
            "answer": request.answer,
            "difficulty": request.difficulty,
            "updated_at": _utc_now(),
        }
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if any(existing[key] != record[key] for key in ("cypher", "answer", "difficulty")):
                raise ValueError(f"Golden answer conflict for id={request.id}")
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_golden(self, qa_id: str) -> Optional[Dict[str, Any]]:
        path = self._paths.goldens / f"{qa_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_submission(
        self,
        request: GeneratedCypherSubmissionRequest,
        *,
        state: str,
    ) -> SaveSubmissionResult:
        for existing in self.list_submission_attempts(request.id):
            if existing["generation_run_id"] != request.generation_run_id:
                continue
            if self._submission_matches(existing, request):
                return SaveSubmissionResult(
                    created=False,
                    attempt_no=int(existing["attempt_no"]),
                    record=SubmissionRecord.model_validate(existing),
                )
            raise ValueError(f"Submission conflict for id={request.id}")

        attempt_no = len(self.list_submission_attempts(request.id)) + 1
        record = SubmissionRecord(
            id=request.id,
            attempt_no=attempt_no,
            question=request.question,
            generation_run_id=request.generation_run_id,
            generated_cypher=request.generated_cypher,
            input_prompt_snapshot=request.input_prompt_snapshot,
            state=state,
            received_at=_utc_now(),
            updated_at=_utc_now(),
        )
        self._write_submission_record(record)
        return SaveSubmissionResult(created=True, attempt_no=attempt_no, record=record)

    def get_submission(self, qa_id: str) -> Optional[Dict[str, Any]]:
        path = self._paths.submissions / f"{qa_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def get_submission_attempt(self, qa_id: str, attempt_no: int) -> Optional[Dict[str, Any]]:
        path = self._paths.attempts / f"{qa_id}__attempt_{attempt_no}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_submission_attempts(self, qa_id: str) -> list[Dict[str, Any]]:
        attempts: list[Dict[str, Any]] = []
        for path in sorted(self._paths.attempts.glob(f"{qa_id}__attempt_*.json")):
            attempts.append(json.loads(path.read_text(encoding="utf-8")))
        return sorted(attempts, key=lambda item: int(item["attempt_no"]))

    def update_submission_state(self, qa_id: str, attempt_no: int, state: str) -> None:
        self._mutate_submission(
            qa_id,
            attempt_no,
            lambda record: record.update({"state": state, "updated_at": _utc_now()}),
        )

    def save_execution(self, qa_id: str, attempt_no: int, execution: ExecutionResult) -> None:
        self._mutate_submission(
            qa_id,
            attempt_no,
            lambda record: record.update(
                {
                    "execution": execution.model_dump(mode="json"),
                    "updated_at": _utc_now(),
                }
            ),
        )

    def save_evaluation(self, qa_id: str, attempt_no: int, evaluation: EvaluationSummary) -> None:
        self._mutate_submission(
            qa_id,
            attempt_no,
            lambda record: record.update(
                {
                    "evaluation": evaluation.model_dump(mode="json"),
                    "updated_at": _utc_now(),
                }
            ),
        )

    def save_issue_ticket(self, ticket: IssueTicket, *, attempt_no: int) -> None:
        path = self._paths.tickets / f"{ticket.ticket_id}.json"
        path.write_text(ticket.model_dump_json(indent=2), encoding="utf-8")
        self._mutate_submission(
            ticket.id,
            attempt_no,
            lambda record: record.update(
                {
                    "issue_ticket_id": ticket.ticket_id,
                    "updated_at": _utc_now(),
                }
            ),
        )

    def get_issue_ticket(self, ticket_id: str) -> Optional[IssueTicket]:
        path = self._paths.tickets / f"{ticket_id}.json"
        if not path.exists():
            return None
        return IssueTicket.model_validate_json(path.read_text(encoding="utf-8"))

    def save_repair_response(self, qa_id: str, attempt_no: int, response: RepairIssueTicketResponse) -> None:
        self._mutate_submission(
            qa_id,
            attempt_no,
            lambda record: record.update({"repair_response": response.model_dump(mode="json"), "updated_at": _utc_now()}),
        )

    def save_improvement_assessment(
        self,
        qa_id: str,
        attempt_no: int,
        assessment: ImprovementAssessment,
    ) -> None:
        self._mutate_submission(
            qa_id,
            attempt_no,
            lambda record: record.update(
                {
                    "improvement_assessment": assessment.model_dump(mode="json"),
                    "updated_at": _utc_now(),
                }
            ),
        )

    def _write_submission_record(self, record: SubmissionRecord) -> None:
        latest_path = self._paths.submissions / f"{record.id}.json"
        attempt_path = self._paths.attempts / f"{record.id}__attempt_{record.attempt_no}.json"
        payload = record.model_dump(mode="json")
        latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        attempt_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _mutate_submission(self, qa_id: str, attempt_no: int, mutate: Callable[[Dict[str, Any]], None]) -> None:
        current = self.get_submission_attempt(qa_id, attempt_no)
        if current is None:
            raise KeyError(f"Submission attempt not found for id={qa_id} attempt_no={attempt_no}")
        mutate(current)
        attempt_path = self._paths.attempts / f"{qa_id}__attempt_{attempt_no}.json"
        attempt_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        latest = self.get_submission(qa_id)
        if latest is None or int(latest["attempt_no"]) <= attempt_no:
            latest_path = self._paths.submissions / f"{qa_id}.json"
            latest_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    def _submission_matches(self, existing: Dict[str, Any], request: GeneratedCypherSubmissionRequest) -> bool:
        return (
            existing["id"] == request.id
            and existing["question"] == request.question
            and existing["generation_run_id"] == request.generation_run_id
            and existing["generated_cypher"] == request.generated_cypher
            and existing["input_prompt_snapshot"] == request.input_prompt_snapshot
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
