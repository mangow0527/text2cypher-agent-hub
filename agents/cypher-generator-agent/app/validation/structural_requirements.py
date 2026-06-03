from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


STRUCTURAL_REQUIREMENTS_SCHEMA_VERSION = "structural_requirements_v1"


class StructuralRequirementBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StructuralTerm(StructuralRequirementBase):
    text: str
    slot: str
    attached_to: str | None = None
    position: int | None = None
    order_index: int


class LimitRequirement(StructuralRequirementBase):
    required: bool = False
    value: int | None = None


class StructuralRequirements(StructuralRequirementBase):
    schema_version: Literal["structural_requirements_v1"] = STRUCTURAL_REQUIREMENTS_SCHEMA_VERSION
    requires_aggregate: bool = False
    requires_group_by: bool = False
    requires_order_by: bool = False
    order_direction: Literal["asc", "desc"] | None = None
    requires_limit: LimitRequirement = Field(default_factory=LimitRequirement)
    path_terms: list[StructuralTerm] = Field(default_factory=list)
    path_order_confidence: Literal["high", "low"] = "high"
    min_path_hops: int = 0
    projection_terms: list[str] = Field(default_factory=list)


class DslStructuralCoverageResult(StructuralRequirementBase):
    schema_version: Literal["dsl_structural_coverage_result_v1"] = "dsl_structural_coverage_result_v1"
    is_valid: bool = True
    missing: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def sync_validity(self) -> "DslStructuralCoverageResult":
        self.is_valid = not self.missing
        return self


def derive_structural_requirements(decomposition: Mapping[str, Any]) -> StructuralRequirements:
    terms = _substantive_terms(decomposition)
    positions = _term_positions(str(decomposition.get("original_question") or ""), terms)

    path_terms = _path_terms(terms, positions)
    projection_terms = _projection_slot_texts(terms)
    limit_terms = [term for term in terms if term.get("slot") == "limit"]
    order_terms = [term for term in terms if term.get("slot") == "order_by"]

    return StructuralRequirements(
        requires_aggregate=_requires_aggregate(decomposition, terms),
        requires_group_by=any(term.get("slot") == "group_by" for term in terms),
        requires_order_by=bool(order_terms),
        order_direction=_order_direction(order_terms),
        requires_limit=LimitRequirement(
            required=bool(limit_terms),
            value=_limit_value(limit_terms),
        ),
        path_terms=path_terms,
        path_order_confidence="low" if any(term.position is None for term in path_terms) else "high",
        min_path_hops=_minimum_path_hops(path_terms),
        projection_terms=projection_terms,
    )


