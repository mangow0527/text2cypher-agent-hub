from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .models import EvaluationSubmissionRequest, ImprovementAssessment, IssueTicket, QAGoldenRequest


class TestingRepository:
    def __init__(self, data_dir: str) -> None:
        self._goldens_dir = Path(data_dir) / "goldens"
        self._submissions_dir = Path(data_dir) / "submissions"
        self._attempt_submissions_dir = Path(data_dir) / "submission_attempts"
        self._tickets_dir = Path(data_dir) / "issue_tickets"
        self._goldens_dir.mkdir(parents=True, exist_ok=True)
        self._submissions_dir.mkdir(parents=True, exist_ok=True)
        self._attempt_submissions_dir.mkdir(parents=True, exist_ok=True)
        self._tickets_dir.mkdir(parents=True, exist_ok=True)

    def save_golden(self, request: QAGoldenRequest) -> None:
        path = self._goldens_dir / f"{request.id}.json"
        now = _utc_now()
        answer_json = json.dumps(request.answer, ensure_ascii=False)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if (
                existing["golden_cypher"] != request.cypher
                or existing["golden_answer_json"] != answer_json
                or existing["difficulty"] != request.difficulty
            ):
                raise ValueError(f"Golden answer conflict for id={request.id}")
            existing["updated_at"] = now
            path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        record = {
            "id": request.id,
            "golden_cypher": request.cypher,
            "golden_answer_json": answer_json,
            "difficulty": request.difficulty,
            "received_at": now,
            "updated_at": now,
        }
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_submission(self, request: EvaluationSubmissionRequest, status: str) -> None:
        path = self._submissions_dir / f"{request.id}.json"
        now = _utc_now()
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if (
                existing["generation_run_id"] == request.generation_run_id
                and existing.get("attempt_no") == request.attempt_no
            ):
                raise ValueError(f"Submission conflict for id={request.id}")
        self._archive_legacy_latest_submission(request.id)
        record = {
            "id": request.id,
            "attempt_no": request.attempt_no,
            "question": request.question,
            "generation_run_id": request.generation_run_id,
            "generated_cypher": request.generated_cypher,
            "parse_summary": request.parse_summary,
            "guardrail_summary": request.guardrail_summary,
            "raw_output_snapshot": request.raw_output_snapshot,
            "input_prompt_snapshot": request.input_prompt_snapshot,
            "execution_json": None,
            "issue_ticket_id": None,
            "krss_response": None,
            "improvement_assessment": None,
            "status": status,
            "received_at": now,
            "updated_at": now,
        }
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        attempt_path = self._attempt_submissions_dir / f"{request.id}__attempt_{request.attempt_no}.json"
        attempt_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_submission_execution(self, id: str, execution_json: str, *, attempt_no: int | None = None) -> None:
        self._update_submission_record(
            id=id,
            attempt_no=attempt_no,
            mutate=lambda record: record.update({"execution_json": execution_json, "updated_at": _utc_now()}),
        )

    def get_golden(self, id: str) -> Optional[Dict[str, Any]]:
        path = self._goldens_dir / f"{id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def get_submission(self, id: str) -> Optional[Dict[str, Any]]:
        path = self._submissions_dir / f"{id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def get_submission_attempt(self, id: str, attempt_no: int) -> Optional[Dict[str, Any]]:
        path = self._attempt_submissions_dir / f"{id}__attempt_{attempt_no}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        latest = self.get_submission(id)
        if latest is None:
            return None
        latest_attempt_no = int(latest.get("attempt_no") or 1)
        if latest_attempt_no == attempt_no:
            latest["attempt_no"] = latest_attempt_no
            return latest
        return None

    def list_submission_attempts(self, id: str) -> list[Dict[str, Any]]:
        attempts: list[Dict[str, Any]] = []
        for path in sorted(self._attempt_submissions_dir.glob(f"{id}__attempt_*.json")):
            attempts.append(json.loads(path.read_text(encoding="utf-8")))
        return sorted(attempts, key=lambda item: int(item.get("attempt_no") or 0))

    def get_submission_snapshot(self, id: str) -> Optional[Dict[str, Any]]:
        return self.get_submission(id)

    def clear_console_run(self, id: str) -> None:
        submission_path = self._submissions_dir / f"{id}.json"
        ticket_ids: list[str] = []
        if submission_path.exists():
            try:
                existing = json.loads(submission_path.read_text(encoding="utf-8"))
                ticket_id = existing.get("issue_ticket_id")
                if ticket_id:
                    ticket_ids.append(str(ticket_id))
            except Exception:
                pass
            submission_path.unlink()
        for attempt_path in self._attempt_submissions_dir.glob(f"{id}__attempt_*.json"):
            try:
                existing = json.loads(attempt_path.read_text(encoding="utf-8"))
                ticket_id = existing.get("issue_ticket_id")
                if ticket_id:
                    ticket_ids.append(str(ticket_id))
            except Exception:
                pass
            attempt_path.unlink()

        ticket_ids.append(f"ticket-{id}")
        for ticket_id in set(ticket_ids):
            ticket_path = self._tickets_dir / f"{ticket_id}.json"
            if ticket_path.exists():
                ticket_path.unlink()

    def save_issue_ticket(self, ticket: IssueTicket) -> None:
        attempt_no = self._extract_attempt_no_from_ticket(ticket.ticket_id)
        record = {
            "ticket_id": ticket.ticket_id,
            "id": ticket.id,
            "attempt_no": attempt_no,
            "ticket_json": ticket.model_dump_json(),
            "created_at": _utc_now(),
        }
        path = self._tickets_dir / f"{ticket.ticket_id}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        self._update_submission_record(
            id=ticket.id,
            attempt_no=attempt_no,
            mutate=lambda submission: submission.update({"issue_ticket_id": ticket.ticket_id, "updated_at": _utc_now()}),
        )

    def mark_submission_repair_pending(self, id: str, ticket_id: str, *, attempt_no: int | None = None) -> None:
        self._update_submission_record(
            id=id,
            attempt_no=attempt_no,
            mutate=lambda record: record.update(
                {
                    "status": "repair_pending",
                    "issue_ticket_id": ticket_id,
                    "updated_at": _utc_now(),
                }
            ),
        )

    def mark_submission_repair_submission_failed(
        self,
        id: str,
        ticket_id: str,
        *,
        attempt_no: int | None = None,
    ) -> None:
        self._update_submission_record(
            id=id,
            attempt_no=attempt_no,
            mutate=lambda record: record.update(
                {
                    "status": "repair_submission_failed",
                    "issue_ticket_id": ticket_id,
                    "updated_at": _utc_now(),
                }
            ),
        )

    def mark_submission_status(self, id: str, status: str, *, attempt_no: int | None = None) -> None:
        self._update_submission_record(
            id=id,
            attempt_no=attempt_no,
            mutate=lambda record: record.update({"status": status, "updated_at": _utc_now()}),
        )

    def mark_submission_issue_ticket_created(self, id: str, ticket_id: str, *, attempt_no: int | None = None) -> None:
        self._update_submission_record(
            id=id,
            attempt_no=attempt_no,
            mutate=lambda record: record.update(
                {
                    "status": "issue_ticket_created",
                    "issue_ticket_id": ticket_id,
                    "updated_at": _utc_now(),
                }
            ),
        )

    def save_submission_krss_response(self, id: str, response: Dict[str, Any], *, attempt_no: int | None = None) -> None:
        self._update_submission_record(
            id=id,
            attempt_no=attempt_no,
            mutate=lambda record: record.update({"krss_response": response, "updated_at": _utc_now()}),
        )

    def save_improvement_assessment(
        self,
        id: str,
        assessment: ImprovementAssessment,
        *,
        attempt_no: int | None = None,
    ) -> None:
        self._update_submission_record(
            id=id,
            attempt_no=attempt_no or assessment.current_attempt_no,
            mutate=lambda record: record.update(
                {
                    "improvement_assessment": assessment.model_dump(mode="json"),
                    "updated_at": _utc_now(),
                }
            ),
        )

    def get_issue_ticket(self, ticket_id: str) -> Optional[IssueTicket]:
        path = self._tickets_dir / f"{ticket_id}.json"
        if not path.exists():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        return IssueTicket.model_validate_json(record["ticket_json"])

    def get_issue_snapshot_by_submission_id(self, id: str) -> Optional[Dict[str, Any]]:
        submission = self.get_submission(id)
        if submission is None or not submission.get("issue_ticket_id"):
            return None
        ticket = self.get_issue_ticket(submission["issue_ticket_id"])
        return None if ticket is None else ticket.model_dump(mode="json")

    def get_krss_snapshot_by_submission_id(self, id: str) -> Optional[Dict[str, Any]]:
        submission = self.get_submission(id)
        if submission is None:
            return None
        return submission.get("krss_response")

    def _update_submission_record(
        self,
        *,
        id: str,
        attempt_no: int | None,
        mutate,
    ) -> None:
        latest_path = self._submissions_dir / f"{id}.json"
        record = self.get_submission_attempt(id, attempt_no) if attempt_no is not None else self.get_submission(id)
        if record is None:
            return
        target_attempt_no = int(record.get("attempt_no") or attempt_no or 1)
        mutate(record)
        attempt_path = self._attempt_submissions_dir / f"{id}__attempt_{target_attempt_no}.json"
        attempt_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        latest = self.get_submission(id)
        if latest is None or int(latest.get("attempt_no") or 0) <= target_attempt_no:
            latest_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    def _extract_attempt_no_from_ticket(self, ticket_id: str) -> int | None:
        marker = "-attempt-"
        if marker not in ticket_id:
            return None
        try:
            return int(ticket_id.split(marker)[-1])
        except Exception:
            return None

    def _archive_legacy_latest_submission(self, id: str) -> None:
        latest_path = self._submissions_dir / f"{id}.json"
        if not latest_path.exists():
            return
        existing = json.loads(latest_path.read_text(encoding="utf-8"))
        attempt_no = int(existing.get("attempt_no") or 1)
        attempt_path = self._attempt_submissions_dir / f"{id}__attempt_{attempt_no}.json"
        if attempt_path.exists():
            return
        existing["attempt_no"] = attempt_no
        attempt_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
