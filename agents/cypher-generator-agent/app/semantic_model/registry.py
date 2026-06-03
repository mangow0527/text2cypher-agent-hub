from __future__ import annotations

from typing import Literal

from .model import (
    EdgeDefinition,
    GraphSemanticModel,
    MetricDefinition,
    PathPatternDefinition,
    PropertyDefinition,
    SemanticModelError,
    VertexDefinition,
)


EdgeDirection = Literal["forward", "reverse", "either"]


class RegistryLookupError(SemanticModelError):
    def __init__(self, object_type: str, name: str, owner: str | None = None) -> None:
        self.object_type = object_type
        self.name = name
        self.owner = owner
        qualified_name = f"{owner}.{name}" if owner else name
        super().__init__(f"{object_type} not found: {qualified_name}")


class UnsupportedDirectionError(SemanticModelError):
    def __init__(self, direction: str) -> None:
        self.direction = direction
        super().__init__(f"unsupported edge direction: {direction}")


class GraphSemanticRegistry:
    def __init__(self, model: GraphSemanticModel) -> None:
        self.model = model
        self._vertices = {vertex.name: vertex for vertex in model.vertices}
        self._edges = {edge.name: edge for edge in model.edges}
        self._metrics = {metric.name: metric for metric in model.metrics}
        self._path_patterns = {path_pattern.name: path_pattern for path_pattern in model.path_patterns}
        self._properties = self._build_properties(model)

    def get_vertex(self, name: str) -> VertexDefinition:
        try:
            return self._vertices[name]
        except KeyError as exc:
            raise RegistryLookupError("vertex", name) from exc

    def get_edge(self, name: str) -> EdgeDefinition:
        try:
            return self._edges[name]
        except KeyError as exc:
            raise RegistryLookupError("edge", name) from exc

    def get_property(self, owner: str, name: str) -> PropertyDefinition:
        try:
            return self._properties[owner][name]
        except KeyError as exc:
            raise RegistryLookupError("property", name, owner) from exc

    def get_metric(self, name: str) -> MetricDefinition:
        try:
            return self._metrics[name]
        except KeyError as exc:
            raise RegistryLookupError("metric", name) from exc

    def get_path_pattern(self, name: str) -> PathPatternDefinition:
        try:
            return self._path_patterns[name]
        except KeyError as exc:
            raise RegistryLookupError("path_pattern", name) from exc

    def edge_connects(
        self,
        edge_name: str,
        from_vertex: str,
        to_vertex: str,
        direction: EdgeDirection = "forward",
    ) -> bool:
        edge = self.get_edge(edge_name)
        if direction == "forward":
            return edge.from_vertex == from_vertex and edge.to_vertex == to_vertex
        if direction == "reverse":
            return edge.from_vertex == to_vertex and edge.to_vertex == from_vertex
        if direction == "either":
            return (
                edge.from_vertex == from_vertex
                and edge.to_vertex == to_vertex
                or edge.from_vertex == to_vertex
                and edge.to_vertex == from_vertex
            )
        raise UnsupportedDirectionError(direction)

    def property_type(self, owner: str, property_name: str) -> str:
        return self.get_property(owner, property_name).type

    @staticmethod
    def _build_properties(model: GraphSemanticModel) -> dict[str, dict[str, PropertyDefinition]]:
        properties: dict[str, dict[str, PropertyDefinition]] = {}
        for vertex in model.vertices:
            properties[vertex.name] = {prop.name: prop for prop in vertex.properties}
        for edge in model.edges:
            properties[edge.name] = {prop.name: prop for prop in edge.properties}
        return properties
