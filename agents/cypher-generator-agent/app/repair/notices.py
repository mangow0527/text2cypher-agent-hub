from __future__ import annotations

from typing import Any, Iterable

from .models import RepairAssumption


def render_user_visible_notices(assumptions: Iterable[RepairAssumption | dict[str, Any]]) -> list[str]:
    notices: list[str] = []
    for raw_assumption in assumptions:
        if not _is_user_visible_assumption(raw_assumption):
            continue
        assumption = (
            raw_assumption
            if isinstance(raw_assumption, RepairAssumption)
            else RepairAssumption.model_validate(raw_assumption)
        )
        notice = _render_assumption(assumption)
        if notice:
            notices.append(notice)
    return notices


def _is_user_visible_assumption(raw_assumption: RepairAssumption | dict[str, Any]) -> bool:
    if isinstance(raw_assumption, RepairAssumption):
        return raw_assumption.kind in {"literal_binding", "modality_warning"}
    kind = raw_assumption.get("kind") or raw_assumption.get("type")
    return kind in {"literal_binding", "modality_warning"}


def _render_assumption(assumption: RepairAssumption) -> str | None:
    if assumption.kind == "literal_binding" and assumption.raw and assumption.assumed_as is not None:
        property_label = _property_label(assumption.property)
        return f"我把“{assumption.raw}”理解为{property_label} {assumption.assumed_as}。"
    if assumption.kind == "modality_warning" and assumption.term:
        return f"问题中的“{assumption.term}”没有被解释为查询约束。"
    return None


def _property_label(property_name: str | None) -> str:
    labels = {
        "NetworkElement.elem_type": "设备类型",
        "Service.quality_of_service": "服务等级",
        "Service.service_type": "服务类型",
        "Port.status": "端口状态",
    }
    if property_name in labels:
        return labels[property_name]
    return property_name or "字面值"
