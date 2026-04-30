from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4


OutboxStatus = Literal["pending", "retrying", "dead_letter"]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeliveryOutbox:
    def __init__(self, outbox_dir: str | Path) -> None:
        self.outbox_dir = Path(outbox_dir)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        *,
        payload_type: str,
        payload: dict[str, Any],
        status: OutboxStatus = "pending",
        error: str | None = None,
    ) -> dict[str, Any]:
        delivery_id = str(uuid4())
        now = _utc_now_iso()
        record = {
            "delivery_id": delivery_id,
            "payload_type": payload_type,
            "id": payload["id"],
            "generation_run_id": payload["generation_run_id"],
            "payload": payload,
            "status": status,
            "attempt_count": 0,
            "last_error": error,
            "next_retry_at": now,
            "created_at": now,
            "updated_at": now,
        }
        self._write_record(record)
        return record

    def list_pending(self) -> list[dict[str, Any]]:
        return [record for record in self._list_records() if record["status"] == "pending"]

    def list_dead_letter(self) -> list[dict[str, Any]]:
        return [record for record in self._list_records() if record["status"] == "dead_letter"]

    def list_retryable(self, now: datetime | None = None, *, retrying_timeout_seconds: int = 300) -> list[dict[str, Any]]:
        retry_at = now or datetime.now(timezone.utc)
        stale_retrying_before = retry_at - timedelta(seconds=retrying_timeout_seconds)
        records = []
        for record in self._list_records():
            if record["status"] == "pending":
                next_retry_at = datetime.fromisoformat(record["next_retry_at"])
                if next_retry_at <= retry_at:
                    records.append(record)
            elif record["status"] == "retrying":
                updated_at = datetime.fromisoformat(record["updated_at"])
                if updated_at <= stale_retrying_before:
                    record["status"] = "pending"
                    self._write_record(record)
                    records.append(record)
        return records

    def mark_retrying(self, delivery_id: str, error: str | None = None) -> dict[str, Any]:
        record = self._read_record(delivery_id)
        if record["status"] != "pending":
            return record
        record["status"] = "retrying"
        record["attempt_count"] = int(record.get("attempt_count", 0)) + 1
        record["last_error"] = error
        record["updated_at"] = _utc_now_iso()
        self._write_record(record)
        return record

    def delete(self, delivery_id: str) -> None:
        self._record_path(delivery_id).unlink(missing_ok=True)

    def mark_dead_letter(self, delivery_id: str, error: str) -> dict[str, Any]:
        record = self._read_record(delivery_id)
        record["status"] = "dead_letter"
        record["last_error"] = error
        record["updated_at"] = _utc_now_iso()
        self._write_record(record)
        return record

    def mark_pending(self, delivery_id: str, error: str, *, delay_seconds: int = 1) -> dict[str, Any]:
        record = self._read_record(delivery_id)
        now = datetime.now(timezone.utc)
        record["status"] = "pending"
        record["last_error"] = error
        record["next_retry_at"] = (now + timedelta(seconds=delay_seconds)).isoformat()
        record["updated_at"] = now.isoformat()
        self._write_record(record)
        return record

    def _list_records(self) -> list[dict[str, Any]]:
        records = []
        for path in sorted(self.outbox_dir.glob("*.json")):
            records.append(json.loads(path.read_text(encoding="utf-8")))
        return records

    def _read_record(self, delivery_id: str) -> dict[str, Any]:
        return json.loads(self._record_path(delivery_id).read_text(encoding="utf-8"))

    def _write_record(self, record: dict[str, Any]) -> None:
        path = self._record_path(record["delivery_id"])
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(path)

    def _record_path(self, delivery_id: str) -> Path:
        return self.outbox_dir / f"{delivery_id}.json"
