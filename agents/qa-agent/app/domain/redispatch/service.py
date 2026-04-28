from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.errors import AppError
from app.logging import ModuleLogStore
from app.storage.redispatch_store import RedispatchAttemptStore


class SingleQARedispatchService:
    def __init__(
        self,
        dispatcher,
        releases_root: Path,
        attempt_store: RedispatchAttemptStore,
        module_logs: ModuleLogStore,
        max_attempts: int = 3,
    ) -> None:
        self.dispatcher = dispatcher
        self.releases_root = releases_root
        self.attempt_store = attempt_store
        self.module_logs = module_logs
        self.max_attempts = max_attempts

    def redispatch(self, qa_id: str, trigger: str = "repair") -> dict[str, Any]:
        self.module_logs.append(
            module="redispatch",
            level="info",
            operation="qa_redispatch_requested",
            trace_id=qa_id,
            status="started",
            request_body={"qa_id": qa_id, "trigger": trigger},
        )

        attempt_count = self.attempt_store.count(qa_id)
        if attempt_count >= self.max_attempts:
            self.module_logs.append(
                module="redispatch",
                level="warning",
                operation="qa_redispatch_rejected",
                trace_id=qa_id,
                status="limit_reached",
                response_body={"attempt": attempt_count, "max_attempts": self.max_attempts},
            )
            raise AppError(
                "REDISPATCH_LIMIT_REACHED",
                f"QA pair {qa_id} has already been redispatched {attempt_count} times.",
            )

        row, source_file = self._find_release_row(qa_id)
        dispatch_result = self.dispatcher.dispatch_release_rows([row])
        next_attempt = attempt_count + 1
        attempt_payload = {
            "trigger": trigger,
            "attempt": next_attempt,
            "max_attempts": self.max_attempts,
            "source_file": source_file.name,
            "dispatch": dispatch_result,
        }
        self.attempt_store.append(qa_id, attempt_payload)

        result = {
            "trace_id": qa_id,
            "qa_id": qa_id,
            "trigger": trigger,
            "attempt": next_attempt,
            "max_attempts": self.max_attempts,
            "source_file": source_file.name,
            "dispatch": dispatch_result,
            "status": dispatch_result.get("status", "unknown"),
        }
        self.module_logs.append(
            module="redispatch",
            level="info",
            operation="qa_redispatch_completed",
            trace_id=qa_id,
            status=result["status"],
            request_body={"qa_id": qa_id, "trigger": trigger},
            response_body=result,
        )
        return result

    def _find_release_row(self, qa_id: str) -> tuple[dict[str, Any], Path]:
        release_files = sorted(self.releases_root.glob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in release_files:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("id") == qa_id:
                    return row, path
        self.module_logs.append(
            module="redispatch",
            level="error",
            operation="qa_release_lookup_failed",
            trace_id=qa_id,
            status="not_found",
            request_body={"qa_id": qa_id},
        )
        raise AppError("QA_RELEASE_NOT_FOUND", f"Unable to find release row for qa_id={qa_id}.")