def validate_dsl_structural_coverage(
    requirements: StructuralRequirements | Mapping[str, Any],
    dsl: Mapping[str, Any],
) -> DslStructuralCoverageResult:
    req = (
        requirements
        if isinstance(requirements, StructuralRequirements)
        else StructuralRequirements.model_validate(requirements)
    )
    operations = _flatten_operations(dsl.get("operations"))
    missing: list[dict[str, Any]] = []

    if req.requires_aggregate and not _has_aggregate(operations, dsl):
        missing.append(_missing("aggregate_required", "Question requires aggregation but DSL has no aggregate operation."))

    if req.requires_group_by and not _has_group_by(operations):
        missing.append(_missing("group_by_required", "Question requires group_by but DSL aggregate has no group_by."))

    if req.requires_order_by:
        sort_directions = _sort_directions(operations, dsl)
        if not sort_directions:
            missing.append(_missing("order_by_required", "Question requires ordering but DSL has no sort operation."))
        elif req.order_direction is not None and req.order_direction not in sort_directions:
            missing.append(
                _missing(
                    "order_by_direction_mismatch",
                    f"Question requires {req.order_direction} ordering but DSL sort directions are {sort_directions}.",
                    expected=req.order_direction,
                    actual=sort_directions,
                )
            )

    if req.requires_limit.required:
        limits = _limit_values(operations, dsl)
        if not limits:
            missing.append(_missing("limit_required", "Question requires limit but DSL has no limit operation."))
        elif req.requires_limit.value is not None and req.requires_limit.value not in limits:
            missing.append(
                _missing(
                    "limit_value_mismatch",
                    f"Question requires limit {req.requires_limit.value} but DSL limits are {limits}.",
                    expected=req.requires_limit.value,
                    actual=limits,
                )
            )

    if req.min_path_hops > 0:
        actual_hops = _path_hop_capacity(operations)
        if actual_hops < req.min_path_hops:
            missing.append(
                _missing(
                    "path_hops_insufficient",
                    f"Question path terms require at least {req.min_path_hops} hop(s) but DSL covers {actual_hops}.",
                    required_min_hops=req.min_path_hops,
                    actual_hops=actual_hops,
                    order_confidence=req.path_order_confidence,
                    order_checked=req.path_order_confidence == "high",
                    path_terms=[term.model_dump(mode="json") for term in req.path_terms],
                )
            )

    if req.projection_terms:
        projection_items = _projection_items(dsl)
        if not projection_items:
            missing.append(
                _missing(
                    "projection_required",
                    "Question requires projection terms but DSL projection is empty.",
                    projection_terms=req.projection_terms,
                )
            )
        else:
            covered_terms = _covered_projection_terms(
                projection_items,
                required_terms=req.projection_terms,
            )
            if _has_measure_projection(projection_items):
                for term in req.projection_terms:
                    if _is_quantity_projection_text(term) and term not in covered_terms:
                        covered_terms.append(term)
                    if (
                        _is_node_count_projection_text(term)
                        and term not in covered_terms
                        and _has_count_measure(operations)
                    ):
                        covered_terms.append(term)
            required_projection_terms = _projection_terms_requiring_coverage(
                req.projection_terms,
                covered_terms=covered_terms,
            )
            for term in req.projection_terms:
                if term not in required_projection_terms and term not in covered_terms:
                    covered_terms.append(term)
            uncovered_terms = [term for term in required_projection_terms if term not in covered_terms]
            if uncovered_terms:
                missing.append(
                    _missing(
                        "projection_terms_uncovered",
                        "Question requires projection terms that are not covered by DSL projection items.",
                        projection_terms=req.projection_terms,
                        covered=covered_terms,
                        uncovered=uncovered_terms,
                    )
                )

    return DslStructuralCoverageResult(missing=missing)


def structural_coverage_issue(result: DslStructuralCoverageResult, requirements: StructuralRequirements) -> dict[str, Any]:
    return {
        "code": "structural_coverage_missing",
        "message": "DSL does not cover all structural requirements derived from decomposition.",
        "severity": "error",
        "recoverability": "repairable",
        "action": "repair_binding",
        "details": {
            "missing": result.missing,
            "structural_requirements": requirements.model_dump(mode="json"),
        },
    }


def _substantive_terms(decomposition: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_terms = decomposition.get("substantive_terms")
    if not isinstance(raw_terms, list | tuple):
        return []
    terms: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None]] = set()
    for item in raw_terms:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "").strip()
        slot = str(item.get("slot") or "unknown").strip() or "unknown"
        attached_to = str(item.get("attached_to") or "").strip() or None
        key = (text, slot, attached_to)
        if not text or key in seen:
            continue
        payload: dict[str, Any] = {"text": text, "slot": slot}
        if attached_to:
            payload["attached_to"] = attached_to
        terms.append(payload)
        seen.add(key)
    return terms


def _slot_texts(terms: list[Mapping[str, Any]], slot: str) -> list[str]:
    values: list[str] = []
    for term in terms:
        if term.get("slot") != slot:
            continue
        text = str(term.get("text") or "").strip()
        if text and text not in values:
            values.append(text)
    return values


def _projection_slot_texts(terms: list[Mapping[str, Any]]) -> list[str]:
    attached_projection_owners = {
        str(term.get("attached_to") or "").strip()
        for term in terms
        if term.get("slot") == "projection" and str(term.get("attached_to") or "").strip()
    }
    values: list[str] = []
    for term in terms:
        if term.get("slot") != "projection":
            continue
        text = str(term.get("text") or "").strip()
        if not text or text in attached_projection_owners:
            continue
        if not _is_required_projection_text(text, attached_to=term.get("attached_to")):
            continue
        if text not in values:
            values.append(text)
    return values


