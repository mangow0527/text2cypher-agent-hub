from __future__ import annotations

from services.cypher_generator_agent.app.core.pipeline import run_pipeline


def test_device_through_tunnel_question_generates_bounded_variable_path() -> None:
    output = run_pipeline(
        question="找出所有经过设备 ne-0001 的隧道",
        qa_id="gq-012",
        generation_run_id="run-gq-012",
    )

    assert output.status == "generated"
    assert output.cypher == (
        "MATCH path = (tun:Tunnel)-[:PATH_THROUGH*1..8]->(ne:NetworkElement)\n"
        "WHERE ne.id = 'ne-0001'\n"
        "RETURN tun.id AS tunnel_id"
    )
    assert output.dsl is not None
    assert output.dsl["query_shape"] == "variable_path_traversal"
    assert output.dsl["operations"][0]["max_hops"] == 8
    assert output.dsl["operations"][0]["through"]["filters"][0]["value"]["normalized"] == "ne-0001"
    assert output.trace["final_outputs"]["cypher"] == output.cypher
    assert output.trace["stages"][-2]["stage"] == "cypher_self_validation"
    assert output.trace["stages"][-2]["status"] == "success"
