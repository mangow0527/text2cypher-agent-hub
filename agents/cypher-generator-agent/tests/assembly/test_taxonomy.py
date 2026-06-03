from __future__ import annotations

from services.cypher_generator_agent.app.assembly.taxonomy import (
    QueryShape,
    ShapeStatus,
    classify_query_shape,
)
from services.cypher_generator_agent.app.validation.structural_requirements import (
    LimitRequirement,
    StructuralRequirements,
)


def test_classifies_zero_hop_projection_filter_and_aggregate_shapes() -> None:
    assert classify_query_shape(StructuralRequirements(projection_terms=["id"])).shape == QueryShape.F1_VERTEX_PROJECTION_0HOP

    filtered = classify_query_shape(
        StructuralRequirements(projection_terms=["id"]),
        {"substantive_terms": [{"text": "Gold", "slot": "filter"}]},
    )
    assert filtered.shape == QueryShape.F2_VERTEX_FILTER_0HOP

    aggregate = classify_query_shape(StructuralRequirements(requires_aggregate=True))
    assert aggregate.shape == QueryShape.F3_VERTEX_AGGREGATE_0HOP


def test_zero_hop_aggregate_with_filter_is_ambiguous_not_priority_guessed() -> None:
    result = classify_query_shape(
        StructuralRequirements(requires_aggregate=True),
        {"substantive_terms": [{"text": "Gold", "slot": "filter"}]},
    )

    assert result.status == ShapeStatus.AMBIGUOUS
    assert result.shape is None
    assert set(result.candidates) == {
        QueryShape.F2_VERTEX_FILTER_0HOP,
        QueryShape.F3_VERTEX_AGGREGATE_0HOP,
    }


def test_zero_hop_property_count_object_terms_do_not_make_aggregate_shape_ambiguous() -> None:
    result = classify_query_shape(
        StructuralRequirements(requires_aggregate=True),
        {
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "服务质量", "slot": "filter", "attached_to": "服务"},
                {"text": "属性", "slot": "filter", "attached_to": "服务质量"},
                {"text": "数量", "slot": "projection"},
            ],
        },
    )

    assert result.status == ShapeStatus.RESOLVED
    assert result.shape == QueryShape.F3_VERTEX_AGGREGATE_0HOP


def test_zero_hop_property_value_count_uses_count_intent_when_quantity_word_was_merged() -> None:
    result = classify_query_shape(
        StructuralRequirements(requires_aggregate=True),
        {
            "intent_type": "count",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "ID", "slot": "filter", "attached_to": "服务"},
                {"text": "属性", "slot": "filter", "attached_to": "服务"},
                {"text": "值", "slot": "projection"},
            ],
        },
    )

    assert result.status == ShapeStatus.RESOLVED
    assert result.shape == QueryShape.F3_VERTEX_AGGREGATE_0HOP


def test_classifies_multihop_projection_filter_and_group_topn_shapes() -> None:
    assert (
        classify_query_shape(StructuralRequirements(min_path_hops=1, projection_terms=["id"])).shape
        == QueryShape.F4_PATH_PROJECTION_MULTIHOP
    )

    filtered = classify_query_shape(
        StructuralRequirements(min_path_hops=2),
        {"literal_hints": ["beijing"]},
    )
    assert filtered.shape == QueryShape.F5_PATH_FILTER_MULTIHOP

    topn = classify_query_shape(
        StructuralRequirements(
            min_path_hops=2,
            requires_aggregate=True,
            requires_group_by=True,
            requires_order_by=True,
            requires_limit=LimitRequirement(required=True, value=3),
        )
    )
    assert topn.shape == QueryShape.F6_PATH_GROUP_TOPN


def test_recognizes_explicit_two_stage_aggregate_only_from_decomposition_signal() -> None:
    result = classify_query_shape(
        StructuralRequirements(min_path_hops=2, requires_aggregate=True),
        {"query_shape": "two_stage_aggregate"},
    )

    assert result.status == ShapeStatus.RESOLVED
    assert result.shape == QueryShape.F8_TWO_STAGE_AGGREGATE


def test_unsupported_when_requirements_do_not_fit_any_unique_shape() -> None:
    result = classify_query_shape(StructuralRequirements(requires_group_by=True))

    assert result.status == ShapeStatus.UNSUPPORTED
    assert result.shape is None
