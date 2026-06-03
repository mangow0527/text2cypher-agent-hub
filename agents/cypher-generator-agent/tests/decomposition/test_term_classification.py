from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from services.cypher_generator_agent.app.decomposition import QuestionDecomposer
from services.cypher_generator_agent.app.decomposition import models as decomposition_models
from services.cypher_generator_agent.app.decomposition.models import QuestionDecomposition


class FakeStructuredLLMClient:
    provider = "fake-llm"

    def __init__(self, response: Mapping[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def generate_structured(
        self,
        *,
        prompt: str,
        schema_name: str,
        schema: Mapping[str, Any],
        attempt: int,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "prompt": prompt,
                "schema_name": schema_name,
                "schema": schema,
                "attempt": attempt,
            }
        )
        return self.response


def test_polite_words_are_ignored_without_stopword_output() -> None:
    question = "麻烦帮我查一下 Gold 服务"
    client = FakeStructuredLLMClient(
        _valid_payload(
            question,
            intent_type="list",
            substantive_terms=[
                {"text": "Gold", "slot": "filter", "attached_to": "服务"},
                {"text": "服务", "slot": "projection"},
            ],
            literal_candidates=[{"text": "Gold", "kind_hint": "enum_or_name", "attached_to": "服务"}],
            output_shape="rows",
        )
    )

    result = QuestionDecomposer(client).decompose(question)

    assert result.schema_version == "question_decomposition_v1"
    assert result.result_type == "decomposition"
    assert result.intent_type == "list"
    assert result.output_shape == "rows"
    assert _term_texts(result.substantive_terms) == ["Gold", "服务"]
    assert result.substantive_terms[0].slot == _slot_kind("FILTER")
    assert result.substantive_terms[0].attached_to == "服务"
    assert "麻烦" not in _term_texts(result.substantive_terms)
    assert "帮我" not in _term_texts(result.substantive_terms)
    assert "查一下" not in _term_texts(result.substantive_terms)
    assert result.literal_candidates[0].text == "Gold"
    assert result.literal_candidates[0].kind_hint == "enum_or_name"
    assert result.literal_candidates[0].attached_to == "服务"
    assert client.calls[0]["schema_name"] == "question_decomposition_v1"
    assert question in client.calls[0]["prompt"]
    assert '你是图原生 Cypher 生成流水线中的"问题结构化拆解器"' in client.calls[0]["prompt"]
    assert "只输出用户问题里的表层词语" in client.calls[0]["prompt"]
    assert "两条" + "正交的分类轴" not in client.calls[0]["prompt"]
    assert "轴" + "三：语义槽位" not in client.calls[0]["prompt"]
    assert "substantive_terms 的 slot 取值" in client.calls[0]["prompt"]
    assert "示例 3:含时间、近似、聚合" in client.calls[0]["prompt"]
    assert "You are the Question Decomposer" not in client.calls[0]["prompt"]


def test_decomposition_has_no_legacy_slot_field() -> None:
    legacy_field = "slot" + "_terms"
    assert legacy_field not in QuestionDecomposition.model_fields


def test_decomposition_schema_drops_redundant_llm_output_fields() -> None:
    removed_fields = {"target_concepts", "relation_phrases", "stopword_terms"}

    assert removed_fields.isdisjoint(QuestionDecomposition.model_fields)
    schema = decomposition_models.question_decomposition_json_schema()
    decomposition_schema = schema["$defs"]["QuestionDecomposition"]
    assert removed_fields.isdisjoint(decomposition_schema["properties"])

    with pytest.raises(ValueError):
        QuestionDecomposition.model_validate(
            {
                "schema_version": "question_decomposition_v1",
                "result_type": "decomposition",
                "intent_type": "list",
                "original_question": "查询服务使用的隧道",
                "target_concepts": ["服务", "隧道"],
                "relation_phrases": ["使用"],
                "stopword_terms": ["查询", "的"],
                "literal_candidates": [],
                "substantive_terms": [
                    {"text": "服务", "slot": "path"},
                    {"text": "使用", "slot": "path"},
                    {"text": "隧道", "slot": "projection"},
                ],
                "modality_terms": [],
                "time_terms": [],
                "unparsed_terms": [],
                "output_shape": "rows",
            }
        )


def test_prompt_contract_omits_redundant_decomposition_views() -> None:
    question = "查询服务使用的隧道"
    client = FakeStructuredLLMClient(
        _valid_payload(
            question,
            intent_type="list",
            output_shape="rows",
            substantive_terms=[
                {"text": "服务", "slot": "path"},
                {"text": "使用", "slot": "path"},
                {"text": "隧道", "slot": "projection"},
            ],
            literal_candidates=[],
        )
    )

    result = QuestionDecomposer(client).decompose(question)
    prompt = client.calls[0]["prompt"]
    schema = client.calls[0]["schema"]

    assert result.result_type == "decomposition"
    for removed in ("target_concepts", "relation_phrases", "stopword_terms"):
        assert removed not in prompt
        assert removed not in schema["$defs"]["QuestionDecomposition"]["properties"]
    assert "substantive_terms" in prompt
    assert "literal_candidates" in prompt
    assert "modality_terms" in prompt
    assert "unparsed_terms" in prompt
    assert "无歧义时省略" in prompt


def test_prompt_contract_requires_substantive_terms_in_question_order() -> None:
    question = "统计服务使用的隧道数量"
    client = FakeStructuredLLMClient(
        _valid_payload(
            question,
            intent_type="aggregate",
            output_shape="grouped_rows",
            substantive_terms=[
                {"text": "统计", "slot": "projection"},
                {"text": "服务", "slot": "path"},
                {"text": "使用", "slot": "path"},
                {"text": "隧道", "slot": "path"},
                {"text": "数量", "slot": "projection"},
            ],
            literal_candidates=[],
        )
    )

    QuestionDecomposer(client).decompose(question)

    prompt = client.calls[0]["prompt"]
    assert "substantive_terms 按原问题中首次出现的顺序输出" in prompt
    assert "下游会再用 original_question 反查位置作为兜底" in prompt


def test_substantive_terms_carry_slot() -> None:
    slot_kind = _slot_kind("PROJECTION")
    decomp = QuestionDecomposition.model_validate(
        {
            "result_type": "decomposition",
            "original_question": "查询服务的名称",
            "intent_type": "list",
            "output_shape": "rows",
            "substantive_terms": [
                {"text": "服务", "slot": "projection"},
                {"text": "名称", "slot": "projection", "attached_to": "服务"},
            ],
        }
    )

    assert decomp.substantive_terms[0].slot == slot_kind
    assert decomp.substantive_terms[1].attached_to == "服务"


def test_modality_word_is_classified_as_modality() -> None:
    question = "大概有多少防火墙"
    client = FakeStructuredLLMClient(
        _valid_payload(
            question,
            intent_type="count",
            substantive_terms=[
                {"text": "多少", "slot": "projection"},
                {"text": "防火墙", "slot": "projection"},
            ],
            modality_terms=["大概"],
            output_shape="scalar",
        )
    )

    result = QuestionDecomposer(client).decompose(question)

    assert "大概" in result.modality_terms
    assert "防火墙" in _term_texts(result.substantive_terms)
    assert result.literal_candidates == []


def test_prompt_example_1_accepts_attribute_query_without_literal() -> None:
    question = "查询服务及其使用的隧道的时延"
    client = FakeStructuredLLMClient(
        _valid_payload(
            question,
            intent_type="list",
            output_shape="rows",
            substantive_terms=[
                {"text": "服务", "slot": "path"},
                {"text": "使用", "slot": "path"},
                {"text": "隧道", "slot": "path"},
                {"text": "时延", "slot": "projection", "attached_to": "服务"},
                {"text": "时延", "slot": "projection", "attached_to": "隧道"},
            ],
            literal_candidates=[],
        )
    )

    result = QuestionDecomposer(client).decompose(question)

    assert result.result_type == "decomposition"
    assert [item.model_dump(exclude_none=True) for item in result.substantive_terms] == [
        {"text": "服务", "slot": "path"},
        {"text": "使用", "slot": "path"},
        {"text": "隧道", "slot": "path"},
        {"text": "时延", "slot": "projection", "attached_to": "服务"},
        {"text": "时延", "slot": "projection", "attached_to": "隧道"},
    ]
    assert result.literal_candidates == []
    assert "slot" + "_terms" not in result.model_dump()
    assert "轴" + "三：语义槽位" not in client.calls[0]["prompt"]


def test_prompt_example_2_accepts_literal_filter() -> None:
    question = "Gold 级别的服务使用了哪些隧道"
    client = FakeStructuredLLMClient(
        _valid_payload(
            question,
            intent_type="list",
            output_shape="rows",
            substantive_terms=[
                {"text": "Gold", "slot": "filter", "attached_to": "服务"},
                {"text": "级别", "slot": "filter", "attached_to": "服务"},
                {"text": "服务", "slot": "path"},
                {"text": "使用", "slot": "path"},
                {"text": "隧道", "slot": "projection"},
            ],
            literal_candidates=[{"text": "Gold", "kind_hint": "enum_or_name", "attached_to": "服务"}],
        )
    )

    result = QuestionDecomposer(client).decompose(question)

    assert result.literal_candidates[0].text == "Gold"
    assert result.literal_candidates[0].kind_hint == "enum_or_name"
    assert "Gold" in _term_texts(result.substantive_terms)


def test_prompt_example_3_treats_firewall_as_concept_not_literal() -> None:
    question = "最近大概有多少台防火墙"
    client = FakeStructuredLLMClient(
        _valid_payload(
            question,
            intent_type="count",
            output_shape="scalar",
            substantive_terms=[
                {"text": "多少", "slot": "projection"},
                {"text": "台", "slot": "projection"},
                {"text": "防火墙", "slot": "projection"},
            ],
            modality_terms=["大概"],
            time_terms=["最近"],
            literal_candidates=[],
        )
    )

    result = QuestionDecomposer(client).decompose(question)

    assert result.literal_candidates == []
    assert result.time_terms == ["最近"]
    assert result.modality_terms == ["大概"]


def test_prompt_defines_literals_by_filter_role_not_control_slots() -> None:
    question = "返回前3名的隧道"
    client = FakeStructuredLLMClient(
        _valid_payload(
            question,
            intent_type="top_n",
            output_shape="rows",
            substantive_terms=[
                {"text": "前", "slot": "limit"},
                {"text": "3", "slot": "limit"},
                {"text": "隧道", "slot": "projection"},
            ],
            literal_candidates=[],
        )
    )

    result = QuestionDecomposer(client).decompose(question)
    prompt = client.calls[0]["prompt"]

    assert result.literal_candidates == []
    assert "返回前3名" in prompt
    assert "不进 literal_candidates" in prompt
    assert "带宽为3的链路" in prompt
    assert "slot=limit" in prompt
    assert "slot=filter" in prompt
    assert "判定锚点" in prompt


def test_recent_is_classified_as_time() -> None:
    question = "最近 down 的端口"
    client = FakeStructuredLLMClient(
        _valid_payload(
            question,
            intent_type="lookup",
            substantive_terms=[
                {"text": "down", "slot": "filter", "attached_to": "端口"},
                {"text": "端口", "slot": "projection"},
            ],
            time_terms=["最近"],
        )
    )

    result = QuestionDecomposer(client).decompose(question)

    assert "最近" in result.time_terms
    assert {"down", "端口"} <= set(_term_texts(result.substantive_terms))


def test_growth_term_is_preserved_as_substantive() -> None:
    question = "收入增长情况"
    client = FakeStructuredLLMClient(
        _valid_payload(
            question,
            intent_type="compare",
            substantive_terms=[
                {"text": "收入", "slot": "unknown"},
                {"text": "增长", "slot": "unknown"},
                {"text": "情况", "slot": "unknown"},
            ],
            output_shape="unknown",
        )
    )

    result = QuestionDecomposer(client).decompose(question)

    assert "增长" in _term_texts(result.substantive_terms)
    assert _term_texts(result.substantive_terms) == ["收入", "增长", "情况"]


def test_unclassified_meaningful_terms_remain_unparsed() -> None:
    question = "异常高的带宽隧道"
    client = FakeStructuredLLMClient(
        _valid_payload(
            question,
            substantive_terms=[
                {"text": "带宽", "slot": "filter", "attached_to": "隧道"},
                {"text": "隧道", "slot": "projection"},
            ],
            unparsed_terms=["异常高"],
        )
    )

    result = QuestionDecomposer(client).decompose(question)

    assert result.unparsed_terms == ["异常高"]


def test_decomposition_does_not_emit_graph_bound_literal_requests_or_coverage() -> None:
    question = "Gold 服务"
    client = FakeStructuredLLMClient(
        _valid_payload(
            question,
            intent_type="lookup",
            substantive_terms=[
                {"text": "Gold", "slot": "filter", "attached_to": "服务"},
                {"text": "服务", "slot": "projection"},
            ],
            literal_candidates=[{"text": "Gold", "kind_hint": "enum_or_name", "attached_to": "服务"}],
            output_shape="rows",
        )
    )

    result = QuestionDecomposer(client).decompose(question)
    serialized = result.model_dump()

    assert "literal_requests" not in serialized
    assert "coverage" not in serialized


def _valid_payload(
    question: str,
    *,
    intent_type: str = "unknown",
    substantive_terms: list[dict[str, str]],
    literal_candidates: list[dict[str, str]] | None = None,
    modality_terms: list[str] | None = None,
    time_terms: list[str] | None = None,
    unparsed_terms: list[str] | None = None,
    output_shape: str = "unknown",
) -> dict[str, Any]:
    return {
        "schema_version": "question_decomposition_v1",
        "result_type": "decomposition",
        "intent_type": intent_type,
        "original_question": question,
        "literal_candidates": literal_candidates or [],
        "substantive_terms": substantive_terms,
        "modality_terms": modality_terms or [],
        "time_terms": time_terms or [],
        "unparsed_terms": unparsed_terms or [],
        "output_shape": output_shape,
    }


def _term_texts(terms: list[Any]) -> list[str]:
    return [str(item.text) for item in terms]


def _slot_kind(name: str) -> Any:
    assert hasattr(decomposition_models, "SlotKind")
    return getattr(decomposition_models.SlotKind, name)
