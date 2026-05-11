from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Literal

from .semantic_layer import SemanticLayer


SemanticDecision = Literal["accept", "clarify", "reject"]


@dataclass(frozen=True)
class SemanticMatchedFilter:
    field: str
    operator: str
    value: str | int | float | bool
    evidence: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticMatchedPath:
    path_semantic: str
    relationships: tuple[str, ...]
    evidence: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["relationships"] = list(self.relationships)
        return payload


@dataclass(frozen=True)
class SemanticMatchedReturn:
    field: str
    evidence: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticMatchedMetric:
    metric_id: str
    evidence: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticMatchResult:
    accepted: bool
    entities: tuple[str, ...] = ()
    filters: tuple[SemanticMatchedFilter, ...] = ()
    paths: tuple[SemanticMatchedPath, ...] = ()
    returns: tuple[SemanticMatchedReturn, ...] = ()
    metrics: tuple[SemanticMatchedMetric, ...] = ()
    order_by: tuple[dict[str, str], ...] = ()
    limit: int | None = None
    needs_clarification: bool = False
    clarification_type: str | None = None
    clarification_question: str | None = None
    clarification_options: tuple[dict[str, str], ...] = ()
    rejection_reason: str | None = None
    trace: tuple[str, ...] = ()
    candidate_trace: tuple[dict[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "entities": list(self.entities),
            "filters": [item.to_dict() for item in self.filters],
            "paths": [item.to_dict() for item in self.paths],
            "returns": [item.to_dict() for item in self.returns],
            "metrics": [item.to_dict() for item in self.metrics],
            "order_by": [dict(item) for item in self.order_by],
            "limit": self.limit,
            "needs_clarification": self.needs_clarification,
            "clarification_type": self.clarification_type,
            "clarification_question": self.clarification_question,
            "clarification_options": [dict(item) for item in self.clarification_options],
            "rejection_reason": self.rejection_reason,
            "trace": list(self.trace),
            "candidate_trace": [dict(item) for item in self.candidate_trace],
        }


@dataclass(frozen=True)
class SemanticViewMatchingTrace:
    source: str
    result: SemanticMatchResult
    stages: dict[str, object] = field(default_factory=dict)
    llm_disambiguation_attempts: tuple[dict[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "result": self.result.to_dict(),
            "stages": self.stages,
            "llm_disambiguation_attempts": [dict(item) for item in self.llm_disambiguation_attempts],
        }


class SemanticViewMatcher:
    """First-pass graph semantic view matcher backed by the current semantic layer resource."""

    def __init__(self, semantic_layer: SemanticLayer) -> None:
        self.semantic_layer = semantic_layer

    def match(self, question: str) -> SemanticViewMatchingTrace:
        text = _normalize_question(question)
        if _is_ambiguous_service_network_element_question(text):
            return SemanticViewMatchingTrace(
                source="semantic_layer.yaml",
                result=SemanticMatchResult(
                    accepted=False,
                    needs_clarification=True,
                    clarification_type="path_ambiguity",
                    clarification_question="你说的“对应网元”是指源网元、目的网元，还是路径经过的网元？",
                    clarification_options=(
                        {"label": "源网元", "value": "service.tunnel_source"},
                        {"label": "目的网元", "value": "service.tunnel_destination"},
                        {"label": "路径经过的网元", "value": "service.tunnel_path"},
                    ),
                    trace=("对应网元 -> 多条合法路径语义候选",),
                ),
                stages={"candidate_generation": {"decision": "clarify", "reason": "ambiguous_service_network_element"}},
            )

        entities = self._match_entities(text)
        filters = self._match_filters(text)
        paths = self._match_paths(text, entities)
        returns = self._match_returns(text, entities, paths)
        metrics = self._match_metrics(text, entities)
        order_by = self._match_order_by(text, returns, metrics)
        limit = _match_limit(text)

        entities = _complete_entities(
            entities=entities,
            filters=filters,
            paths=paths,
            returns=returns,
            metrics=metrics,
            order_by=order_by,
            semantic_layer=self.semantic_layer,
        )
        if not entities:
            return SemanticViewMatchingTrace(
                source="semantic_layer.yaml",
                result=SemanticMatchResult(
                    accepted=False,
                    rejection_reason="no_semantic_view_candidate",
                    trace=("未命中语义视图实体、字段、路径或指标",),
                ),
                stages={"candidate_generation": {"decision": "reject", "reason": "no_candidate"}},
            )

        trace = _trace_items(entities, filters, paths, returns, metrics, order_by, limit)
        result = SemanticMatchResult(
            accepted=True,
            entities=tuple(entities),
            filters=tuple(filters),
            paths=tuple(paths),
            returns=tuple(returns),
            metrics=tuple(metrics),
            order_by=tuple(order_by),
            limit=limit,
            needs_clarification=False,
            trace=tuple(trace),
            candidate_trace=tuple(_candidate_trace(entities, filters, paths, returns, metrics)),
        )
        return SemanticViewMatchingTrace(
            source="semantic_layer.yaml",
            result=result,
            stages={
                "candidate_generation": {"decision": "accept", "candidate_count": len(result.candidate_trace)},
                "semantic_completion": {"decision": "accept"},
                "candidate_ranking": {"decision": "accept", "reason": "规则唯一命中或无竞争候选"},
            },
        )

    def _match_entities(self, text: str) -> list[str]:
        matches: list[str] = []
        for entity in self.semantic_layer.entities.values():
            terms = [entity.name, entity.label, *entity.synonyms]
            if any(_contains(text, term) for term in terms):
                matches.append(entity.name)
        return _unique(matches)

    def _match_filters(self, text: str) -> list[SemanticMatchedFilter]:
        filters: list[SemanticMatchedFilter] = []
        for mapping in self.semantic_layer.value_mappings.values():
            for raw_value, normalized in mapping.values.items():
                if _contains(text, raw_value):
                    filters.append(
                        SemanticMatchedFilter(
                            field=f"{mapping.owner}.{mapping.property}",
                            operator="=",
                            value=normalized,
                            evidence=raw_value,
                        )
                    )
        name_match = re.search(r"(?:名称为|名为)([A-Za-z0-9_\\-]+)", text)
        if name_match:
            owner = _name_filter_owner(text, self._match_entities(text))
            filters.append(
                SemanticMatchedFilter(
                    field=f"{owner}.name",
                    operator="=",
                    value=name_match.group(1),
                    evidence=f"名称为{name_match.group(1)}",
                )
            )
        return filters

    def _match_paths(self, text: str, entities: list[str]) -> list[SemanticMatchedPath]:
        paths: list[SemanticMatchedPath] = []
        if _has_relationships(self.semantic_layer, ("service_uses_tunnel", "path_through", "has_port")) and (
            _contains(text, "经过网元上的端口")
            or (_contains(text, "经过网元") and _contains(text, "端口"))
            or (_contains(text, "所经过网元") and _contains(text, "端口"))
        ):
            paths.append(
                SemanticMatchedPath(
                    path_semantic="service.tunnel_path_ports",
                    relationships=("service_uses_tunnel", "path_through", "has_port"),
                    evidence=_first_evidence(text, ("经过网元上的端口", "所经过网元", "端口")),
                )
            )
        elif _has_relationships(self.semantic_layer, ("service_uses_tunnel", "path_through")) and (
            _contains(text, "经过网元")
            or _contains(text, "所经过网元")
            or _contains(text, "路径经过")
        ):
            paths.append(
                SemanticMatchedPath(
                    path_semantic="service.tunnel_path",
                    relationships=("service_uses_tunnel", "path_through"),
                    evidence=_first_evidence(text, ("所经过网元", "经过网元", "路径经过")),
                )
            )
        elif _has_relationships(self.semantic_layer, ("service_uses_tunnel", "tunnel_dst")) and (
            _contains(text, "目的端网元")
            or _contains(text, "目的网元")
            or _contains(text, "目的端")
        ):
            paths.append(
                SemanticMatchedPath(
                    path_semantic="service.tunnel_destination",
                    relationships=("service_uses_tunnel", "tunnel_dst"),
                    evidence=_first_evidence(text, ("目的端网元", "目的网元", "目的端")),
                )
            )
        elif _has_relationships(self.semantic_layer, ("service_uses_tunnel", "tunnel_src")) and (
            _contains(text, "源端网元")
            or _contains(text, "源网元")
            or _contains(text, "源端")
        ):
            paths.append(
                SemanticMatchedPath(
                    path_semantic="service.tunnel_source",
                    relationships=("service_uses_tunnel", "tunnel_src"),
                    evidence=_first_evidence(text, ("源端网元", "源网元", "源端")),
                )
            )
        elif self.semantic_layer.relationships.get("service_uses_tunnel") and (
            ("service" in entities and "tunnel" in entities)
            or _contains(text, "使用的隧道")
            or _contains(text, "服务使用隧道")
            or _contains(text, "承载")
        ):
            paths.append(
                SemanticMatchedPath(
                    path_semantic="service.uses_tunnel",
                    relationships=("service_uses_tunnel",),
                    evidence=_first_evidence(text, ("使用的隧道", "服务使用隧道", "承载", "使用")),
                )
            )
        return paths

    def _match_returns(
        self,
        text: str,
        entities: list[str],
        paths: list[SemanticMatchedPath],
    ) -> list[SemanticMatchedReturn]:
        returns: list[SemanticMatchedReturn] = []
        path_target = _path_target_entity(paths, self.semantic_layer)
        if _contains(text, "详细信息") or _contains(text, "节点信息"):
            target = path_target or (entities[-1] if entities else None)
            if target is not None:
                return [SemanticMatchedReturn(field=f"{target}.*", evidence="详细信息")]

        ordered_fields = sorted(
            self.semantic_layer.properties.values(),
            key=lambda field: (0 if field.owner in entities else 1, field.owner, field.name),
        )
        for field in ordered_fields:
            if field.property == "id" and not _contains(text, "ID") and not _contains(text, "编号"):
                continue
            terms = [field.name, f"{field.owner}.{field.property}", *field.synonyms]
            owner = self.semantic_layer.entities.get(field.owner)
            if owner is not None:
                for entity_term in (owner.name, owner.label, *owner.synonyms):
                    if field.property == "id":
                        terms.append(f"{entity_term}编号")
                    if field.property == "name":
                        terms.append(f"{entity_term}名称")
                    if field.property == "latency":
                        terms.append(f"{entity_term}时延")
                    if field.property == "elem_type":
                        terms.append(f"{entity_term}类型")
                has_generic_owner_context = _has_field_owner_context(text, owner, field.property)
                can_use_generic_field = field.owner in entities and (
                    path_target is None or has_generic_owner_context
                )
                if can_use_generic_field:
                    if field.property == "name" and _contains(text, "名称"):
                        returns.append(
                            SemanticMatchedReturn(
                                field=f"{field.owner}.{field.property}",
                                evidence=_generic_field_evidence(text, owner, field.property, "名称"),
                            )
                        )
                        continue
                    if field.property == "id" and _contains(text, "编号"):
                        returns.append(
                            SemanticMatchedReturn(
                                field=f"{field.owner}.{field.property}",
                                evidence=_generic_field_evidence(text, owner, field.property, "编号"),
                            )
                        )
                        continue
                    if field.property == "bandwidth" and _contains(text, "带宽"):
                        returns.append(
                            SemanticMatchedReturn(
                                field=f"{field.owner}.{field.property}",
                                evidence=_generic_field_evidence(text, owner, field.property, "带宽"),
                            )
                        )
                        continue
                    if field.property == "latency" and _contains(text, "时延"):
                        returns.append(
                            SemanticMatchedReturn(
                                field=f"{field.owner}.{field.property}",
                                evidence=_generic_field_evidence(text, owner, field.property, "时延"),
                            )
                        )
                        continue
                if field.property == "elem_type" and field.owner in entities and _contains(text, "类型"):
                    returns.append(SemanticMatchedReturn(field=f"{field.owner}.{field.property}", evidence="类型"))
                    continue
            if any(_contains(text, term) for term in terms):
                returns.append(SemanticMatchedReturn(field=f"{field.owner}.{field.property}", evidence=_field_evidence(text, terms)))

        if not returns and _contains(text, "所有"):
            target = _path_target_entity(paths, self.semantic_layer) or (entities[-1] if entities else None)
            if target is not None:
                returns.extend(
                    [
                        SemanticMatchedReturn(field=f"{target}.id", evidence="默认返回ID"),
                        SemanticMatchedReturn(field=f"{target}.name", evidence="默认返回名称"),
                    ]
                )
        if not returns and paths:
            last_relationship = self.semantic_layer.relationships[paths[0].relationships[-1]]
            returns.append(SemanticMatchedReturn(field=f"{last_relationship.to_entity}.name", evidence="默认返回路径终点名称"))
        return _prune_return_owners(_sort_returns_by_evidence(text, _unique_returns(returns)), entities, paths, self.semantic_layer)

    def _match_metrics(self, text: str, entities: list[str]) -> list[SemanticMatchedMetric]:
        metrics: list[SemanticMatchedMetric] = []
        for metric in self.semantic_layer.metrics.values():
            terms = [metric.name, *metric.synonyms]
            if any(_contains(text, term) for term in terms):
                metrics.append(SemanticMatchedMetric(metric_id=metric.name, evidence=_field_evidence(text, terms)))
        if not metrics and (_contains(text, "数量") or _contains(text, "多少") or _contains(text, "统计")):
            owner = _metric_owner(text, entities)
            metric_name = f"{owner}_count"
            metrics.append(SemanticMatchedMetric(metric_id=metric_name, evidence="数量"))
        return _unique_metrics(metrics)

    def _match_order_by(
        self,
        text: str,
        returns: list[SemanticMatchedReturn],
        metrics: list[SemanticMatchedMetric],
    ) -> list[dict[str, str]]:
        if metrics and (_contains(text, "升序") or _contains(text, "从小到大")):
            return [{"field": metrics[0].metric_id, "direction": "asc", "evidence": "升序"}]
        if metrics and (_contains(text, "降序") or _contains(text, "从大到小")):
            return [{"field": metrics[0].metric_id, "direction": "desc", "evidence": "降序"}]
        if metrics and (_contains(text, "最多") or _contains(text, "最高") or _contains(text, "前")):
            return [{"field": metrics[0].metric_id, "direction": "desc", "evidence": "排序词"}]
        if _contains(text, "最高") or _contains(text, "最大") or _contains(text, "前"):
            for item in returns:
                if item.field.endswith(".latency") or item.field.endswith(".bandwidth"):
                    return [{"field": item.field, "direction": "desc", "evidence": "最高/最大/前"}]
        if _contains(text, "最低") or _contains(text, "最小"):
            for item in returns:
                if item.field.endswith(".latency") or item.field.endswith(".bandwidth"):
                    return [{"field": item.field, "direction": "asc", "evidence": "最低/最小"}]
        return []


def _has_relationships(semantic_layer: SemanticLayer, relationship_names: tuple[str, ...]) -> bool:
    return all(name in semantic_layer.relationships for name in relationship_names)


def _path_target_entity(paths: list[SemanticMatchedPath], semantic_layer: SemanticLayer) -> str | None:
    if not paths:
        return None
    relationship = semantic_layer.relationships.get(paths[0].relationships[-1])
    return relationship.to_entity if relationship is not None else None


def _name_filter_owner(text: str, entities: list[str]) -> str:
    for entity_name in ("service", "tunnel", "network_element", "port", "link", "fiber", "protocol"):
        if entity_name in entities:
            return entity_name
    if _contains(text, "服务") or _contains(text, "业务") or _contains(text, "Service"):
        return "service"
    if _contains(text, "隧道") or _contains(text, "Tunnel"):
        return "tunnel"
    if _contains(text, "网元") or _contains(text, "设备"):
        return "network_element"
    return "service"


def _metric_owner(text: str, entities: list[str]) -> str:
    if _contains(text, "网元") or _contains(text, "设备") or _contains(text, "厂商"):
        return "network_element"
    if _contains(text, "端口") or _contains(text, "接口"):
        return "port"
    if _contains(text, "隧道"):
        return "tunnel"
    if _contains(text, "服务") or _contains(text, "业务"):
        return "service"
    return entities[-1] if entities else "service"


def _has_field_owner_context(text: str, owner: object, property_name: str) -> bool:
    word = {
        "id": "编号",
        "name": "名称",
        "bandwidth": "带宽",
        "latency": "时延",
        "elem_type": "类型",
    }.get(property_name)
    if not word:
        return False
    owner_name = getattr(owner, "name", "")
    owner_label = getattr(owner, "label", "")
    owner_synonyms = getattr(owner, "synonyms", ())
    for term in (owner_name, owner_label, *owner_synonyms):
        if _contains(text, f"{term}{word}") or _contains(text, f"{term}的{word}"):
            return True
        if property_name == "latency" and _contains(text, f"{term}的名称、时延"):
            return True
    return False


def _generic_field_evidence(text: str, owner: object, property_name: str, fallback: str) -> str:
    word = {
        "id": "编号",
        "name": "名称",
        "bandwidth": "带宽",
        "latency": "时延",
        "elem_type": "类型",
    }.get(property_name, fallback)
    owner_name = getattr(owner, "name", "")
    owner_label = getattr(owner, "label", "")
    owner_synonyms = getattr(owner, "synonyms", ())
    for term in (owner_name, owner_label, *owner_synonyms):
        for candidate in (f"{term}{word}", f"{term}的{word}"):
            if _contains(text, candidate):
                return candidate
    return fallback


def _prune_return_owners(
    returns: list[SemanticMatchedReturn],
    entities: list[str],
    paths: list[SemanticMatchedPath],
    semantic_layer: SemanticLayer,
) -> list[SemanticMatchedReturn]:
    if not returns:
        return []
    path_target = _path_target_entity(paths, semantic_layer)
    allowed = set(entities)
    if path_target is not None:
        allowed.add(path_target)
    if not allowed:
        return returns
    preferred = [item for item in returns if item.field.split(".", 1)[0] in allowed]
    return preferred or returns


def _sort_returns_by_evidence(text: str, returns: list[SemanticMatchedReturn]) -> list[SemanticMatchedReturn]:
    def key(item: SemanticMatchedReturn) -> tuple[int, str]:
        index = text.find(item.evidence)
        if index < 0:
            index = 10_000
        return (index, item.field)

    return sorted(returns, key=key)


def _normalize_question(question: str) -> str:
    return question.replace(" ", "").replace("\u3000", "")


def _contains(text: str, term: str | None) -> bool:
    if not term:
        return False
    return str(term).replace(" ", "") in text


def _is_ambiguous_service_network_element_question(text: str) -> bool:
    return _contains(text, "服务") and (_contains(text, "对应的网元") or _contains(text, "对应网元"))


def _complete_entities(
    *,
    entities: list[str],
    filters: list[SemanticMatchedFilter],
    paths: list[SemanticMatchedPath],
    returns: list[SemanticMatchedReturn],
    metrics: list[SemanticMatchedMetric],
    order_by: list[dict[str, str]],
    semantic_layer: SemanticLayer,
) -> list[str]:
    completed = [] if paths else list(entities)
    for path in paths:
        for relationship_name in path.relationships:
            relationship = semantic_layer.relationships.get(relationship_name)
            if relationship is not None:
                completed.extend([relationship.from_entity, relationship.to_entity])
    for item in filters:
        completed.append(item.field.split(".", 1)[0])
    for item in returns:
        completed.append(item.field.split(".", 1)[0])
    for item in metrics:
        metric = semantic_layer.metrics.get(item.metric_id)
        if metric is not None:
            completed.append(metric.owner)
    for item in order_by:
        field = item.get("field", "")
        if "." in field:
            completed.append(field.split(".", 1)[0])
        elif field in semantic_layer.metrics:
            completed.append(semantic_layer.metrics[field].owner)
    completed.extend(entities)
    return _unique(completed)


def _trace_items(
    entities: list[str],
    filters: list[SemanticMatchedFilter],
    paths: list[SemanticMatchedPath],
    returns: list[SemanticMatchedReturn],
    metrics: list[SemanticMatchedMetric],
    order_by: list[dict[str, str]],
    limit: int | None,
) -> list[str]:
    trace: list[str] = []
    trace.extend(f"entity -> {entity}" for entity in entities)
    trace.extend(f"{item.evidence} -> {item.field} {item.operator} {item.value}" for item in filters)
    trace.extend(f"{item.evidence} -> path_semantics {item.path_semantic}" for item in paths)
    trace.extend(f"{item.evidence} -> return {item.field}" for item in returns)
    trace.extend(f"{item.evidence} -> metric {item.metric_id}" for item in metrics)
    trace.extend(f"{item.get('evidence', '排序')} -> order {item.get('field')} {item.get('direction')}" for item in order_by)
    if limit is not None:
        trace.append(f"limit -> {limit}")
    return trace


def _candidate_trace(
    entities: list[str],
    filters: list[SemanticMatchedFilter],
    paths: list[SemanticMatchedPath],
    returns: list[SemanticMatchedReturn],
    metrics: list[SemanticMatchedMetric],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    rows.extend({"candidate_type": "entity", "target_id": entity, "evidence": [f"命中 {entity}"]} for entity in entities)
    rows.extend({"candidate_type": "field_value", "target_id": f"{item.field}.{item.value}", "evidence": [item.evidence]} for item in filters)
    rows.extend({"candidate_type": "path_semantic", "target_id": item.path_semantic, "evidence": [item.evidence]} for item in paths)
    rows.extend({"candidate_type": "return", "target_id": item.field, "evidence": [item.evidence]} for item in returns)
    rows.extend({"candidate_type": "metric", "target_id": item.metric_id, "evidence": [item.evidence]} for item in metrics)
    return rows


def _field_evidence(text: str, terms: list[str]) -> str:
    for term in terms:
        if _contains(text, term):
            return term
    return terms[0] if terms else ""


def _first_evidence(text: str, terms: tuple[str, ...]) -> str:
    for term in terms:
        if _contains(text, term):
            return term
    return terms[-1]


def _match_limit(text: str) -> int | None:
    match = re.search(r"(?:前|top|TOP|Top)(\d+)", text)
    if not match:
        match = re.search(r"前(\d+)个", text)
    if not match:
        return None
    return int(match.group(1))


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _unique_returns(values: list[SemanticMatchedReturn]) -> list[SemanticMatchedReturn]:
    result: list[SemanticMatchedReturn] = []
    seen: set[str] = set()
    for value in values:
        if value.field not in seen:
            seen.add(value.field)
            result.append(value)
    return result


def _unique_metrics(values: list[SemanticMatchedMetric]) -> list[SemanticMatchedMetric]:
    result: list[SemanticMatchedMetric] = []
    seen: set[str] = set()
    for value in values:
        if value.metric_id not in seen:
            seen.add(value.metric_id)
            result.append(value)
    return result
