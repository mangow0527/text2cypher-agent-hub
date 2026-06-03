from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.cypher_generator_agent.app.decomposition import (
    QuestionDecomposer,
    QuestionDecomposition,
    QuestionDecompositionClarification,
    QuestionDecompositionFailure,
)


class FakeProviderUnavailable(RuntimeError):
    pass


class FakeStructuredLLMClient:
    provider = "fake-llm"

    def __init__(
        self,
        responses: list[Mapping[str, Any]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.error = error
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
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)


def test_schema_violation_retries_then_returns_valid_decomposition() -> None:
    question = "收入增长情况"
    client = FakeStructuredLLMClient(
        [
            {"schema_version": "not_question_decomposition_v1"},
            _valid_payload(
                question,
                intent_type="compare",
                substantive_terms=[
                    {"text": "收入", "slot": "unknown"},
                    {"text": "增长", "slot": "unknown"},
                    {"text": "情况", "slot": "unknown"},
                ],
                output_shape="unknown",
            ),
        ]
    )

    result = QuestionDecomposer(client).decompose(question)

    assert isinstance(result, QuestionDecomposition)
    assert result.result_type == "decomposition"
    assert result.intent_type == "compare"
    assert result.output_shape == "unknown"
    assert [term.text for term in result.substantive_terms] == ["收入", "增长", "情况"]
    assert [call["attempt"] for call in client.calls] == [1, 2]
    assert all("两条" + "正交的分类轴" not in call["prompt"] for call in client.calls)
    assert all("slot" + "_terms" not in call["prompt"] for call in client.calls)
    assert all("示例 2:含字面值与过滤" in call["prompt"] for call in client.calls)


def test_schema_violation_stops_after_initial_attempt_plus_two_retries() -> None:
    client = FakeStructuredLLMClient(
        [
            {"schema_version": "wrong"},
            {"schema_version": "wrong"},
            {"schema_version": "wrong"},
        ]
    )

    result = QuestionDecomposer(client).decompose("收入增长情况")

    assert isinstance(result, QuestionDecompositionFailure)
    assert result.status == "generation_failed"
    assert result.reason == "question_decomposer_schema_invalid"
    assert result.provider == "fake-llm"
    assert result.error_type == "ValidationError"
    assert result.attempts == 3
    assert result.retry_count == 2
    assert [call["attempt"] for call in client.calls] == [1, 2, 3]


def test_missing_intent_or_output_shape_is_schema_invalid() -> None:
    client = FakeStructuredLLMClient(
        [
            {
                "schema_version": "question_decomposition_v1",
                "result_type": "decomposition",
                "original_question": "Gold 服务",
                "substantive_terms": [
                    {"text": "Gold", "slot": "filter", "attached_to": "服务"},
                    {"text": "服务", "slot": "projection"},
                ],
            }
        ]
    )

    result = QuestionDecomposer(client, max_schema_retries=0).decompose("Gold 服务")

    assert isinstance(result, QuestionDecompositionFailure)
    assert result.reason == "question_decomposer_schema_invalid"
    assert result.attempts == 1


def test_literal_candidate_requires_text_kind_hint_and_attached_to_keys() -> None:
    client = FakeStructuredLLMClient(
        [
            {
                "schema_version": "question_decomposition_v1",
                "result_type": "decomposition",
                "intent_type": "list",
                "original_question": "Gold 服务",
                "literal_candidates": [{"text": "Gold"}],
                "substantive_terms": [
                    {"text": "Gold", "slot": "filter", "attached_to": "服务"},
                    {"text": "服务", "slot": "projection"},
                ],
                "modality_terms": [],
                "time_terms": [],
                "unparsed_terms": [],
                "output_shape": "rows",
            }
        ]
    )

    result = QuestionDecomposer(client, max_schema_retries=0).decompose("Gold 服务")

    assert isinstance(result, QuestionDecompositionFailure)
    assert result.reason == "question_decomposer_schema_invalid"


def test_literal_candidate_allows_null_attached_to_and_normalizes_to_none() -> None:
    question = "查询名称为Service_003的服务"
    client = FakeStructuredLLMClient(
        [
            {
                "schema_version": "question_decomposition_v1",
                "result_type": "decomposition",
                "intent_type": "list",
                "original_question": question,
                "literal_candidates": [
                    {"text": "Service_003", "kind_hint": "id", "attached_to": None}
                ],
                "substantive_terms": [
                    {"text": "名称", "slot": "filter", "attached_to": "服务"},
                    {"text": "Service_003", "slot": "filter", "attached_to": "名称"},
                    {"text": "服务", "slot": "projection"},
                ],
                "modality_terms": [],
                "time_terms": [],
                "unparsed_terms": [],
                "output_shape": "rows",
            }
        ]
    )

    result = QuestionDecomposer(client, max_schema_retries=0).decompose(question)

    assert isinstance(result, QuestionDecomposition)
    assert result.literal_candidates[0].attached_to is None


def test_normal_decomposition_requires_explicit_result_type() -> None:
    client = FakeStructuredLLMClient(
        [
            {
                "schema_version": "question_decomposition_v1",
                "intent_type": "list",
                "original_question": "Gold 服务",
                "literal_candidates": [],
                "substantive_terms": [
                    {"text": "Gold", "slot": "filter", "attached_to": "服务"},
                    {"text": "服务", "slot": "projection"},
                ],
                "modality_terms": [],
                "time_terms": [],
                "unparsed_terms": [],
                "output_shape": "rows",
            }
        ]
    )

    result = QuestionDecomposer(client, max_schema_retries=0).decompose("Gold 服务")

    assert isinstance(result, QuestionDecompositionFailure)
    assert result.reason == "question_decomposer_schema_invalid"


def test_filter_phrases_is_rejected_as_removed_field() -> None:
    client = FakeStructuredLLMClient(
        [
            {
                **_valid_payload(
                    "Gold 服务",
                    intent_type="list",
                    substantive_terms=[
                        {"text": "Gold", "slot": "filter", "attached_to": "服务"},
                        {"text": "服务", "slot": "projection"},
                    ],
                    output_shape="rows",
                ),
                "filter_phrases": ["Gold 服务"],
            }
        ]
    )

    result = QuestionDecomposer(client, max_schema_retries=0).decompose("Gold 服务")

    assert isinstance(result, QuestionDecompositionFailure)
    assert result.reason == "question_decomposer_schema_invalid"


def test_literal_kind_hint_rejects_values_outside_enum() -> None:
    client = FakeStructuredLLMClient(
        [
            {
                **_valid_payload(
                    "Gold 服务",
                    intent_type="list",
                    substantive_terms=[
                        {"text": "Gold", "slot": "filter", "attached_to": "服务"},
                        {"text": "服务", "slot": "projection"},
                    ],
                    output_shape="rows",
                ),
                "literal_candidates": [
                    {"text": "Gold", "kind_hint": "service", "attached_to": "服务"}
                ],
            }
        ]
    )

    result = QuestionDecomposer(client, max_schema_retries=0).decompose("Gold 服务")

    assert isinstance(result, QuestionDecompositionFailure)
    assert result.reason == "question_decomposer_schema_invalid"


def test_provider_unavailable_returns_service_failed_without_deterministic_fallback() -> None:
    client = FakeStructuredLLMClient(error=FakeProviderUnavailable("provider unavailable"))

    result = QuestionDecomposer(client).decompose("大概有多少防火墙")

    assert isinstance(result, QuestionDecompositionFailure)
    assert result.status == "service_failed"
    assert result.reason == "model_invocation_failed"
    assert result.provider == "fake-llm"
    assert result.error_type == "FakeProviderUnavailable"
    assert result.attempts == 1
    assert result.retry_count == 0
    assert len(client.calls) == 1


def test_missing_referent_returns_clarification_result() -> None:
    question = "它最近 down 了吗"
    client = FakeStructuredLLMClient(
        [
            {
                "schema_version": "question_decomposition_v1",
                "result_type": "clarification_required",
                "original_question": question,
                "clarification_question": "请说明“它”指的是哪个服务、设备或端口。",
                "missing_referents": ["它"],
            }
        ]
    )

    result = QuestionDecomposer(client).decompose(question)

    assert isinstance(result, QuestionDecompositionClarification)
    assert result.status == "clarification_required"
    assert result.clarification.question == "请说明“它”指的是哪个服务、设备或端口。"
    assert result.missing_referents == ["它"]


def _valid_payload(
    question: str,
    *,
    intent_type: str = "unknown",
    substantive_terms: list[dict[str, str]],
    output_shape: str = "unknown",
) -> dict[str, Any]:
    return {
        "schema_version": "question_decomposition_v1",
        "result_type": "decomposition",
        "intent_type": intent_type,
        "original_question": question,
        "literal_candidates": [],
        "substantive_terms": substantive_terms,
        "modality_terms": [],
        "time_terms": [],
        "unparsed_terms": [],
        "output_shape": output_shape,
    }
