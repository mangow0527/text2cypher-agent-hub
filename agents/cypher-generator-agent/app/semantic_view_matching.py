from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Literal

from .graph_semantic_view import GraphSemanticView, SemanticEntity, normalize_question


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
    """Graph semantic view matcher backed by the governed network graph semantic view."""

    def __init__(self, semantic_view: GraphSemanticView) -> None:
        self.semantic_view = semantic_view

    def match(self, question: str) -> SemanticViewMatchingTrace:
        text = normalize_question(question)
        entities = self._match_entities(text)
        filters = self._match_filters(text)
        paths = self._match_paths(text, entities)
        if len(paths) > 1:
            return SemanticViewMatchingTrace(
                source="network_graph_semantic_view.yaml",
                result=SemanticMatchResult(
                    accepted=False,
                    entities=tuple(entities),
                    paths=tuple(paths),
                    needs_clarification=True,
                    clarification_type="path_ambiguity",
                    clarification_question="你说的“对应网元”是指源网元、目的网元，还是路径经过的网元？",
                    clarification_options=(
                        {"label": "源网元", "value": "service.tunnel_source"},
                        {"label": "目的网元", "value": "service.tunnel_destination"},
                        {"label": "路径经过的网元", "value": "service.tunnel_path"},
                    ),
                    trace=("网元路径语义未唯一命中 -> 多条合法路径语义候选",),
                    candidate_trace=tuple(_candidate_trace(entities, filters, paths, [], [])),
                ),
                stages={
                    "candidate_generation": {"decision": "clarify", "reason": "ambiguous_service_network_element"},
                    "disambiguation_rules": [
                        rule.rule_id
                        for rule in self.semantic_view.disambiguation_rules
                        if rule.prefer in {path.path_semantic for path in paths}
                    ],
                },
            )
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
            semantic_view=self.semantic_view,
        )
        if not entities:
            return SemanticViewMatchingTrace(
                source="network_graph_semantic_view.yaml",
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
            source="network_graph_semantic_view.yaml",
            result=result,
            stages={
                "candidate_generation": {"decision": "accept", "candidate_count": len(result.candidate_trace)},
                "semantic_completion": {"decision": "accept"},
                "candidate_ranking": {"decision": "accept", "reason": "语义视图规则唯一命中或无竞争候选"},
            },
        )

    def _match_entities(self, text: str) -> list[str]:
        matches: list[str] = []
        for entity in self.semantic_view.entities.values():
            terms = [entity.name, entity.name_zh, entity.label, *entity.synonyms]
            if any(_contains(text, term) for term in terms):
                matches.append(entity.name)
        return _unique(matches)

    def _match_filters(self, text: str) -> list[SemanticMatchedFilter]:
        filters: list[SemanticMatchedFilter] = []
        for field in self.semantic_view.fields.values():
            if not field.value_aliases:
                continue
            for normalized, aliases in field.value_aliases.items():
                for raw_value in (normalized, *aliases):
                    if not _contains(text, raw_value):
                        continue
                    filters.append(
                        SemanticMatchedFilter(
                            field=field.name,
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
        filters.extend(_match_numeric_filters(text, self._match_entities(text), self.semantic_view))
        return _unique_filters(filters)

    def _match_paths(self, text: str, entities: list[str]) -> list[SemanticMatchedPath]:
        paths: list[SemanticMatchedPath] = []
        for path in self.semantic_view.path_semantics.values():
            if any(_contains(text, phrase) for phrase in path.negative_phrases):
                continue
            evidence = _first_evidence(text, path.trigger_phrases)
            if evidence:
                paths.append(
                    SemanticMatchedPath(
                        path_semantic=path.name,
                        relationships=path.relationships,
                        evidence=evidence,
                    )
                )
        if not paths:
            paths.extend(_infer_paths_from_entities(text, entities, self.semantic_view))
        paths = _apply_disambiguation_rules(text, paths, self.semantic_view)
        ranked_paths = _unique_paths(sorted(paths, key=lambda item: (-len(item.relationships), -len(item.evidence), item.path_semantic)))
        if len(ranked_paths) > 1 and {path.evidence for path in ranked_paths} == {"实体组合命中"}:
            return ranked_paths
        return ranked_paths[:1]

    def _match_returns(
        self,
        text: str,
        entities: list[str],
        paths: list[SemanticMatchedPath],
    ) -> list[SemanticMatchedReturn]:
        returns: list[SemanticMatchedReturn] = []
        path_target = _path_target_entity(paths, self.semantic_view)
        if _contains(text, "详细信息") or _contains(text, "节点信息") or _contains(text, "详情"):
            target = path_target or (entities[-1] if entities else None)
            if target is not None:
                evidence = "详情" if _contains(text, "详情") else "详细信息"
                returns.append(SemanticMatchedReturn(field=f"{target}.*", evidence=evidence))

        ordered_fields = sorted(
            self.semantic_view.fields.values(),
            key=lambda field: (0 if field.owner in entities else 1, field.owner, field.name),
        )
        for field in ordered_fields:
            if field.property == "id" and not _contains(text, "ID") and not _contains(text, "编号"):
                continue
            terms = [field.name, f"{field.owner}.{field.property}", field.name_zh, *field.synonyms]
            owner_terms: list[str] = []
            owner = self.semantic_view.entities.get(field.owner)
            if owner is not None:
                for entity_term in (owner.name, owner.name_zh, owner.label, *owner.synonyms):
                    if field.property == "id":
                        owner_terms.extend(
                            [
                                f"{entity_term}编号",
                                f"{entity_term}ID",
                                f"{entity_term}的ID",
                                f"{entity_term} Id",
                            ]
                        )
                    if field.property == "name":
                        owner_terms.append(f"{entity_term}名称")
                    if field.property == "latency":
                        owner_terms.append(f"{entity_term}时延")
                    if field.property == "elem_type":
                        owner_terms.append(f"{entity_term}类型")
                terms.extend(owner_terms)
                has_generic_owner_context = _has_field_owner_context(text, owner, field.property)
                can_use_generic_field = field.owner in entities and (
                    path_target is None or has_generic_owner_context
                )
                if can_use_generic_field:
                    generic_evidence = _generic_return_evidence(text, owner, field.property)
                    if generic_evidence is not None:
                        returns.append(SemanticMatchedReturn(field=field.name, evidence=generic_evidence))
                        continue
                if (
                    field.property == "elem_type"
                    and field.owner in entities
                    and _contains(text, "类型")
                    and (len(entities) == 1 or has_generic_owner_context)
                ):
                    returns.append(SemanticMatchedReturn(field=field.name, evidence="类型"))
                    continue
            evidence_terms = terms
            if path_target is not None and field.owner not in entities and owner is not None:
                if has_generic_owner_context:
                    evidence_terms = terms
                else:
                    evidence_terms = owner_terms
            if any(_contains(text, term) for term in evidence_terms):
                returns.append(SemanticMatchedReturn(field=field.name, evidence=_field_evidence(text, evidence_terms)))

        if not returns and _contains(text, "所有"):
            target = _path_target_entity(paths, self.semantic_view) or (entities[-1] if entities else None)
            if target is not None:
                returns.extend(
                    [
                        SemanticMatchedReturn(field=f"{target}.id", evidence="默认返回ID"),
                        SemanticMatchedReturn(field=f"{target}.name", evidence="默认返回名称"),
                    ]
                )
        if not returns and paths:
            returns.extend(
                SemanticMatchedReturn(field=field, evidence="return_policies.default_return_fields")
                for field in self.semantic_view.path_semantics[paths[0].path_semantic].default_return_fields
                if field.split(".", 1)[0] == self.semantic_view.path_semantics[paths[0].path_semantic].target_entity
            )
        returns.extend(_relationship_detail_default_returns(text, paths, self.semantic_view))
        returns.extend(_service_tunnel_context_returns(text, paths))
        returns.extend(_both_side_property_returns(text, paths, self.semantic_view))
        return _prune_return_owners(_sort_returns_by_evidence(text, _unique_returns(returns)), entities, paths, self.semantic_view)

    def _match_metrics(self, text: str, entities: list[str]) -> list[SemanticMatchedMetric]:
        metrics: list[SemanticMatchedMetric] = []
        property_count = _match_property_count_metric(text, entities, self.semantic_view)
        if property_count is not None:
            metrics.append(property_count)
        for metric in self.semantic_view.metrics.values():
            terms = [metric.name, metric.name_zh, *metric.synonyms]
            if any(_contains(text, term) for term in terms):
                metrics.append(SemanticMatchedMetric(metric_id=metric.name, evidence=_field_evidence(text, terms)))
        if not metrics and (_contains(text, "数量") or _contains(text, "多少") or _contains(text, "统计")):
            metric_name = f"{_metric_owner(text, entities)}_count"
            if metric_name in self.semantic_view.metrics:
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


def _path_target_entity(paths: list[SemanticMatchedPath], semantic_view: GraphSemanticView) -> str | None:
    if not paths:
        return None
    relationship = semantic_view.relationships.get(paths[0].relationships[-1])
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
    if (
        _contains(text, "网元")
        or _contains(text, "网络元素")
        or _contains(text, "设备")
        or _contains(text, "厂商")
        or _contains(text, "位置")
        or _contains(text, "终点")
    ):
        return "network_element"
    if _contains(text, "端口") or _contains(text, "接口"):
        return "port"
    if _contains(text, "隧道"):
        return "tunnel"
    if _contains(text, "服务") or _contains(text, "业务"):
        return "service"
    return entities[-1] if entities else "service"


def _match_numeric_filters(
    text: str,
    entities: list[str],
    semantic_view: GraphSemanticView,
) -> list[SemanticMatchedFilter]:
    filters: list[SemanticMatchedFilter] = []
    ordered_fields = sorted(
        semantic_view.fields.values(),
        key=lambda field: (0 if field.owner in entities else 1, field.owner, field.name),
    )
    for field in ordered_fields:
        if field.value_type != "number" or "filter" not in field.roles:
            continue
        if entities and field.owner not in entities:
            continue
        owner = semantic_view.entities.get(field.owner)
        terms = [field.name_zh, _property_zh(field.property), *field.synonyms]
        if owner is not None:
            terms.extend(
                [
                    f"{owner.name_zh}{_property_zh(field.property)}",
                    f"{owner.name_zh}的{_property_zh(field.property)}",
                ]
            )
        for term in [item for item in terms if item]:
            match = re.search(rf"{re.escape(term)}(?:为|是|等于|=)(\d+(?:\.\d+)?)", text)
            if not match:
                continue
            raw_value = match.group(1)
            value: int | float = float(raw_value) if "." in raw_value else int(raw_value)
            filters.append(
                SemanticMatchedFilter(
                    field=field.name,
                    operator="=",
                    value=value,
                    evidence=f"{term}为{raw_value}",
                )
            )
            break
    return filters


def _property_zh(property_name: str) -> str:
    return {
        "bandwidth": "带宽",
        "latency": "延迟",
        "quality_of_service": "服务质量",
        "speed": "速率",
        "length": "长度",
        "mtu": "MTU",
        "bandwidth_capacity": "带宽容量",
    }.get(property_name, "")


def _relationship_detail_default_returns(
    text: str,
    paths: list[SemanticMatchedPath],
    semantic_view: GraphSemanticView,
) -> list[SemanticMatchedReturn]:
    if not paths or not (
        _contains(text, "对应关系") or _contains(text, "双方") or _contains(text, "其使用的隧道")
    ):
        return []
    path = semantic_view.path_semantics[paths[0].path_semantic]
    return [
        SemanticMatchedReturn(field=field, evidence="path_semantics.default_return_fields")
        for field in path.default_return_fields
    ]


def _service_tunnel_context_returns(text: str, paths: list[SemanticMatchedPath]) -> list[SemanticMatchedReturn]:
    if not paths:
        return []
    if not paths[0].path_semantic.startswith("service.tunnel_"):
        return []
    if not _contains(text, "及其"):
        return []
    if _contains(text, "返回"):
        return []
    if not (
        _contains(text, "服务使用的隧道")
        or _contains(text, "业务使用的隧道")
        or _contains(text, "服务所用隧道")
        or _contains(text, "业务所用隧道")
    ):
        return []
    return [
        SemanticMatchedReturn(field="service.name", evidence="服务使用的隧道"),
        SemanticMatchedReturn(field="tunnel.name", evidence="服务使用的隧道"),
    ]


def _both_side_property_returns(
    text: str,
    paths: list[SemanticMatchedPath],
    semantic_view: GraphSemanticView,
) -> list[SemanticMatchedReturn]:
    if not paths or paths[0].path_semantic != "service.uses_tunnel" or not _contains(text, "双方"):
        return []
    returns: list[SemanticMatchedReturn] = []
    if _contains(text, "名称"):
        returns.extend(
            [
                SemanticMatchedReturn(field="service.name", evidence="双方名称"),
                SemanticMatchedReturn(field="tunnel.name", evidence="双方名称"),
            ]
        )
    if _contains(text, "延迟") or _contains(text, "时延"):
        for field in ("service.latency", "tunnel.latency"):
            if field in semantic_view.fields:
                returns.append(SemanticMatchedReturn(field=field, evidence="双方延迟"))
    return returns


def _has_field_owner_context(text: str, owner: SemanticEntity, property_name: str) -> bool:
    word = {
        "id": "编号",
        "name": "名称",
        "bandwidth": "带宽",
        "latency": "时延",
        "quality_of_service": "服务质量",
        "ip_address": "IP",
        "software_version": "版本",
        "vendor": "厂商",
        "status": "状态",
        "elem_type": "类型",
    }.get(property_name)
    if not word:
        return False
    for term in (owner.name, owner.name_zh, owner.label, *owner.synonyms):
        if _contains(text, f"{term}{word}") or _contains(text, f"{term}的{word}"):
            return True
        if property_name == "latency" and _contains(text, f"{term}的名称、时延"):
            return True
        if property_name == "name" and (
            _contains(text, f"{term}的ID和名称")
            or _contains(text, f"{term}的编号和名称")
            or _contains(text, f"{term}ID、名称")
            or _contains(text, f"{term}编号、名称")
        ):
            return True
        if property_name == "status" and (
            _contains(text, f"{term}ID、名称和状态")
            or _contains(text, f"{term}编号、名称和状态")
            or _contains(text, f"{term}名称和状态")
        ):
            return True
    return False


def _generic_return_evidence(text: str, owner: SemanticEntity, property_name: str) -> str | None:
    word = {
        "id": "编号",
        "name": "名称",
        "bandwidth": "带宽",
        "latency": "时延",
        "quality_of_service": "服务质量",
        "ip_address": "IP",
        "software_version": "版本",
        "vendor": "厂商",
        "status": "状态",
        "elem_type": "类型",
    }.get(property_name)
    if property_name == "latency" and not _contains(text, word) and _contains(text, "延迟"):
        word = "延迟"
    if word is None or not _contains(text, word):
        return None
    if property_name in {"bandwidth", "latency"} and _contains(text, f"{word}值"):
        return f"{word}值"
    for term in (owner.name, owner.name_zh, owner.label, *owner.synonyms):
        for candidate in (f"{term}{word}", f"{term}的{word}"):
            if _contains(text, candidate):
                return candidate
    return word


def _match_property_count_metric(
    text: str,
    entities: list[str],
    semantic_view: GraphSemanticView,
) -> SemanticMatchedMetric | None:
    if not (_contains(text, "数量") or _contains(text, "统计")):
        return None
    if not (_contains(text, "属性") or _contains(text, "非空")):
        return None
    ordered_fields = sorted(
        semantic_view.fields.values(),
        key=lambda field: (0 if field.owner in entities else 1, field.owner, field.name),
    )
    for field in ordered_fields:
        if entities and field.owner not in entities:
            continue
        terms = [field.name_zh, _property_zh(field.property), *field.synonyms]
        if any(_contains(text, term) for term in terms if term):
            return SemanticMatchedMetric(
                metric_id=f"count_property:{field.name}",
                evidence=f"{field.name_zh}属性数量",
            )
    return None


def _prune_return_owners(
    returns: list[SemanticMatchedReturn],
    entities: list[str],
    paths: list[SemanticMatchedPath],
    semantic_view: GraphSemanticView,
) -> list[SemanticMatchedReturn]:
    if not returns:
        return []
    path_target = _path_target_entity(paths, semantic_view)
    allowed = set(entities)
    if path_target is not None:
        allowed.add(path_target)
    for path in paths:
        for relationship_name in path.relationships:
            relationship = semantic_view.relationships.get(relationship_name)
            if relationship is not None:
                allowed.add(relationship.from_entity)
                allowed.add(relationship.to_entity)
    if not allowed:
        return returns
    preferred = [item for item in returns if item.field.split(".", 1)[0] in allowed]
    return preferred or returns


def _sort_returns_by_evidence(text: str, returns: list[SemanticMatchedReturn]) -> list[SemanticMatchedReturn]:
    def key(item: SemanticMatchedReturn) -> tuple[int, int, int, str]:
        if item.evidence in {"path_semantics.default_return_fields", "双方名称"}:
            priority = 0
        elif item.evidence == "双方延迟" or item.field.endswith(".latency"):
            priority = 2
        else:
            priority = 1
        index = text.find(item.evidence)
        if index < 0:
            index = 10_000
        owner = item.field.split(".", 1)[0]
        owner_order = {"service": 0, "tunnel": 1, "network_element": 2, "port": 3}.get(owner, 99)
        return (priority, index, owner_order, item.field)

    return sorted(returns, key=key)


def _contains(text: str, term: str | None) -> bool:
    if not term:
        return False
    return str(term).replace(" ", "") in text


def _complete_entities(
    *,
    entities: list[str],
    filters: list[SemanticMatchedFilter],
    paths: list[SemanticMatchedPath],
    returns: list[SemanticMatchedReturn],
    metrics: list[SemanticMatchedMetric],
    order_by: list[dict[str, str]],
    semantic_view: GraphSemanticView,
) -> list[str]:
    completed = [] if paths else list(entities)
    for path in paths:
        for relationship_name in path.relationships:
            relationship = semantic_view.relationships.get(relationship_name)
            if relationship is not None:
                completed.extend([relationship.from_entity, relationship.to_entity])
    for item in filters:
        completed.append(item.field.split(".", 1)[0])
    for item in returns:
        completed.append(item.field.split(".", 1)[0])
    for item in metrics:
        metric = semantic_view.metrics.get(item.metric_id)
        if metric is not None:
            completed.append(metric.target_entity)
        elif item.metric_id.startswith("count_property:"):
            completed.append(item.metric_id.split(":", 1)[1].split(".", 1)[0])
    for item in order_by:
        field = item.get("field", "")
        if "." in field:
            completed.append(field.split(".", 1)[0])
        elif field in semantic_view.metrics:
            completed.append(semantic_view.metrics[field].target_entity)
        elif field.startswith("count_property:"):
            completed.append(field.split(":", 1)[1].split(".", 1)[0])
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


def _first_evidence(text: str, terms: tuple[str, ...]) -> str | None:
    for term in terms:
        if _contains(text, term):
            return term
    return None


def _match_limit(text: str) -> int | None:
    match = re.search(r"(?:前|top|TOP|Top)(\d+)", text)
    if not match:
        match = re.search(r"前(\d+)个", text)
    if not match:
        match = re.search(r"(?:最少|最多|数量最少|数量最多)的?(\d+)个", text)
    if not match:
        match = re.search(r"返回(?:数量)?(?:最少|最多)的?(\d+)个", text)
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
    seen: dict[str, int] = {}
    for value in values:
        existing_index = seen.get(value.field)
        if existing_index is None:
            seen[value.field] = len(result)
            result.append(value)
            continue
        if _return_evidence_priority(value.evidence) < _return_evidence_priority(result[existing_index].evidence):
            result[existing_index] = value
    return result


def _return_evidence_priority(evidence: str) -> int:
    if evidence in {"path_semantics.default_return_fields", "双方名称", "双方延迟"}:
        return 0
    return 1


def _unique_filters(values: list[SemanticMatchedFilter]) -> list[SemanticMatchedFilter]:
    result: list[SemanticMatchedFilter] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        key = (value.field, value.operator, str(value.value))
        if key not in seen:
            seen.add(key)
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


def _unique_paths(values: list[SemanticMatchedPath]) -> list[SemanticMatchedPath]:
    result: list[SemanticMatchedPath] = []
    seen: set[str] = set()
    for value in values:
        if value.path_semantic not in seen:
            seen.add(value.path_semantic)
            result.append(value)
    return result


def _infer_paths_from_entities(
    text: str,
    entities: list[str],
    semantic_view: GraphSemanticView,
) -> list[SemanticMatchedPath]:
    inferred: list[SemanticMatchedPath] = []
    entity_set = set(entities)
    for path in semantic_view.path_semantics.values():
        if path.source_entity in entity_set and path.target_entity in entity_set:
            inferred.append(
                SemanticMatchedPath(
                    path_semantic=path.name,
                    relationships=path.relationships,
                    evidence="实体组合命中",
                )
            )
    if not inferred and "service" in entity_set and _contains(text, "隧道"):
        path = semantic_view.path_semantics.get("service.uses_tunnel")
        if path is not None:
            inferred.append(
                SemanticMatchedPath(
                    path_semantic=path.name,
                    relationships=path.relationships,
                    evidence=path.trigger_phrases[0],
                )
            )
    return inferred


def _apply_disambiguation_rules(
    text: str,
    paths: list[SemanticMatchedPath],
    semantic_view: GraphSemanticView,
) -> list[SemanticMatchedPath]:
    if not paths:
        return []
    by_name = {path.path_semantic: path for path in paths}
    for rule in semantic_view.disambiguation_rules:
        if not any(_contains(text, pattern) for pattern in rule.positive_patterns):
            continue
        if any(_contains(text, pattern) for pattern in rule.negative_patterns):
            continue
        preferred = by_name.get(rule.prefer)
        if preferred is not None:
            return [preferred]
        preferred_path = semantic_view.path_semantics.get(rule.prefer)
        if preferred_path is not None and not any(_contains(text, phrase) for phrase in preferred_path.negative_phrases):
            return [
                SemanticMatchedPath(
                    path_semantic=preferred_path.name,
                    relationships=preferred_path.relationships,
                    evidence=_first_evidence(text, rule.positive_patterns) or rule.positive_patterns[0],
                )
            ]
    return paths
