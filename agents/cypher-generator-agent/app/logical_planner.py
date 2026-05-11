from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from .intent_recognition import IntentRecognitionResult
from .semantic_layer import SemanticLayer
from .semantic_query import (
    SemanticEntityRef,
    SemanticFieldRef,
    SemanticFilterRef,
    SemanticMetricRef,
    SemanticOrderBy,
    SemanticQueryKind,
    SemanticQuerySpec,
    SemanticRelationshipRef,
    SemanticWithStage,
)
from .semantic_view_matching import SemanticMatchResult


@dataclass(frozen=True)
class LogicalQueryPlan:
    version: int
    plan_id: str
    answer_shape: str
    operators: tuple[dict[str, object], ...]
    schema_path_ref: str | None
    renderer_hints: dict[str, object]
    trace_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "plan_id": self.plan_id,
            "answer_shape": self.answer_shape,
            "operators": [dict(item) for item in self.operators],
            "schema_path_ref": self.schema_path_ref,
            "renderer_hints": dict(self.renderer_hints),
            "trace_refs": list(self.trace_refs),
        }


@dataclass(frozen=True)
class SchemaPathPlan:
    path_id: str
    selected_paths: tuple[dict[str, object], ...]
    required_labels: dict[str, list[str]]
    required_edges: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "path_id": self.path_id,
            "selected_paths": [dict(item) for item in self.selected_paths],
            "required_labels": {key: list(value) for key, value in self.required_labels.items()},
            "required_edges": list(self.required_edges),
        }


@dataclass(frozen=True)
class PlanningResult:
    logical_plan: LogicalQueryPlan
    schema_path_plan: SchemaPathPlan
    semantic_query: SemanticQuerySpec