def _is_required_projection_text(text: str, *, attached_to: Any) -> bool:
    if str(attached_to or "").strip():
        return True
    normalized = text.strip()
    if normalized in {"统计", "查询", "返回", "多少", "几", "几个", "几台", "台", "个"}:
        return False
    field_markers = (
        "ID",
        "id",
        "编号",
        "名称",
        "名字",
        "带宽",
        "时延",
        "延迟",
        "状态",
        "类型",
        "等级",
        "版本",
        "厂商",
        "厂家",
        "位置",
        "地址",
        "IP",
        "ip",
        "节点",
        "详细",
        "详情",
        "完整",
        "全部属性",
        "所有属性",
        "标准",
        "数量",
        "个数",
        "总数",
    )
    return any(marker in normalized for marker in field_markers)


def _path_terms(terms: list[dict[str, Any]], positions: dict[int, int | None]) -> list[StructuralTerm]:
    indexed_terms = [
        (index, term, positions.get(index))
        for index, term in enumerate(terms)
        if term.get("slot") == "path"
    ]
    if indexed_terms and all(position is not None for _, _, position in indexed_terms):
        indexed_terms.sort(key=lambda item: (int(item[2]), item[0]))

    path_terms: list[StructuralTerm] = []
    for order_index, (original_index, term, position) in enumerate(indexed_terms):
        path_terms.append(
            StructuralTerm(
                text=str(term["text"]),
                slot="path",
                attached_to=term.get("attached_to"),
                position=position,
                order_index=order_index,
            )
        )
    return path_terms


def _term_positions(question: str, terms: list[dict[str, Any]]) -> dict[int, int | None]:
    positions: dict[int, int | None] = {index: None for index in range(len(terms))}
    if not question:
        return positions

    occupied: list[tuple[int, int]] = []
    for index, term in sorted(
        enumerate(terms),
        key=lambda item: (-len(str(item[1].get("text") or "")), item[0]),
    ):
        text = str(term.get("text") or "").strip()
        if not text:
            continue
        position = _find_non_overlapping(question, text, occupied)
        if position is None:
            continue
        positions[index] = position
        occupied.append((position, position + len(text)))
    return positions


def _find_non_overlapping(question: str, term: str, occupied: list[tuple[int, int]]) -> int | None:
    start = question.find(term)
    while start != -1:
        end = start + len(term)
        if not any(start < used_end and end > used_start for used_start, used_end in occupied):
            return start
        start = question.find(term, start + 1)
    return None


def _requires_aggregate(decomposition: Mapping[str, Any], terms: list[Mapping[str, Any]]) -> bool:
    intent = str(decomposition.get("intent_type") or "").strip()
    output_shape = str(decomposition.get("output_shape") or "").strip()
    if intent in {"count", "aggregate", "top_n"} or output_shape == "grouped_rows":
        return True
    aggregate_terms = {"统计", "数量", "多少", "个数", "总数"}
    return any(str(term.get("text") or "").strip() in aggregate_terms for term in terms)


def _order_direction(order_terms: list[Mapping[str, Any]]) -> Literal["asc", "desc"] | None:
    text = " ".join(str(term.get("text") or "") for term in order_terms)
    if any(token in text for token in ("降序", "最多", "最高", "最大", "前", "Top", "top", "高到低", "从高到低")):
        return "desc"
    if any(token in text for token in ("升序", "最少", "最低", "最小", "低到高", "从低到高")):
        return "asc"
    return None


def _limit_value(limit_terms: list[Mapping[str, Any]]) -> int | None:
    for term in limit_terms:
        match = re.search(r"\d+", str(term.get("text") or ""))
        if match is not None:
            value = int(match.group(0))
            if value > 0:
                return value
    return None


def _minimum_path_hops(path_terms: list[StructuralTerm]) -> int:
    anchor_terms = _path_anchor_terms(path_terms)
    if len(anchor_terms) < 2:
        return 0
    return len(anchor_terms) - 1


def _path_anchor_terms(path_terms: list[StructuralTerm]) -> list[StructuralTerm]:
    anchors = [
        term
        for term in path_terms
        if not _is_relation_only_path_text(term.text)
    ]
    normalized = [_norm_text(term.text) for term in anchors]
    compact: list[StructuralTerm] = []
    for index, term in enumerate(anchors):
        text = normalized[index]
        if any(
            other
            and other != text
            and other in text
            for other_index, other in enumerate(normalized)
            if other_index != index
        ):
            continue
        compact.append(term)
    return compact


