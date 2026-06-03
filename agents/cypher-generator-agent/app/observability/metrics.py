from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class MetricName(str, Enum):
    CGA_GRAPH_GENERATION_SUCCESS_COUNT = "cga_graph_generation_success_count"
    CGA_GRAPH_CLARIFICATION_REQUIRED_COUNT = "cga_graph_clarification_required_count"
    CGA_GRAPH_UNSUPPORTED_QUERY_SHAPE_COUNT = "cga_graph_unsupported_query_shape_count"
    CGA_GRAPH_GENERATION_FAILED_COUNT = "cga_graph_generation_failed_count"
    CGA_GRAPH_STAGE_DURATION_MS = "cga_graph_stage_duration_ms"
    CGA_GRAPH_TOTAL_DURATION_MS = "cga_graph_total_duration_ms"
    CGA_GRAPH_LLM_CALL_COUNT = "cga_graph_llm_call_count"
    CGA_GRAPH_SCHEMA_RETRY_COUNT = "cga_graph_schema_retry_count"
    CGA_GRAPH_TOKEN_USAGE_TOTAL = "cga_graph_token_usage_total"
    CGA_GRAPH_REPAIR_ATTEMPT_COUNT = "cga_graph_repair_attempt_count"
    CGA_GRAPH_REPAIR_OSCILLATION_COUNT = "cga_graph_repair_oscillation_count"
    CGA_GRAPH_LITERAL_CACHE_HIT_RATE = "cga_graph_literal_cache_hit_rate"
    CGA_GRAPH_COVERAGE_FAILURE_COUNT = "cga_graph_coverage_failure_count"
    CGA_GRAPH_INPUT_CLARIFICATION_REQUIRED_COUNT = "cga_graph_input_clarification_required_count"
    CGA_GRAPH_ASSUMPTION_NOTICE_COUNT = "cga_graph_assumption_notice_count"
    CGA_GRAPH_QUERY_WITH_ASSUMPTION_COUNT = "cga_graph_query_with_assumption_count"
    CGA_GRAPH_COMPILER_SHAPE_MISMATCH_COUNT = "cga_graph_compiler_shape_mismatch_count"
    CGA_GRAPH_CYPHER_SELF_VALIDATION_FAILURE_COUNT = "cga_graph_cypher_self_validation_failure_count"


class MetricDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: MetricName
    metric_type: Literal["counter", "histogram", "ratio"]
    description: str


