from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from app.domain.models import StageRecord


BUSINESS_STAGE_DEFINITIONS = [
    {
        "key": "ground_schema",
        "label": "构建 GroundingIndex",
        "internal_statuses": ["schema_ready"],
    },
    {
        "key": "spec_coverage",
        "label": "定义 CoverageSpec",
        "internal_statuses": ["skeleton_ready"],
    },
    {
        "key": "generate_cypher",
        "label": "生成 Cypher",
        "internal_statuses": ["cypher_ready"],
    },
    {
        "key": "tugraph_validate",
        "label": "TuGraph 校验",
        "internal_statuses": ["validated"],
    },
    {
        "key": "generate_qa",
        "label": "完成 Q&A 对生成",
        "internal_statuses": ["questions_ready"],
    },
    {
        "key": "roundtrip_check",
        "label": "独立一致性校验",
        "internal_statuses": ["roundtrip_done"],
    },
    {
        "key": "release_dispatch",
        "label": "去重分层发布",
        "internal_statuses": ["deduped", "packaged"],
    },
]

_STAGE_ORDER = [
    "schema_ready",
    "skeleton_ready",
    "cypher_ready",
    "validated",
    "questions_ready",
    "roundtrip_done",
    "deduped",
    "packaged",
]


def build_business_stage_summary(
    stages: list[StageRecord],
    dispatch: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    stage_by_status = OrderedDict((definition["key"], definition) for definition in BUSINESS_STAGE_DEFINITIONS)
    now = now or datetime.now(timezone.utc)
    seen_statuses = [stage.to_status.value for stage in stages]
    max_status_index = max((index for index, status in enumerate(_STAGE_ORDER) if status in seen_statuses), default=-1)
    packaged_stage = _find_stage(stages, "packaged")
    dispatch_result = dispatch or {}

    summary = []
    for definition in stage_by_status.values():
        matching_stages = [stage for stage in stages if stage.to_status.value in definition["internal_statuses"]]
        first_index = min(_STAGE_ORDER.index(status_name) for status_name in definition["internal_statuses"])
        last_index = max(_STAGE_ORDER.index(status_name) for status_name in definition["internal_statuses"])
        has_running_stage = any(stage.finished_at is None for stage in matching_stages)
        if has_running_stage:
            status = "running"
        elif matching_stages:
            status = "completed"
        else:
            status = "pending" if max_status_index < 0 or max_status_index < first_index else "skipped"

        duration_ms = sum(stage.duration_ms or 0 for stage in matching_stages)
        for stage in matching_stages:
            if stage.finished_at is None:
                duration_ms += _elapsed_ms(stage.started_at, now)
        if not matching_stages:
            duration_ms = None
        if definition["key"] == "release_dispatch" and packaged_stage is not None:
            duration_ms = packaged_stage.duration_ms if packaged_stage.finished_at else _elapsed_ms(packaged_stage.started_at, now)

        message = matching_stages[-1].summary if matching_stages else ""
        if definition["key"] == "release_dispatch":
            message = _dispatch_message(dispatch_result, message)
            if not message and packaged_stage is not None:
                message = packaged_stage.summary

        summary.append(
            {
                "key": definition["key"],
                "label": definition["label"],
                "status": _dispatch_status_override(definition["key"], dispatch_result, status),
                "duration_ms": duration_ms,
                "message": message,
            }
        )
    return summary


def _elapsed_ms(started_at: str, now: datetime) -> int:
    try:
        start = datetime.fromisoformat(started_at)
    except ValueError:
        return 0
    return max(0, int((now - start).total_seconds() * 1000))


def _find_stage(stages: list[StageRecord], status_name: str) -> StageRecord | None:
    for stage in stages:
        if stage.to_status.value == status_name:
            return stage
    return None


def _dispatch_message(dispatch: dict[str, Any], fallback: str) -> str:
    message = str(dispatch.get("message") or "").strip()
    if message:
        return message
    status = str(dispatch.get("status") or "").strip()
    if status:
        return status
    return fallback


def _dispatch_status_override(key: str, dispatch: dict[str, Any], current_status: str) -> str:
    if key != "release_dispatch":
        return current_status
    if not dispatch:
        return current_status
    status = str(dispatch.get("status") or "").strip()
    if status == "skipped":
        return "skipped"
    if status in {"success", "partial"}:
        return "completed"
    if status == "failed":
        return "failed"
    return current_status