def _is_relation_only_path_text(text: str) -> bool:
    normalized = text.strip()
    return normalized in {
        "使用",
        "所用",
        "所使用",
        "经",
        "所经",
        "经过",
        "穿过",
        "关联",
        "关联关系",
        "对应",
        "对应关系",
        "连接关系",
        "之间",
        "节点",
        "目的",
        "源",
        "到达",
        "达到",
        "连接",
    }


def _flatten_operations(raw_operations: Any) -> list[Mapping[str, Any]]:
    if not isinstance(raw_operations, list | tuple):
        return []
    operations: list[Mapping[str, Any]] = []
    for item in raw_operations:
        if not isinstance(item, Mapping):
            continue
        operations.append(item)
        operations.extend(_flatten_operations(item.get("operations")))
    return operations


def _has_aggregate(operations: list[Mapping[str, Any]], dsl: Mapping[str, Any]) -> bool:
    if str(dsl.get("query_shape") or "") in {"metric_aggregate", "ad_hoc_aggregate", "top_n", "two_step_aggregate"}:
        return True
    return any(str(op.get("op") or "") in {"aggregate", "metric_aggregate", "subquery"} for op in operations)


def _has_group_by(operations: list[Mapping[str, Any]]) -> bool:
    return any(isinstance(op.get("group_by"), list) and bool(op.get("group_by")) for op in operations)


def _sort_directions(operations: list[Mapping[str, Any]], dsl: Mapping[str, Any]) -> list[str]:
    directions: list[str] = []
    for item in _sort_items(operations, dsl):
        direction = str(item.get("direction") or "").strip()
        if direction and direction not in directions:
            directions.append(direction)
    return directions