METRIC_DEFINITIONS: dict[MetricName, MetricDefinition] = {
    MetricName.CGA_GRAPH_GENERATION_SUCCESS_COUNT: MetricDefinition(
        name=MetricName.CGA_GRAPH_GENERATION_SUCCESS_COUNT,
        metric_type="counter",
        description="Incremented for each run with final_status=generated.",
    ),
    MetricName.CGA_GRAPH_CLARIFICATION_REQUIRED_COUNT: MetricDefinition(
        name=MetricName.CGA_GRAPH_CLARIFICATION_REQUIRED_COUNT,
        metric_type="counter",
        description="Incremented for each run requiring user clarification.",
    ),
    MetricName.CGA_GRAPH_UNSUPPORTED_QUERY_SHAPE_COUNT: MetricDefinition(
        name=MetricName.CGA_GRAPH_UNSUPPORTED_QUERY_SHAPE_COUNT,
        metric_type="counter",
        description="Incremented for each unsupported_query_shape run.",
    ),
    MetricName.CGA_GRAPH_GENERATION_FAILED_COUNT: MetricDefinition(
        name=MetricName.CGA_GRAPH_GENERATION_FAILED_COUNT,
        metric_type="counter",
        description="Incremented for each generation_failed run.",
    ),
    MetricName.CGA_GRAPH_STAGE_DURATION_MS: MetricDefinition(
        name=MetricName.CGA_GRAPH_STAGE_DURATION_MS,
        metric_type="histogram",
        description="Stage duration distribution in milliseconds.",
    ),
    MetricName.CGA_GRAPH_TOTAL_DURATION_MS: MetricDefinition(
        name=MetricName.CGA_GRAPH_TOTAL_DURATION_MS,
        metric_type="histogram",
        description="End-to-end generation duration distribution in milliseconds.",
    ),
    MetricName.CGA_GRAPH_LLM_CALL_COUNT: MetricDefinition(
        name=MetricName.CGA_GRAPH_LLM_CALL_COUNT,
        metric_type="counter",
        description="Counts LLM calls, including retries and repair loop calls.",
    ),
    MetricName.CGA_GRAPH_SCHEMA_RETRY_COUNT: MetricDefinition(
        name=MetricName.CGA_GRAPH_SCHEMA_RETRY_COUNT,
        metric_type="counter",
        description="Counts structured-output schema retries across LLM stages.",
    ),
    MetricName.CGA_GRAPH_TOKEN_USAGE_TOTAL: MetricDefinition(
        name=MetricName.CGA_GRAPH_TOKEN_USAGE_TOTAL,
        metric_type="counter",
        description="Counts total LLM token usage recorded in trace stage metrics.",
    ),
    MetricName.CGA_GRAPH_REPAIR_ATTEMPT_COUNT: MetricDefinition(
        name=MetricName.CGA_GRAPH_REPAIR_ATTEMPT_COUNT,
        metric_type="counter",
        description="Counts repair attempts.",
    ),
    MetricName.CGA_GRAPH_REPAIR_OSCILLATION_COUNT: MetricDefinition(
        name=MetricName.CGA_GRAPH_REPAIR_OSCILLATION_COUNT,
        metric_type="counter",
        description="Counts runs where repair oscillation is detected.",
    ),
    MetricName.CGA_GRAPH_LITERAL_CACHE_HIT_RATE: MetricDefinition(
        name=MetricName.CGA_GRAPH_LITERAL_CACHE_HIT_RATE,
        metric_type="ratio",
        description="Literal cache hit ratio by property/window.",
    ),
    MetricName.CGA_GRAPH_COVERAGE_FAILURE_COUNT: MetricDefinition(
        name=MetricName.CGA_GRAPH_COVERAGE_FAILURE_COUNT,
        metric_type="counter",
        description="Counts uncovered substantive, time, or unparsed terms.",
    ),
    MetricName.CGA_GRAPH_INPUT_CLARIFICATION_REQUIRED_COUNT: MetricDefinition(
        name=MetricName.CGA_GRAPH_INPUT_CLARIFICATION_REQUIRED_COUNT,
        metric_type="counter",
        description="Counts input clarification gate clarification decisions.",
    ),
    MetricName.CGA_GRAPH_ASSUMPTION_NOTICE_COUNT: MetricDefinition(
        name=MetricName.CGA_GRAPH_ASSUMPTION_NOTICE_COUNT,
        metric_type="counter",
        description="Counts assumptions rendered as user-visible notices.",
    ),
    MetricName.CGA_GRAPH_QUERY_WITH_ASSUMPTION_COUNT: MetricDefinition(
        name=MetricName.CGA_GRAPH_QUERY_WITH_ASSUMPTION_COUNT,
        metric_type="counter",
        description="Counts runs with at least one assumption.",
    ),
    MetricName.CGA_GRAPH_COMPILER_SHAPE_MISMATCH_COUNT: MetricDefinition(
        name=MetricName.CGA_GRAPH_COMPILER_SHAPE_MISMATCH_COUNT,
        metric_type="counter",
        description="Counts compiler shape mismatch failures.",
    ),
    MetricName.CGA_GRAPH_CYPHER_SELF_VALIDATION_FAILURE_COUNT: MetricDefinition(
        name=MetricName.CGA_GRAPH_CYPHER_SELF_VALIDATION_FAILURE_COUNT,
        metric_type="counter",
        description="Counts failed Cypher self-validation checks.",
    ),
}
