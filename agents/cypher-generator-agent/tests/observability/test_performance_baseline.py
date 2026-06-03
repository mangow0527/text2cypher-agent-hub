from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from services.cypher_generator_agent.app.observability.baseline import (
    collect_performance_baseline,
    load_baseline_cases,
    write_baseline_artifact,
)
from services.cypher_generator_agent.app.observability.metrics import METRIC_DEFINITIONS, MetricName


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "performance_baseline_cases.yaml"


def test_performance_baseline_collects_generated_and_non_success_cases() -> None:
    cases = load_baseline_cases(FIXTURE_PATH)

    summary = collect_performance_baseline(cases)

    assert summary.schema_version == "cga_performance_baseline_v1"
    assert summary.case_count == 2
    assert summary.status_counts["generated"] == 1
    assert summary.status_counts["clarification_required"] == 1
    assert set(summary.latency_ms) == {"p50", "p95"}
    assert summary.llm_call_count == 0
    assert summary.schema_retry_count == 0
    assert summary.token_usage_total == 0
    for case in summary.cases:
        assert case.stage_count > 0
        assert case.actual_status == case.expected_status
        assert case.total_stage_duration_ms >= 0
        assert case.stage_durations_ms
        assert all(duration >= 0 for durations in case.stage_durations_ms.values() for duration in durations)
        assert case.trace["trace_schema_version"] == "cga_graph_trace_v1"
    failed = next(case for case in summary.cases if case.id == "perf-gq-019")
    assert failed.actual_reason == "coverage_failure"


def test_performance_baseline_writes_dated_report_artifact(tmp_path: Path) -> None:
    summary = collect_performance_baseline(load_baseline_cases(FIXTURE_PATH))

    path = write_baseline_artifact(
        summary,
        reports_dir=tmp_path / "reports",
        artifact_date=date(2026, 5, 27),
    )

    assert path.name == "baseline_20260527.json"
    artifact = json.loads(path.read_text(encoding="utf-8"))
    assert artifact["schema_version"] == "cga_performance_baseline_v1"
    assert artifact["case_count"] == 2
    assert "p50" in artifact["latency_ms"]
    assert "p95" in artifact["latency_ms"]
    assert "llm_call_count" in artifact
    assert "schema_retry_count" in artifact
    assert "token_usage_total" in artifact


def test_performance_baseline_metrics_are_declared() -> None:
    assert METRIC_DEFINITIONS[MetricName.CGA_GRAPH_TOTAL_DURATION_MS].metric_type == "histogram"
    assert METRIC_DEFINITIONS[MetricName.CGA_GRAPH_STAGE_DURATION_MS].metric_type == "histogram"
    assert METRIC_DEFINITIONS[MetricName.CGA_GRAPH_LLM_CALL_COUNT].metric_type == "counter"
    assert METRIC_DEFINITIONS[MetricName.CGA_GRAPH_SCHEMA_RETRY_COUNT].metric_type == "counter"
    assert METRIC_DEFINITIONS[MetricName.CGA_GRAPH_TOKEN_USAGE_TOTAL].metric_type == "counter"
