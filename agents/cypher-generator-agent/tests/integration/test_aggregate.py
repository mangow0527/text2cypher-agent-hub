from __future__ import annotations

import pytest

from services.cypher_generator_agent.app.core import pipeline as pipeline_module
from services.cypher_generator_agent.app.core.pipeline import run_pipeline


def test_pipeline_generates_deterministic_ad_hoc_aggregate_for_firewall_count() -> None:
    result = run_pipeline(
        question="全网有多少台防火墙",
        qa_id="gq-008",
        generation_run_id="run-aggregate-firewall",
    )

    assert result.status == "generated"
    assert result.dsl["query_shape"] == "ad_hoc_aggregate"
    assert result.dsl["filters"][0]["value"]["normalized"] == "firewall"
    assert result.dsl["operations"][0]["measures"][0]["alias"] == "network_element_count"
    assert result.cypher == (
        "MATCH (ne:NetworkElement)\n"
        "WHERE ne.elem_type = 'firewall'\n"
        "RETURN count(ne.id) AS network_element_count"
    )
    trace = result.trace
    assert trace["final_status"] == "generated"
    assert trace["final_outputs"]["cypher"] == result.cypher


def test_pipeline_generates_ad_hoc_aggregate_for_port_status_count() -> None:
    result = run_pipeline(
        question="按状态统计端口数量",
        qa_id="gq-010",
        generation_run_id="run-aggregate-port-status",
    )

    assert result.status == "generated"
    assert result.dsl["query_shape"] == "ad_hoc_aggregate"
    assert result.dsl["operations"][0]["measures"][0]["alias"] == "port_count"
    assert result.cypher == (
        "MATCH (port:Port)\n"
        "RETURN port.status AS status, count(port.id) AS port_count"
    )


def test_pipeline_counts_property_when_count_object_modifier_is_in_filter_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decompose(question: str) -> dict[str, object]:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "original_question": question,
            "intent_type": "count",
            "output_shape": "scalar",
            "literal_candidates": [],
            "literal_requests": [],
            "semantic_terms": ["Service.quality_of_service"],
            "substantive_terms": [
                {"text": "服务", "slot": "path"},
                {"text": "节点", "slot": "path"},
                {"text": "服务质量", "slot": "filter", "attached_to": "服务"},
                {"text": "属性", "slot": "filter", "attached_to": "服务质量"},
                {"text": "数量", "slot": "projection"},
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
        }

    monkeypatch.setattr(pipeline_module, "_mock_decompose", fake_decompose)

    result = run_pipeline(
        question="统计所有服务节点中服务质量属性的数量。",
        qa_id="qa_66c751051eaf",
        generation_run_id="run-qa_66c751051eaf",
    )

    assert result.status == "generated"
    assert result.cypher == (
        "MATCH (svc:Service)\n"
        "RETURN count(svc.quality_of_service) AS service_quality_of_service_count"
    )
    assert result.dsl["operations"][0]["measures"][0]["property"] == {
        "owner": "Service",
        "name": "quality_of_service",
    }
    assert _stage_names(result.trace).count("grounded_understanding") == 0


def _stage_names(trace: dict[str, object]) -> list[str]:
    return [stage["stage"] for stage in trace["stages"]]