class LogicalQueryPlanner:
    def __init__(self, semantic_layer: SemanticLayer) -> None:
        self.semantic_layer = semantic_layer

    def plan(
        self,
        *,
        question: str,
        intent_result: IntentRecognitionResult,
        semantic_match: SemanticMatchResult,
        generation_run_id: str | None = None,
    ) -> PlanningResult:
        entities = tuple(self._entity_ref(entity_name) for entity_name in semantic_match.entities)
        relationships = tuple(self._relationship_ref(name) for path in semantic_match.paths for name in path.relationships)
        filters = tuple(self._filter_ref(item) for item in semantic_match.filters)
        projections = tuple(self._return_ref(item.field) for item in semantic_match.returns)
        metrics = tuple(self._metric_ref(item.metric_id) for item in semantic_match.metrics)
        kind = _semantic_query_kind(intent_result, metrics, projections)
        if kind == "ranking":
            projections = _ensure_ranking_name_projection(projections, semantic_match, self.semantic_layer)
        order_by = tuple(self._order_ref(item, projections, metrics) for item in semantic_match.order_by)
        if kind == "dimension_breakdown" and metrics and not order_by:
            order_by = (SemanticOrderBy(expression=metrics[0].output_alias, direction="DESC"),)
        with_stage = None
        if _requires_two_stage_aggregate(question, metrics, relationships):
            first_metric = metrics[0]
            with_stage = SemanticWithStage(
                carry_aliases=(entities[0].alias,) if entities else (),
                metric=replace(first_metric, output_alias="first_total"),
                output_alias="first_total",
            )
            metrics = (replace(first_metric, name="total_count", output_alias="total_count"),)
            if order_by and _contains(question, "首次统计值"):
                order_by = tuple(
                    SemanticOrderBy(
                        expression="first_total" if item.expression == first_metric.output_alias else item.expression,
                        direction=item.direction,
                    )
                    for item in order_by
                )
        output_alias = "exists" if kind == "existence_check" else None
        semantic_query = SemanticQuerySpec(
            kind=kind,
            intent=_intent_name(intent_result),
            entities=entities,
            relationships=relationships,
            projections=projections if kind not in {"metric_aggregation", "dimension_breakdown"} else (),
            dimensions=projections if kind == "dimension_breakdown" else (),
            metrics=metrics,
            filters=filters,
            order_by=order_by,
            with_stage=with_stage,
            limit=semantic_match.limit,
            output_alias=output_alias,
        )
        schema_path_plan = self._schema_path_plan(relationships, entities)
        plan_id = f"logical_plan_{generation_run_id or 'runtime'}"
        operators = self._operators(
            semantic_query=semantic_query,
            semantic_match=semantic_match,
            schema_path_ref=schema_path_plan.path_id if schema_path_plan.selected_paths else None,
        )
        logical_plan = LogicalQueryPlan(
            version=1,
            plan_id=plan_id,
            answer_shape=_answer_shape(intent_result, semantic_query),
            operators=tuple(operators),
            schema_path_ref=schema_path_plan.path_id if schema_path_plan.selected_paths else None,
            renderer_hints={
                "renderer_family": semantic_query.kind,
                "aggregation_shape": "two_stage" if with_stage is not None else "single_stage",
                "requires_path_variable": False,
            },
            trace_refs=tuple(
                [
                    f"intent:{_intent_name(intent_result)}",
                    *[f"semantic_match:path_semantic={path.path_semantic}" for path in semantic_match.paths],
                    *[f"semantic_match:metric={metric.metric_id}" for metric in semantic_match.metrics],
                    *([f"schema_path:{schema_path_plan.path_id}"] if schema_path_plan.selected_paths else []),
                ]
            ),
        )
        return PlanningResult(
            logical_plan=logical_plan,
            schema_path_plan=schema_path_plan,
            semantic_query=semantic_query,
        )

    def _entity_ref(self, entity_name: str) -> SemanticEntityRef:
        entity = self.semantic_layer.entities[entity_name]
        return SemanticEntityRef(name=entity.name, label=entity.label, alias=entity.alias)

    def _relationship_ref(self, relationship_name: str) -> SemanticRelationshipRef:
        relationship = self.semantic_layer.relationships[relationship_name]
        return SemanticRelationshipRef(
            name=relationship.name,
            from_entity=relationship.from_entity,
            to_entity=relationship.to_entity,
            edge=relationship.edge,
            direction=relationship.direction,
        )

    def _filter_ref(self, filter_item: Any) -> SemanticFilterRef:
        owner, property_name = filter_item.field.split(".", 1)
        entity = self.semantic_layer.entities[owner]
        return SemanticFilterRef(
            entity=owner,
            alias=entity.alias,
            property=property_name,
            operator=filter_item.operator,
            value=filter_item.value,
        )

    def _return_ref(self, field_id: str) -> SemanticFieldRef:
        owner, property_name = field_id.split(".", 1)
        entity = self.semantic_layer.entities[owner]
        field_name = entity.alias if property_name == "*" else _field_name(self.semantic_layer, owner, property_name)
        return SemanticFieldRef(
            name=field_name,
            entity=owner,
            alias=entity.alias,
            property=property_name,
            output_alias=field_name,
        )

    def _metric_ref(self, metric_id: str) -> SemanticMetricRef:
        metric = self.semantic_layer.metrics.get(metric_id)
        if metric is None and metric_id.endswith("_count"):
            owner = metric_id[: -len("_count")]
            entity = self.semantic_layer.entities[owner]
            return SemanticMetricRef(
                name=metric_id,
                entity=owner,
                alias=entity.alias,
                aggregation="count",
                expression=f"count({entity.alias})",
                output_alias=metric_id,
                property=None,
            )
        if metric is None:
            raise KeyError(metric_id)
        entity = self.semantic_layer.entities[metric.owner]
        return SemanticMetricRef(
            name=metric.name,
            entity=metric.owner,
            alias=entity.alias,
            aggregation=metric.aggregation,
            expression=metric.expression,
            output_alias=metric.name,
            property=metric.property,
        )

    def _order_ref(
        self,
        item: dict[str, str],
        projections: tuple[SemanticFieldRef, ...],
        metrics: tuple[SemanticMetricRef, ...],
    ) -> SemanticOrderBy:
        field = item.get("field") or ""
        direction = "ASC" if item.get("direction", "desc").lower() == "asc" else "DESC"
        for metric in metrics:
            if field == metric.name:
                return SemanticOrderBy(expression=metric.output_alias, direction=direction)
        for projection in projections:
            if field == f"{projection.entity}.{projection.property}":
                return SemanticOrderBy(expression=projection.expression, direction=direction)
        return SemanticOrderBy(expression=field, direction=direction)

    def _schema_path_plan(
        self,
        relationships: tuple[SemanticRelationshipRef, ...],
        entities: tuple[SemanticEntityRef, ...],
    ) -> SchemaPathPlan:
        required_labels = {entity.name: [entity.label] for entity in entities}
        selected_paths: list[dict[str, object]] = []
        for index, relationship in enumerate(relationships, start=1):
            from_entity = _entity(entities, relationship.from_entity)
            to_entity = _entity(entities, relationship.to_entity)
            selected_paths.append(
                {
                    "path_id": f"schema_path_{index:03d}",
                    "relationships": [relationship.name],
                    "cypher_pattern": (
                        f"({from_entity.alias}:{from_entity.label})-[:{relationship.edge}]->"
                        f"({to_entity.alias}:{to_entity.label})"
                    ),
                }
            )
        return SchemaPathPlan(
            path_id="schema_path_001",
            selected_paths=tuple(selected_paths),
            required_labels=required_labels,
            required_edges=[relationship.edge for relationship in relationships],
        )

    def _operators(
        self,
        *,
        semantic_query: SemanticQuerySpec,
        semantic_match: SemanticMatchResult,
        schema_path_ref: str | None,
    ) -> list[dict[str, object]]:
        operators: list[dict[str, object]] = []
        if semantic_query.entities:
            first = semantic_query.entities[0]
            operators.append({"op": "scan", "entity": first.name, "as": first.alias})
        for path in semantic_match.paths:
            last_relationship = self.semantic_layer.relationships[path.relationships[-1]]
            operators.append(
                {
                    "op": "traverse",
                    "path_semantic": path.path_semantic,
                    "from": last_relationship.from_entity,
                    "to": last_relationship.to_entity,
                    "as": schema_path_ref or "schema_path_001",
                }
            )
        for filter_item in semantic_match.filters:
            operators.append(
                {
                    "op": "filter",
                    "field": filter_item.field,
                    "operator": filter_item.operator,
                    "value": filter_item.value,
                }
            )
        for metric in semantic_match.metrics:
            operators.append({"op": "aggregate", "metric_id": metric.metric_id, "alias": metric.metric_id})
        if semantic_query.with_stage is not None:
            operators.append(
                {
                    "op": "with",
                    "carry_aliases": list(semantic_query.with_stage.carry_aliases),
                    "metric": semantic_query.with_stage.metric.expression,
                    "alias": semantic_query.with_stage.output_alias,
                }
            )
            operators.append({"op": "rematch", "schema_path_ref": schema_path_ref or "schema_path_001"})
        if semantic_query.dimensions:
            operators.append({"op": "group_by", "fields": [f"{item.entity}.{item.property}" for item in semantic_query.dimensions]})
        project_items = [
            {
                "kind": "entity" if item.property == "*" else "dimension",
                "field": item.entity if item.property == "*" else f"{item.entity}.{item.property}",
                "alias": item.output_alias,
            }
            for item in [*semantic_query.projections, *semantic_query.dimensions]
        ]
        project_items.extend({"kind": "metric_alias", "field": item.output_alias, "alias": item.output_alias} for item in semantic_query.metrics)
        if semantic_query.kind == "existence_check":
            project_items.append({"kind": "expression", "field": "count(*) > 0", "alias": semantic_query.output_alias or "exists"})
        if project_items:
            operators.append({"op": "project", "items": project_items})
        for order in semantic_query.order_by:
            operators.append({"op": "order", "field": order.expression, "direction": order.direction.lower()})
        if semantic_query.limit is not None:
            operators.append({"op": "limit", "value": semantic_query.limit})
        return operators


