from __future__ import annotations

from typing import Any

from services.cypher_generator_agent.app.semantic_query import (
    SemanticEntityRef,
    SemanticFilterRef,
    SemanticFieldRef,
    SemanticMetricRef,
    SemanticQuerySpec,
    SemanticRelationshipRef,
)


class CypherRenderer:
    """Render validated semantic query specs into deterministic read-only Cypher."""

    def render(self, spec: SemanticQuerySpec) -> str:
        lines = [f"MATCH {', '.join(self._render_match_items(spec))}"]
        if spec.filters:
            lines.append(f"WHERE {' AND '.join(self._render_filter(filter_ref) for filter_ref in spec.filters)}")

        return_items = self._render_return_items(spec)
        lines.append(f"RETURN {', '.join(return_items)}")

        if spec.order_by:
            order_items = [f"{item.expression} {item.direction}" for item in spec.order_by]
            lines.append(f"ORDER BY {', '.join(order_items)}")
        if spec.limit is not None:
            lines.append(f"LIMIT {spec.limit}")
        return "\n".join(lines)

    def _render_match_items(self, spec: SemanticQuerySpec) -> list[str]:
        if spec.relationships:
            return [self._render_relationship(relationship, spec.entities) for relationship in spec.relationships]
        return [self._render_node(entity) for entity in spec.entities]

    def _render_relationship(self, relationship_ref: SemanticRelationshipRef, entities: tuple[SemanticEntityRef, ...]) -> str:
        from_node = self._render_node(_entity(entities, relationship_ref.from_entity))
        relationship = f"[:{relationship_ref.edge}]"
        to_node = self._render_node(_entity(entities, relationship_ref.to_entity))
        if relationship_ref.direction == "out":
            return f"{from_node}-{relationship}->{to_node}"
        if relationship_ref.direction == "in":
            return f"{from_node}<-{relationship}-{to_node}"
        if relationship_ref.direction == "undirected":
            return f"{from_node}-{relationship}-{to_node}"
        raise ValueError(f"Unsupported relationship direction: {relationship_ref.direction}")

    def _render_node(self, entity: SemanticEntityRef) -> str:
        return f"({entity.alias}:{entity.label})"

    def _render_filter(self, filter_ref: SemanticFilterRef) -> str:
        return f"{filter_ref.left} {filter_ref.operator} {self._literal(filter_ref.value)}"

    def _render_return_items(self, spec: SemanticQuerySpec) -> list[str]:
        if spec.kind == "existence_check":
            return [f"count(*) > 0 AS {spec.output_alias or 'exists'}"]
        items: list[str] = []
        items.extend(self._render_field(field_ref) for field_ref in spec.projections)
        items.extend(self._render_field(field_ref) for field_ref in spec.dimensions)
        items.extend(self._render_metric(metric_ref) for metric_ref in spec.metrics)
        if not items:
            raise ValueError("Semantic query spec requires at least one return item.")
        return items

    def _render_field(self, field_ref: SemanticFieldRef) -> str:
        return f"{field_ref.expression} AS {field_ref.output_alias}"

    def _render_metric(self, metric_ref: SemanticMetricRef) -> str:
        return f"{metric_ref.expression} AS {metric_ref.output_alias}"

    def _literal(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return "null"
        if isinstance(value, int | float):
            return str(value)
        escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"


def _entity(entities: tuple[SemanticEntityRef, ...], name: str) -> SemanticEntityRef:
    for entity in entities:
        if entity.name == name:
            return entity
    raise ValueError(f"Semantic query relationship references missing entity: {name}")
