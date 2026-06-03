from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .models import (
    CgaGenerationNonSuccessReport,
    CgaQuestionReceivedReport,
    EvaluationSummary,
    ExecutionResult,
    GeneratedCypherSubmissionRequest,
    ImprovementAssessment,
    IssueTicket,
    QAGoldenRequest,
    SemanticReviewArtifact,
    SaveSubmissionResult,
    RepairAgentResponse,
    SubmissionRecord,
)


@dataclass
class _PathSet:
    goldens: Path
    question_receipts: Path
    submissions: Path
    attempts: Path
    generation_failures: Path
    tickets: Path


class TestingRepository:
    __test__ = False

    def __init__(self, data_dir: str) -> None:
        paths = _PathSet(
            goldens=Path(data_dir) / "goldens",
            question_receipts=Path(data_dir) / "question_receipts",
            submissions=Path(data_dir) / "submissions",
            attempts=Path(data_dir) / "submission_attempts",
            generation_failures=Path(data_dir) / "generation_failures",
            tickets=Path(data_dir) / "issue_tickets",
        )
        self._paths = paths
        for path in (
            paths.goldens,
            paths.question_receipts,
            paths.submissions,
            paths.attempts,
            paths.generation_failures,
            paths.tickets,
        ):
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

    def save_question_received_report(self, report: CgaQuestionReceivedReport) -> None:
        path = self._question_received_report_path(report.id)
        payload = report.model_dump(mode="json")
        payload["received_at"] = _utc_now()
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            comparable = {key: value for key, value in existing.items() if key != "received_at"}
            if comparable == report.model_dump(mode="json"):
                return
            if comparable.get("generation_run_id") == report.generation_run_id:
                raise ValueError(f"CGA question receipt conflict for id={report.id}")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_question_received_report(self, qa_id: str) -> Optional[Dict[str, Any]]:
        path = self._question_received_report_path(qa_id)
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
            generation_status="generated",
            failure_reason=None,
            state=state,
            received_at=_utc_now(),
            updated_at=_utc_now(),
        )
        self._write_submission_record(record)
        return SaveSubmissionResult(created=True, attempt_no=attempt_no, record=record)

    def save_generation_failure_report(self, report: CgaGenerationNonSuccessReport) -> None:
        path = self._generation_failure_report_path(report.id, report.generation_run_id)
        payload = report.model_dump(mode="json")
        payload["received_at"] = _utc_now()
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            comparable = {key: value for key, value in existing.items() if key != "received_at"}
            if comparable != report.model_dump(mode="json"):
                raise ValueError(f"CGA non-success report conflict for id={report.id}")
            return
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_generation_failure_report(self, qa_id: str, generation_run_id: str) -> Optional[Dict[str, Any]]:
        path = self._generation_failure_report_path(qa_id, generation_run_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_generation_failure_reports(self, qa_id: str) -> list[Dict[str, Any]]:
        reports = []
        for path in sorted(self._paths.generation_failures.glob(f"{qa_id}__*.json")):
            reports.append(json.loads(path.read_text(encoding="utf-8")))
        return sorted(
            reports,
            key=lambda item: (
                str(item.get("received_at", "")),
                str(item.get("generation_run_id", "")),
            ),
        )

    def save_generation_failure_submission(
        self,
        report: CgaGenerationNonSuccessReport,
        *,
        state: str,
    ) -> SaveSubmissionResult:
        if report.generation_status != "generation_failed":
            raise ValueError("Only generation_failed reports can create evaluation attempts")
        self.save_generation_failure_report(report)
        parsed_cypher = (report.parsed_cypher or "").strip()
        generated_cypher = parsed_cypher
        for existing in self.list_submission_attempts(report.id):
            if existing["generation_run_id"] != report.generation_run_id:
                continue
            if self._generation_failure_submission_matches(existing, report, generated_cypher):
                return SaveSubmissionResult(
                    created=False,
                    attempt_no=int(existing["attempt_no"]),
                    record=SubmissionRecord.model_validate(existing),
                )
            raise ValueError(f"Submission conflict for id={report.id}")

        attempt_no = len(self.list_submission_attempts(report.id)) + 1
        record = SubmissionRecord(
            id=report.id,
            attempt_no=attempt_no,
            question=report.question,
            generation_run_id=report.generation_run_id,
            generated_cypher=generated_cypher,
            input_prompt_snapshot=report.input_prompt_snapshot,
            generation_status="generation_failed",
            failure_reason=report.failure_reason,
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

    def list_submission_attempts_by_state(self, state: str) -> list[Dict[str, Any]]:
        attempts: list[Dict[str, Any]] = []
        for path in sorted(self._paths.attempts.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("state") == state:
                attempts.append(data)
        return sorted(
            attempts,
            key=lambda item: (
                str(item.get("received_at", "")),
                str(item.get("id", "")),
                int(item.get("attempt_no", 0)),
            ),
        )

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

    def save_semantic_review_artifact(
        self,
        qa_id: str,
        attempt_no: int,
        artifact: SemanticReviewArtifact,
    ) -> None:
        self._mutate_submission(
            qa_id,
            attempt_no,
            lambda record: record.update(
                {
                    "semantic_review": artifact.model_dump(mode="json"),
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

    def save_repair_response(self, qa_id: str, attempt_no: int, response: RepairAgentResponse) -> None:
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
            and existing.get("generation_status", "generated") == "generated"
        )

    def _generation_failure_submission_matches(
        self,
        existing: Dict[str, Any],
        report: CgaGenerationNonSuccessReport,
        generated_cypher: str,
    ) -> bool:
        return (
            existing["id"] == report.id
            and existing["question"] == report.question
            and existing["generation_run_id"] == report.generation_run_id
            and existing["generated_cypher"] == generated_cypher
            and existing["input_prompt_snapshot"] == report.input_prompt_snapshot
            and existing.get("generation_status") == "generation_failed"
            and existing.get("failure_reason") == report.failure_reason
        )

    def _generation_failure_report_path(self, qa_id: str, generation_run_id: str) -> Path:
        return self._paths.generation_failures / f"{qa_id}__{generation_run_id}.json"

    def _question_received_report_path(self, qa_id: str) -> Path:
        return self._paths.question_receipts / f"{qa_id}.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
