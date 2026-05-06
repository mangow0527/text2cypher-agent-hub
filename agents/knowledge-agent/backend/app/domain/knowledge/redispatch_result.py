from __future__ import annotations

from typing import Any


def skipped_redispatch_result(qa_id: str) -> dict[str, Any]:
    return {
        "trace_id": qa_id,
        "qa_id": qa_id,
        "status": "skipped",
        "attempt": 0,
        "max_attempts": 0,
        "dispatch": {
            "status": "skipped",
            "reason": "knowledge_agent_no_longer_redispatches_qa",
        },
    }
