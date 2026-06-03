from __future__ import annotations

from services.cypher_generator_agent.app.core.pipeline import run_pipeline


def test_pipeline_generates_top_n_for_devices_with_most_ports() -> None:
    result = run_pipeline(
        question="端口最多的 5 台设备",
        qa_id="gq-018",
        generation_run_id="run-top-n-ports",
    )

    assert result.status == "generated"
    assert result.dsl["query_shape"] == "top_n"
    assert [operation["op"] for operation in result.dsl["operations"]] == ["metric_aggregate", "sort", "limit"]
    assert result.cypher == (
        "MATCH (ne:NetworkElement)-[:HAS_PORT]->(port:Port)\n"
        "RETURN ne.id AS device, count(port) AS port_count\n"
        "ORDER BY port_count DESC\n"
        "LIMIT 5"
    )


def test_pipeline_generates_two_step_with_chain_for_port_count_ranking() -> None:
    result = run_pipeline(
        question="先按状态统计端口，再取最多的 5 个状态",
        qa_id="gq-019",
        generation_run_id="run-two-step-port-status",
    )

    assert result.status == "generated"
    assert result.dsl["query_shape"] == "two_step_aggregate"
    assert [operation["op"] for operation in result.dsl["operations"]] == ["subquery", "sort", "limit"]
    assert result.cypher == (
        "MATCH (port:Port)\n"
        "WITH port.status AS status, count(port.id) AS port_count\n"
        "RETURN status AS status, port_count AS port_count\n"
        "ORDER BY port_count DESC\n"
        "LIMIT 5"
    )