def _sort_items(operations: list[Mapping[str, Any]], dsl: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items: list[Mapping[str, Any]] = []
    for op in operations:
        if op.get("op") != "sort":
            continue
        raw_items = op.get("by")
        if isinstance(raw_items, list | tuple):
            items.extend(item for item in raw_items if isinstance(item, Mapping))
    raw_order_by = dsl.get("order_by")
    if isinstance(raw_order_by, list | tuple):
        items.extend(item for item in raw_order_by if isinstance(item, Mapping))
    return items


def _limit_values(operations: list[Mapping[str, Any]], dsl: Mapping[str, Any]) -> list[int]:
    values: list[int] = []
    for op in operations:
        if op.get("op") != "limit":
            continue
        value = _positive_int(op.get("value"))
        if value is not None and value not in values:
            values.append(value)
    top_level = _positive_int(dsl.get("limit"))
    if top_level is not None and top_level not in values:
        values.append(top_level)
    return values


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _path_hop_capacity(operations: list[Mapping[str, Any]]) -> int:
    capacity = 0
    for op in operations:
        op_name = str(op.get("op") or "")
        if op_name == "traverse_edge":
            capacity += 1
            continue
        if op_name == "variable_path":
            capacity = max(capacity, _positive_int(op.get("max_hops")) or 1)
            continue
        if op_name == "use_path_pattern":
            capacity = max(capacity, 99)
    return capacity


def _projection_items(dsl: Mapping[str, Any]) -> list[Any]:
    projection = dsl.get("projection")
    if not isinstance(projection, Mapping):
        return []
    items = projection.get("items")
    if not isinstance(items, list | tuple):
        return []
    return list(items)


def _covered_projection_terms(items: list[Any], *, required_terms: list[str] | None = None) -> list[str]:
    covered: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        raw_terms = item.get("projection_terms")
        if isinstance(raw_terms, list | tuple):
            for raw_term in raw_terms:
                term = str(raw_term).strip()
                if term and term not in covered:
                    covered.append(term)
        for term in required_terms or []:
            if term not in covered and _projection_item_covers_term(item, term):
                covered.append(term)
    return covered


def _projection_item_covers_term(item: Mapping[str, Any], term: str) -> bool:
    normalized_term = _norm_text(term)
    if not normalized_term:
        return False
    surfaces = _projection_item_surfaces(item)
    return any(_surface_matches_projection_term(surface, normalized_term) for surface in surfaces)


def _projection_item_surfaces(item: Mapping[str, Any]) -> list[str]:
    surfaces: list[str] = []
    for key in ("alias", "source", "name", "semantic_id"):
        raw = item.get(key)
        if raw is not None:
            surfaces.append(str(raw))
    if item.get("vertex_full"):
        surfaces.extend(_VERTEX_FULL_PROJECTION_TERM_SURFACES)
    prop = item.get("property")
    owner = item.get("owner")
    prop_name: Any = item.get("property_name")
    if isinstance(prop, Mapping):
        owner = prop.get("owner") or owner
        prop_name = prop.get("name") or prop.get("property_name") or prop_name
    elif isinstance(prop, str):
        prop_name = prop
    if prop_name is not None:
        prop_text = str(prop_name)
        surfaces.append(prop_text)
        if owner is not None:
            surfaces.append(f"{owner}.{prop_text}")
        surfaces.extend(_PROPERTY_PROJECTION_TERM_SURFACES.get(prop_text, ()))
    return surfaces


def _surface_matches_projection_term(surface: str, normalized_term: str) -> bool:
    normalized_surface = _norm_text(surface)
    if not normalized_surface:
        return False
    return normalized_surface == normalized_term or normalized_term in normalized_surface


def _projection_terms_requiring_coverage(required_terms: list[str], *, covered_terms: list[str]) -> list[str]:
    concrete_terms = [term for term in required_terms if not _is_closed_generic_projection_modifier(term)]
    has_covered_concrete_term = any(term in covered_terms for term in concrete_terms)
    if not has_covered_concrete_term:
        return list(required_terms)
    return [term for term in required_terms if not _is_closed_generic_projection_modifier(term)]


def _is_closed_generic_projection_modifier(term: str) -> bool:
    return term.strip() in _CLOSED_GENERIC_PROJECTION_MODIFIER_TERMS


def _norm_text(value: Any) -> str:
    return str(value or "").casefold().strip().replace("_", "").replace("-", "").replace(" ", "")


_CLOSED_GENERIC_PROJECTION_MODIFIER_TERMS = {
    "实例",
    "服务节点",
    "业务节点",
    "属性",
    "属性值",
    "属性记录",
    "记录",
    "值",
    "非空值",
}


_PROPERTY_PROJECTION_TERM_SURFACES = {
    "id": ("ID", "id", "编号", "服务ID", "服务编号", "隧道ID", "隧道编号", "网元ID", "网元编号", "端口ID", "端口编号", "链路ID", "链路编号", "协议ID", "协议编号", "光纤ID", "光纤编号"),
    "name": ("名称", "名字", "服务名称"),
    "elem_type": ("类型", "网元类型", "元素类型", "设备类型", "服务类型", "型号"),
    "quality_of_service": ("服务质量等级", "质量等级", "等级", "等级值"),
    "bandwidth": ("带宽",),
    "latency": ("时延", "延迟", "延迟值"),
    "ietf_standard": ("IETF标准", "标准"),
    "ip_address": ("IP地址", "地址"),
    "software_version": ("软件版本", "版本"),
    "vendor": ("厂商", "厂家", "厂商信息", "厂家信息"),
    "location": ("位置",),
    "status": ("状态",),
}


_VERTEX_FULL_PROJECTION_TERM_SURFACES = (
    "节点",
    "信息",
    "节点信息",
    "详细信息",
    "详情",
    "完整信息",
    "全部信息",
    "所有信息",
    "全部属性",
    "全部属性信息",
    "所有属性",
    "所有属性信息",
)


def _has_measure_projection(items: list[Any]) -> bool:
    for item in items:
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("source") or "")
        alias = str(item.get("alias") or "")
        if source.startswith(("measure.", "metric.")):
            return True
        if "count" in source or "count" in alias:
            return True
    return False


def _is_quantity_projection_text(term: str) -> bool:
    return any(marker in term for marker in ("数量", "个数", "总数", "多少"))


def _is_node_count_projection_text(term: str) -> bool:
    return term.strip() in {"节点", "节点数", "节点数量", "节点总数"}


def _has_count_measure(operations: list[Mapping[str, Any]]) -> bool:
    for op in operations:
        if str(op.get("op") or "") == "metric_aggregate" and "count" in str(op.get("metric_name") or ""):
            return True
        raw_measures = op.get("measures")
        if not isinstance(raw_measures, list | tuple):
            continue
        for measure in raw_measures:
            if not isinstance(measure, Mapping):
                continue
            if str(measure.get("function") or "").strip().lower() == "count":
                return True
    return False


def _missing(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "details": details,
    }
