from __future__ import annotations

from app.domain.models import CanonicalSchemaSpec, TuGraphConfig
from app.errors import AppError
from app.integrations.tugraph.graph_executor import GraphExecutor


class SchemaCompatibilityService:
    def __init__(self, graph_executor: GraphExecutor | None = None) -> None:
        self.graph_executor = graph_executor or GraphExecutor()

    def validate(self, schema: CanonicalSchemaSpec, config: TuGraphConfig) -> dict:
        labels = self.graph_executor.fetch_labels(config)
        vertex_labels = set(labels.get("vertex") or [])
        edge_labels = set(labels.get("edge") or [])

        missing_nodes = sorted(node for node in schema.node_types if node not in vertex_labels)
        missing_edges = sorted(edge for edge in schema.edge_types if edge not in edge_labels)

        return {
            "ok": not missing_nodes and not missing_edges,
            "planner": labels.get("planner", "unknown"),
            "vertex_labels": sorted(vertex_labels),
            "edge_labels": sorted(edge_labels),
            "missing_nodes": missing_nodes,
            "missing_edges": missing_edges,
        }

    def assert_compatible(self, schema: CanonicalSchemaSpec, config: TuGraphConfig) -> dict:
        result = self.validate(schema, config)
        if result["planner"] == "mock":
            raise AppError("SCHEMA_VALIDATION_ERROR", "TuGraph is not configured; schema compatibility cannot be verified.")
        if result["ok"]:
            return result

        parts = []
        if result["missing_nodes"]:
            parts.append(f"missing node labels: {', '.join(result['missing_nodes'])}")
        if result["missing_edges"]:
            parts.append(f"missing edge labels: {', '.join(result['missing_edges'])}")
        raise AppError(
            "SCHEMA_VALIDATION_ERROR",
            f"Input schema does not match current TuGraph graph; {'; '.join(parts)}.",
        )
