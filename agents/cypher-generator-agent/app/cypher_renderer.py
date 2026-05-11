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
        if spec.with_stage is not None:
            return self._render_with_stage(spec)
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

    def _render_with_stage(self, spec: SemanticQuerySpec) -> str:
        if spec.with_stage is None:
            raise ValueError("with-stage renderer requires with_stage metadata.")
        lines = [f"MATCH {', '.join(self._render_match_items(spec))}"]
        if spec.filters:
            lines.append(f"WHERE {' AND '.join(self._render_filter(filter_ref) for filter_ref in spec.filters)}")
        with_items = [
            *spec.with_stage.carry_aliases,
            f"{spec.with_stage.metric.expression} AS {spec.with_stage.output_alias}",
        ]
        lines.append(f"WITH {', '.join(with_items)}")
        lines.append(
            f"MATCH {', '.join(self._render_match_items(spec, bare_aliases=set(spec.with_stage.carry_aliases)))}"
        )
        return_items = self._render_return_items(spec)
        return_items.insert(len(spec.projections) + len(spec.dimensions), spec.with_stage.output_alias)
        lines.append(f"RETURN {', '.join(return_items)}")
        if spec.order_by:
            order_items = [f"{item.expression} {item.direction}" for item in spec.order_by]
            lines.append(f"ORDER BY {', '.join(order_items)}")
        if spec.limit is not None:
            lines.append(f"LIMIT {spec.limit}")
        return "\n".join(lines)

    def _render_match_items(self, spec: SemanticQuerySpec, *, bare_aliases: set[str] | None = None) -> list[str]:
        if spec.relationships:
            chained = self._render_relationship_chain(spec.relationships, spec.entities, bare_aliases=bare_aliases)
            if chained is not None:
                return [chained]
            return [
                self._render_relationship(relationship, spec.entities, bare_aliases=bare_aliases)
                for relationship in spec.relationships
            ]
        return [self._render_node(entity, bare_aliases=bare_aliases) for entity in spec.entities]

    def _render_relationship(
        self,
        relationship_ref: SemanticRelationshipRef,
        entities: tuple[SemanticEntityRef, ...],
        *,
        bare_aliases: set[str] | None = None,
    ) -> str:
        from_node = self._render_node(_entity(entities, relationship_ref.from_entity), bare_aliases=bare_aliases)
        relationship = f"[:{relationship_ref.edge}]"
        to_node = self._render_node(_entity(entities, relationship_ref.to_entity), bare_aliases=bare_aliases)
        if relationship_ref.direction == "out":
            return f"{from_node}-{relationship}->{to_node}"
        if relationship_ref.direction == "in":
            return f"{from_node}<-{relationship}-{to_node}"
        if relationship_ref.direction == "undirected":
            return f"{from_node}-{relationship}-{to_node}"
        raise ValueError(f"Unsupported relationship direction: {relationship_ref.direction}")

    def _render_relationship_chain(
        self,
        relationships: tuple[SemanticRelationshipRef, ...],
        entities: tuple[SemanticEntityRef, ...],
        *,
        bare_aliases: set[str] | None = None,
    ) -> str | None:
        if not relationships or any(item.direction != "out" for item in relationships):
            return None
        parts: list[str] = []
        current_entity_name = relationships[0].from_entity
        parts.append(self._render_node(_entity(entities, current_entity_name), bare_aliases=bare_aliases))
        for relationship_ref in relationships:
            if relationship_ref.from_entity != current_entity_name:
                return None
            parts.append(f"-[:{relationship_ref.edge}]->")
            parts.append(self._render_node(_entity(entities, relationship_ref.to_entity), bare_aliases=bare_aliases))
            current_entity_name = relationship_ref.to_entity
        return "".join(parts)

    def _render_node(self, entity: SemanticEntityRef, *, bare_aliases: set[str] | None = None) -> str:
        if bare_aliases and entity.alias in bare_aliases:
            return f"({entity.alias})"
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
        if field_ref.property == "*":
            return field_ref.expression
        return f"{field_ref.expression} AS {field_ref.output_alias}"

    def _render_metric(self, metric_ref: SemanticMetricRef) -> str:
        return f"{metric_ref.expression} AS {metric_ref.output_alias}"

    def _literal(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return "null"
        if isinstance(value, (int, float)):
            return str(value)
        escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"


def _entity(entities: tuple[SemanticEntityRef, ...], name: str) -> SemanticEntityRef:
    for entity in entities:
        if entity.name == name:
            return entity
    raise ValueError(f"Semantic query relationship references missing entity: {name}")
