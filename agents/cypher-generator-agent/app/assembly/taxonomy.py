from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from services.cypher_generator_agent.app.validation.structural_requirements import StructuralRequirements


class QueryShape(StrEnum):
    F1_VERTEX_PROJECTION_0HOP = "F1 vertex_projection_0hop"
    F2_VERTEX_FILTER_0HOP = "F2 vertex_filter_0hop"
    F3_VERTEX_AGGREGATE_0HOP = "F3 vertex_aggregate_0hop"
    F4_PATH_PROJECTION_MULTIHOP = "F4 path_projection_multihop"
    F5_PATH_FILTER_MULTIHOP = "F5 path_filter_multihop"
    F6_PATH_GROUP_TOPN = "F6 path_group_topn"
    F8_TWO_STAGE_AGGREGATE = "F8 two_stage_aggregate"


class ShapeStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "shape_ambiguous"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class QueryShapeResult:
    status: ShapeStatus
    shape: QueryShape | None = None
    candidates: tuple[QueryShape, ...] = field(default_factory=tuple)
    reason: str | None = None


def classify_query_shape(
    structural_requirements: StructuralRequirements | Mapping[str, Any],
    decomposition: Mapping[str, Any] | None = None,
) -> QueryShapeResult:
    requirements = _requirements(structural_requirements)
    decomposition = decomposition or {}

    if _is_explicit_two_stage(decomposition):
        return QueryShapeResult(status=ShapeStatus.RESOLVED, shape=QueryShape.F8_TWO_STAGE_AGGREGATE)

    candidates = _zero_hop_candidates(requirements, decomposition) if requirements.min_path_hops == 0 else []
    if requirements.min_path_hops > 0:
        candidates = _multihop_candidates(requirements, decomposition)

    if len(candidates) == 1:
        return QueryShapeResult(status=ShapeStatus.RESOLVED, shape=candidates[0])
    if len(candidates) > 1:
        return QueryShapeResult(
            status=ShapeStatus.AMBIGUOUS,
            candidates=tuple(candidates),
            reason="multiple_shape_signals",
        )
    return QueryShapeResult(status=ShapeStatus.UNSUPPORTED, reason="no_supported_shape")


def _requirements(requirements: StructuralRequirements | Mapping[str, Any]) -> StructuralRequirements:
    if isinstance(requirements, StructuralRequirements):
        return requirements
    return StructuralRequirements.model_validate(requirements)


def _zero_hop_candidates(requirements: StructuralRequirements, decomposition: Mapping[str, Any]) -> list[QueryShape]:
    candidates: list[QueryShape] = []
    if requirements.requires_aggregate and not _has_group_topn_signal(requirements):
        candidates.append(QueryShape.F3_VERTEX_AGGREGATE_0HOP)
    filter_signal = _has_filter_or_literal_hint(decomposition)
    if requirements.requires_aggregate and _is_count_intent(decomposition):
        filter_signal = False
    if requirements.requires_aggregate and _has_property_count_object_signal(decomposition):
        filter_signal = _has_explicit_literal_hint(decomposition)
    if filter_signal:
        candidates.append(QueryShape.F2_VERTEX_FILTER_0HOP)
    if not candidates and (requirements.projection_terms or _has_object_info_projection_hint(decomposition)):
        candidates.append(QueryShape.F1_VERTEX_PROJECTION_0HOP)
    return candidates


def _multihop_candidates(requirements: StructuralRequirements, decomposition: Mapping[str, Any]) -> list[QueryShape]:
    candidates: list[QueryShape] = []
    if requirements.requires_aggregate and _has_group_topn_signal(requirements):
        candidates.append(QueryShape.F6_PATH_GROUP_TOPN)
    if _has_filter_or_literal_hint(decomposition):
        candidates.append(QueryShape.F5_PATH_FILTER_MULTIHOP)
    if not candidates:
        candidates.append(QueryShape.F4_PATH_PROJECTION_MULTIHOP)
    return candidates


def _has_group_topn_signal(requirements: StructuralRequirements) -> bool:
    return requirements.requires_group_by or requirements.requires_order_by or requirements.requires_limit.required


def _has_filter_or_literal_hint(decomposition: Mapping[str, Any]) -> bool:
    if _truthy_sequence(decomposition.get("literal_hints")) or _truthy_sequence(decomposition.get("filter_terms")):
        return True
    raw_terms = decomposition.get("substantive_terms")
    if not isinstance(raw_terms, list | tuple):
        return False
    return any(isinstance(term, Mapping) and term.get("slot") in {"filter", "literal", "value"} for term in raw_terms)


def _has_explicit_literal_hint(decomposition: Mapping[str, Any]) -> bool:
    return any(
        _truthy_sequence(decomposition.get(key))
        for key in ("literal_hints", "literal_candidates", "literal_candidate_objects")
    )


def _has_property_count_object_signal(decomposition: Mapping[str, Any]) -> bool:
    raw_terms = decomposition.get("substantive_terms")
    if not isinstance(raw_terms, list | tuple):
        return False
    texts = [
        str(term.get("text") or "").strip()
        for term in raw_terms
        if isinstance(term, Mapping)
    ]
    return any(_is_property_count_modifier_text(text) for text in texts) and (
        any(_is_quantity_text(text) for text in texts) or _is_count_intent(decomposition)
    )


def _has_object_info_projection_hint(decomposition: Mapping[str, Any]) -> bool:
    question = _compact_text(decomposition.get("original_question") or decomposition.get("question") or "")
    raw_terms = decomposition.get("substantive_terms")
    if not question or not isinstance(raw_terms, list | tuple):
        return False
    projection_terms = [
        _compact_text(term.get("text") or "")
        for term in raw_terms
        if isinstance(term, Mapping) and term.get("slot") == "projection"
    ]
    projection_terms = [term for term in projection_terms if term]
    if not projection_terms:
        return False
    return any(
        f"{term}{suffix}" in question
        for term in projection_terms
        for suffix in _OBJECT_INFO_PROJECTION_SUFFIXES
    )


def _is_property_count_modifier_text(text: str) -> bool:
    return text.strip() in _PROPERTY_COUNT_MODIFIER_TERMS


def _is_quantity_text(text: str) -> bool:
    return any(marker in text for marker in ("数量", "个数", "总数", "多少", "次数", "频率", "count", "Count"))


def _is_count_intent(decomposition: Mapping[str, Any]) -> bool:
    return str(decomposition.get("intent_type") or "").strip() == "count" or str(
        decomposition.get("output_shape") or ""
    ).strip() == "scalar"


def _truthy_sequence(value: Any) -> bool:
    return isinstance(value, list | tuple | set) and bool(value)


def _compact_text(value: Any) -> str:
    return str(value or "").casefold().replace(" ", "").replace("_", "").replace("-", "")


def _is_explicit_two_stage(decomposition: Mapping[str, Any]) -> bool:
    values = {
        str(decomposition.get("query_shape") or "").strip().lower(),
        str(decomposition.get("intent_type") or "").strip().lower(),
    }
    return bool(values & {"two_stage_aggregate", "two-stage-aggregate", "two stage aggregate"})


_PROPERTY_COUNT_MODIFIER_TERMS = {
    "属性",
    "属性值",
    "属性记录",
    "记录",
    "非空值",
    "字段值",
    "值",
    "条目",
}

_OBJECT_INFO_PROJECTION_SUFFIXES = ("信息", "详情", "详细信息", "完整信息", "全部信息", "所有信息")
