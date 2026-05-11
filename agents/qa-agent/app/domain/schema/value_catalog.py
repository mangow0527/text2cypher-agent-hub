from __future__ import annotations

from typing import Any

from app.domain.models import CanonicalSchemaSpec, TuGraphConfig
from app.integrations.tugraph.graph_executor import GraphExecutor


class ValueCatalogProfiler:
    def __init__(self, graph_executor: GraphExecutor | None = None, max_values_per_property: int = 5) -> None:
        self.graph_executor = graph_executor or GraphExecutor()
        self.max_values_per_property = max(1, max_values_per_property)

    def enrich(self, schema: CanonicalSchemaSpec, config: TuGraphConfig) -> CanonicalSchemaSpec:
        if not config.base_url:
            return schema

        enriched = schema.model_copy(deep=True)
        catalog = dict(enriched.value_catalog)
        for label in enriched.node_types:
            properties = enriched.node_properties.get(label, {})
            if not isinstance(properties, dict):
                continue
            for property_name in properties:
                key = f"{label}.{property_name}"
                if catalog.get(key):
                    continue
                values = self._sample_property_values(label, property_name, config)
                if values:
                    catalog[key] = values
        enriched.value_catalog = catalog
        return enriched

    def _sample_property_values(self, label: str, property_name: str, config: TuGraphConfig) -> list[Any]:
        cypher = (
            f"MATCH (n:{label}) "
            f"WHERE n.{property_name} IS NOT NULL "
            f"RETURN DISTINCT n.{property_name} AS value "
            f"LIMIT {self.max_values_per_property}"
        )
        _meta, signature, ok = self.graph_executor.execute(cypher, config)
        if not ok:
            return []

        values: list[Any] = []
        for row in signature.result_rows or signature.result_preview:
            value = row.get("value") if isinstance(row, dict) else None
            if value is None or value in values:
                continue
            values.append(value)
        return values