def _field_name(semantic_layer: SemanticLayer, owner: str, property_name: str) -> str:
    for field in semantic_layer.properties.values():
        if field.owner == owner and field.property == property_name:
            return field.name
    return f"{owner}_{property_name}"


def _semantic_query_kind(
    intent_result: IntentRecognitionResult,
    metrics: tuple[SemanticMetricRef, ...],
    projections: tuple[SemanticFieldRef, ...],
) -> SemanticQueryKind:
    primary = intent_result.primary_intent
    if primary == "metric_query":
        return "metric_aggregation"
    if primary == "breakdown_query":
        return "dimension_breakdown"
    if primary == "ranking_query":
        return "ranking"
    if primary == "existence_query":
        return "existence_check"
    if metrics and not projections:
        return "metric_aggregation"
    return "record_selection"


def _ensure_ranking_name_projection(
    projections: tuple[SemanticFieldRef, ...],
    semantic_match: SemanticMatchResult,
    semantic_layer: SemanticLayer,
) -> tuple[SemanticFieldRef, ...]:
    if any(item.property == "name" for item in projections):
        return projections
    target_entity = projections[0].entity if projections else (semantic_match.entities[-1] if semantic_match.entities else None)
    if target_entity is None or target_entity not in semantic_layer.entities:
        return projections
    entity = semantic_layer.entities[target_entity]
    name_projection = SemanticFieldRef(
        name=f"{target_entity}_name",
        entity=target_entity,
        alias=entity.alias,
        property="name",
        output_alias=f"{target_entity}_name",
    )
    return (name_projection, *projections)


def _answer_shape(intent_result: IntentRecognitionResult, semantic_query: SemanticQuerySpec) -> str:
    if semantic_query.kind == "metric_aggregation":
        return "scalar_metric"
    if semantic_query.kind == "dimension_breakdown":
        return "breakdown_table"
    if semantic_query.kind == "ranking":
        return "ranking_table"
    if semantic_query.kind == "existence_check":
        return "boolean"
    return "records"


def _requires_two_stage_aggregate(
    question: str,
    metrics: tuple[SemanticMetricRef, ...],
    relationships: tuple[SemanticRelationshipRef, ...],
) -> bool:
    if not metrics or not relationships:
        return False
    return _contains(question, "首次统计值") or _contains(question, "两次统计结果")


def _contains(text: str, term: str) -> bool:
    return term in text.replace(" ", "").replace("\u3000", "")


def _intent_name(intent_result: IntentRecognitionResult) -> str:
    if intent_result.primary_intent and intent_result.secondary_intent:
        return f"{intent_result.primary_intent}.{intent_result.secondary_intent}"
    return str(intent_result.primary_intent or intent_result.secondary_intent or "")


def _entity(entities: tuple[SemanticEntityRef, ...], name: str) -> SemanticEntityRef:
    for entity in entities:
        if entity.name == name:
            return entity
    raise ValueError(f"schema path references missing entity: {name}")
