from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from services.cypher_generator_agent.app.core.pipeline import run_pipeline
from services.cypher_generator_agent.app.core.result import GenerationOutput


BASELINE_SCHEMA_VERSION = "cga_performance_baseline_v1"


class BaselineCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_golden_id: str | None = None
    question: str
    expected_status: str
    expected_reason: str | None = None
    category: str


class BaselineCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    expected_status: str
    actual_status: str
    expected_reason: str | None = None
    actual_reason: str | None = None
    stage_count: int
    stage_durations_ms: dict[str, list[int]]
    total_stage_duration_ms: int
    llm_call_count: int
    schema_retry_count: int
    token_usage_total: int
    trace: dict[str, Any]


class BaselineSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = BASELINE_SCHEMA_VERSION
    generated_at: str
    case_count: int
    status_counts: dict[str, int]
    latency_ms: dict[str, int]
    llm_call_count: int
    schema_retry_count: int
    token_usage_total: int
    cases: list[BaselineCaseResult]


def load_baseline_cases(path: Path) -> list[BaselineCase]:
    import yaml

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = payload.get("cases", []) if isinstance(payload, dict) else []
    return [BaselineCase.model_validate(item) for item in cases]


def collect_performance_baseline(cases: Iterable[BaselineCase]) -> BaselineSummary:
    results: list[BaselineCaseResult] = []
    for case in cases:
        output = run_pipeline(
            qa_id=case.id,
            question=case.question,
            generation_run_id=f"baseline-{case.id}",
        )
        results.append(_case_result(case, output))

    total_durations = [item.total_stage_duration_ms for item in results]
    status_counts: dict[str, int] = {}
    for item in results:
        status_counts[item.actual_status] = status_counts.get(item.actual_status, 0) + 1
    return BaselineSummary(
        generated_at=datetime.now(UTC).isoformat(),
        case_count=len(results),
        status_counts=status_counts,
        latency_ms={
            "p50": _percentile(total_durations, 50),
            "p95": _percentile(total_durations, 95),
        },
        llm_call_count=sum(item.llm_call_count for item in results),
        schema_retry_count=sum(item.schema_retry_count for item in results),
        token_usage_total=sum(item.token_usage_total for item in results),
        cases=results,
    )


def write_baseline_artifact(
    summary: BaselineSummary,
    *,
    reports_dir: Path,
    artifact_date: date | None = None,
) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    day = artifact_date or date.today()
    path = reports_dir / f"baseline_{day:%Y%m%d}.json"
    path.write_text(
        json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _case_result(case: BaselineCase, output: GenerationOutput) -> BaselineCaseResult:
    trace = output.trace
    stage_durations = _stage_durations(trace.get("stages", []))
    reason = _output_reason(output)
    return BaselineCaseResult(
        id=case.id,
        question=case.question,
        expected_status=case.expected_status,
        actual_status=output.status,
        expected_reason=case.expected_reason,
        actual_reason=reason,
        stage_count=sum(len(items) for items in stage_durations.values()),
        stage_durations_ms=stage_durations,
        total_stage_duration_ms=sum(duration for items in stage_durations.values() for duration in items),
        llm_call_count=_metric_sum(trace, "llm_call_count"),
        schema_retry_count=_metric_sum(trace, "schema_retry_count"),
        token_usage_total=_token_usage_total(trace),
        trace=trace,
    )


def _output_reason(output: GenerationOutput) -> str | None:
    if output.failure is not None:
        return output.failure.reason
    for stage in reversed(output.trace.get("stages", [])):
        if not isinstance(stage, dict) or stage.get("stage") != "repair_controller":
            continue
        output_ref = stage.get("output_ref", {})
        if not isinstance(output_ref, dict):
            continue
        value = output_ref.get("value", {})
        if isinstance(value, dict) and value.get("reason_code"):
            return str(value["reason_code"])
    return None


def _stage_durations(stages: list[Any]) -> dict[str, list[int]]:
    durations: dict[str, list[int]] = {}
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        stage_name = str(stage.get("stage", "unknown"))
        duration = int(stage.get("duration_ms", 0))
        durations.setdefault(stage_name, []).append(duration)
    return durations


def _metric_sum(trace: dict[str, Any], key: str) -> int:
    total = 0
    for stage in trace.get("stages", []):
        if not isinstance(stage, dict):
            continue
        metrics = stage.get("metrics", {})
        if isinstance(metrics, dict):
            total += int(metrics.get(key, 0) or 0)
    return total


def _token_usage_total(trace: dict[str, Any]) -> int:
    total = 0
    for stage in trace.get("stages", []):
        if not isinstance(stage, dict):
            continue
        metrics = stage.get("metrics", {})
        if not isinstance(metrics, dict):
            continue
        token_usage = metrics.get("token_usage", {})
        if isinstance(token_usage, dict):
            total += sum(int(value or 0) for value in token_usage.values())
        total += int(metrics.get("token_usage_total", 0) or 0)
    return total


def _percentile(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    if percentile == 50:
        return int(median(values))
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * (percentile / 100)))
    return ordered[index]
