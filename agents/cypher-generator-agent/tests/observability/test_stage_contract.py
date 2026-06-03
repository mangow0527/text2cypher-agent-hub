from __future__ import annotations

from services.cypher_generator_agent.app.observability.metrics import MetricName, METRIC_DEFINITIONS
from services.cypher_generator_agent.app.observability.stages import StageName


def test_stage_enum_contains_all_observability_v1_required_stages() -> None:
    assert {stage.value for stage in StageName} == {
        "graph_model_loader",
        "input_clarification_gate",
        "question_decomposer",
        "candidate_retrieval",
        "candidate_reranker",
        "literal_resolver",
        "deterministic_assembler",
        "grounded_understanding",
        "semantic_binder",
        "semantic_validator",
        "repair_controller",
        "dsl_builder",
        "dsl_parser",
        "dsl_structural_coverage_gate",
        "cypher_compiler",
        "cypher_self_validation",
        "output",
    }


def test_metric_skeleton_exposes_observability_v1_metric_names() -> None:
    assert {metric.value for metric in MetricName} == {
        "cga_graph_generation_success_count",
        "cga_graph_clarification_required_count",
        "cga_graph_unsupported_query_shape_count",
        "cga_graph_generation_failed_count",
        "cga_graph_stage_duration_ms",
        "cga_graph_total_duration_ms",
        "cga_graph_llm_call_count",
        "cga_graph_schema_retry_count",
        "cga_graph_token_usage_total",
        "cga_graph_repair_attempt_count",
        "cga_graph_repair_oscillation_count",
        "cga_graph_literal_cache_hit_rate",
        "cga_graph_coverage_failure_count",
        "cga_graph_input_clarification_required_count",
        "cga_graph_assumption_notice_count",
        "cga_graph_query_with_assumption_count",
        "cga_graph_compiler_shape_mismatch_count",
        "cga_graph_cypher_self_validation_failure_count",
    }
    assert METRIC_DEFINITIONS[MetricName.CGA_GRAPH_STAGE_DURATION_MS].metric_type == "histogram"
