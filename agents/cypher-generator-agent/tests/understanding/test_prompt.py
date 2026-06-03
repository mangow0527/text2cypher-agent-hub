from __future__ import annotations

import json

from services.cypher_generator_agent.app.understanding.prompt import (
    build_grounded_understanding_prompt,
    build_grounded_understanding_schema,
)


def test_prompt_and_schema_use_compact_decision_contract() -> None:
    prompt = build_grounded_understanding_prompt(
        question_decomposition={
            "schema_version": "question_decomposition_v1",
            "original_question": "Gold 服务使用了哪些隧道",
            "substantive_terms": [
                {"text": "Gold", "slot": "filter", "attached_to": "服务"},
                {"text": "服务", "slot": "path"},
                {"text": "使用", "slot": "path"},
                {"text": "隧道", "slot": "projection"},
            ],
        },
        candidates={"candidates": []},
        literal_results=[],
    )

    assert "rationale" not in prompt
    assert "coverage" not in prompt
    assert "原样复制 semantic_type、semantic_id、semantic_name 和 owner" not in prompt

    schema = build_grounded_understanding_schema()
    assert schema["required"] == ["status", "query_shape", "selected_bindings"]
    assert "coverage" not in schema["required"]
    assert "confidence" not in schema["properties"]
    binding_schema = schema["$defs"]["CompactGroundedBinding"]
    assert binding_schema["required"] == ["candidate_id"]
    assert "rationale" not in binding_schema["properties"]
    assert "semantic_id" not in binding_schema["properties"]
    assert "semantic_name" not in binding_schema["properties"]
    assert "owner" not in binding_schema["properties"]


def test_prompt_payload_assigns_literal_ids_for_compact_selection() -> None:
    prompt = build_grounded_understanding_prompt(
        question_decomposition={
            "schema_version": "question_decomposition_v1",
            "original_question": "Gold 服务使用了哪些隧道",
        },
        candidates={"candidates": []},
        literal_results=[
            {
                "schema_version": "literal_resolver_result_v1",
                "raw_literal": "Gold",
                "resolved": True,
                "resolved_value": "GOLD",
                "normalized_value": "GOLD",
                "match_type": "value_synonym",
                "confidence": 0.98,
                "expected_vertex": "Service",
                "expected_property": "quality_of_service",
                "evidence": [],
            }
        ],
    )

    payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
    assert payload["literal_resolver_results"][0]["literal_id"] == "literal:0"
    assert "repair_context" not in payload
    assert "structural_repair_guidance" not in payload
