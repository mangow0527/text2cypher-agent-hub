from __future__ import annotations

import re
from typing import Iterable, Sequence

from pydantic import BaseModel, Field

from .model import (
    EdgeDefinition,
    GraphSemanticModel,
    MetricDefinition,
    PropertyDefinition,
    SemanticModelError,
    VertexDefinition,
)


ALLOWED_CARDINALITIES = {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}
ALLOWED_PROPERTY_TYPES = {"string", "int", "float", "boolean", "datetime"}
VERTEX_REFERENCE_RE = re.compile(r"\((?:(?P<alias>\w+)\s*)?:(?P<label>[A-Z][A-Za-z0-9]*)\)")
RELATIONSHIP_TYPES_RE = re.compile(r"\[[^\]]*:(?P<types>[A-Z][A-Z0-9_]*(?:\s*\|\s*[A-Z][A-Z0-9_]*)*)[^\]]*\]")
AGGREGATE_ALIAS_RE = re.compile(r"\b(?:count|sum|avg|min|max)\(\s*(?:DISTINCT\s+)?(?P<alias>\w+)\s*\)")


class GraphModelValidationIssue(BaseModel):
    code: str
    message: str
    location: str


class GraphModelValidationResult(BaseModel):
    is_valid: bool
    errors: list[GraphModelValidationIssue] = Field(default_factory=list)
    warnings: list[GraphModelValidationIssue] = Field(default_factory=list)


class GraphModelValidationError(SemanticModelError):
    def __init__(self, validation_result: GraphModelValidationResult) -> None:
        self.validation_result = validation_result
        messages = "; ".join(issue.message for issue in validation_result.errors)
        super().__init__(messages or "graph semantic model validation failed")


def validate_graph_model(model: GraphSemanticModel) -> GraphModelValidationResult:
    errors: list[GraphModelValidationIssue] = []
    vertex_names = [vertex.name for vertex in model.vertices]
    edge_names = [edge.name for edge in model.edges]

    _validate_unique("duplicate_vertex", "vertices[].name", vertex_names, errors)
    _validate_unique("duplicate_edge", "edges[].name", edge_names, errors)
    _validate_unique(
        "duplicate_path_pattern",
        "path_patterns[].name",
        [path_pattern.name for path_pattern in model.path_patterns],
        errors,
    )
    _validate_unique(
        "duplicate_metric",
        "metrics[].name",
        [metric.name for metric in model.metrics],
        errors,
    )

    vertices_by_name = {vertex.name: vertex for vertex in model.vertices}
    edges_by_name = {edge.name: edge for edge in model.edges}

    for vertex in model.vertices:
        _validate_owner_properties(vertex.name, vertex.properties, errors)
        property_names = {prop.name for prop in vertex.properties}
        if vertex.id_property not in property_names:
            _add_error(
                errors,
                "missing_id_property",
                f"vertex {vertex.name} id_property {vertex.id_property} is not declared",
                f"vertices.{vertex.name}.id_property",
            )

    for edge in model.edges:
        _validate_owner_properties(edge.name, edge.properties, errors)
        if edge.from_vertex not in vertices_by_name:
            _add_error(
                errors,
                "unknown_edge_endpoint",
                f"unknown edge endpoint {edge.name}.from={edge.from_vertex}",
                f"edges.{edge.name}.from",
            )
        if edge.to_vertex not in vertices_by_name:
            _add_error(
                errors,
                "unknown_edge_endpoint",
                f"unknown edge endpoint {edge.name}.to={edge.to_vertex}",
                f"edges.{edge.name}.to",
            )
        if edge.cardinality not in ALLOWED_CARDINALITIES:
            _add_error(
                errors,
                "invalid_cardinality",
                f"edge {edge.name} cardinality {edge.cardinality} is not allowed",
                f"edges.{edge.name}.cardinality",
            )

    for path_pattern in model.path_patterns:
        _validate_unique(
            "duplicate_path_pattern_parameter",
            f"path_patterns.{path_pattern.name}.parameters[].name",
            [parameter.name for parameter in path_pattern.parameters],
            errors,
        )
        for parameter in path_pattern.parameters:
            _validate_type(
                parameter.type,
                errors,
                f"path_patterns.{path_pattern.name}.parameters.{parameter.name}.type",
                f"path pattern {path_pattern.name} parameter {parameter.name}",
            )

    for metric in model.metrics:
        _validate_metric(metric, vertices_by_name, edges_by_name, errors)

    return GraphModelValidationResult(is_valid=not errors, errors=errors)


def _validate_owner_properties(
    owner_name: str,
    properties: Sequence[PropertyDefinition],
    errors: list[GraphModelValidationIssue],
) -> None:
    _validate_unique(
        "duplicate_property",
        f"{owner_name}.properties[].name",
        [prop.name for prop in properties],
        errors,
    )
    for prop in properties:
        _validate_type(prop.type, errors, f"{owner_name}.{prop.name}.type", f"property {owner_name}.{prop.name}")
        valid_values = set(prop.valid_values)
        unknown_value_synonyms = set(prop.value_synonyms) - valid_values
        if unknown_value_synonyms:
            _add_error(
                errors,
                "invalid_value_synonyms",
                (
                    f"property {owner_name}.{prop.name} value_synonyms keys "
                    f"{sorted(unknown_value_synonyms)} are not in valid_values"
                ),
                f"{owner_name}.{prop.name}.value_synonyms",
            )


def _validate_metric(
    metric: MetricDefinition,
    vertices_by_name: dict[str, VertexDefinition],
    edges_by_name: dict[str, EdgeDefinition],
    errors: list[GraphModelValidationIssue],
) -> None:
    has_full_cypher = bool(metric.full_cypher)
    has_pattern_or_expression = bool(metric.pattern or metric.expression)
    if has_full_cypher and has_pattern_or_expression:
        _add_error(
            errors,
            "metric_query_mode_conflict",
            f"metric {metric.name} cannot define full_cypher together with pattern or expression",
            f"metrics.{metric.name}",
        )
    if not has_full_cypher and not (metric.pattern and metric.expression):
        _add_error(
            errors,
            "metric_query_mode_missing",
            f"metric {metric.name} must define pattern and expression, or full_cypher",
            f"metrics.{metric.name}",
        )

    if not metric.pattern:
        if metric.valid_dimensions:
            _add_error(
                errors,
                "metric_dimensions_without_pattern",
                f"metric {metric.name} valid_dimensions require a pattern",
                f"metrics.{metric.name}.valid_dimensions",
            )
        return

    aliases: dict[str, str] = {}
    seen_aliases: set[str] = set()
    duplicate_aliases: set[str] = set()
    for alias, vertex_name in _extract_vertex_references(metric.pattern):
        if alias is not None:
            if alias in seen_aliases:
                duplicate_aliases.add(alias)
            seen_aliases.add(alias)
            aliases.setdefault(alias, vertex_name)
        if vertex_name not in vertices_by_name:
            _add_error(
                errors,
                "unknown_metric_vertex",
                f"metric {metric.name} pattern uses unknown vertex {vertex_name}",
                f"metrics.{metric.name}.pattern",
            )
    for alias in sorted(duplicate_aliases):
        _add_error(
            errors,
            "duplicate_metric_alias",
            f"metric {metric.name} pattern contains duplicate alias {alias}",
            f"metrics.{metric.name}.pattern",
        )

    unknown_edge_types = _extract_relationship_types(metric.pattern) - set(edges_by_name)
    for edge_type in sorted(unknown_edge_types):
        _add_error(
            errors,
            "unknown_metric_edge",
            f"metric {metric.name} pattern uses unknown edge {edge_type}",
            f"metrics.{metric.name}.pattern",
            )

    for alias in _extract_metric_expression_aliases(metric.expression or ""):
        if alias not in aliases:
            _add_error(
                errors,
                "unknown_metric_expression_alias",
                f"metric {metric.name} expression references unknown alias {alias}",
                f"metrics.{metric.name}.expression",
            )

    for dimension in metric.valid_dimensions:
        if "." not in dimension:
            _add_error(
                errors,
                "invalid_metric_dimension",
                f"metric {metric.name} dimension {dimension} must be alias.property",
                f"metrics.{metric.name}.valid_dimensions",
            )
            continue
        alias, property_name = dimension.split(".", 1)
        if alias not in aliases:
            _add_error(
                errors,
                "unknown_metric_dimension_alias",
                f"metric {metric.name} dimension {dimension} uses unknown alias {alias}",
                f"metrics.{metric.name}.valid_dimensions",
            )
            continue
        vertex_name = aliases[alias]
        vertex = vertices_by_name.get(vertex_name)
        if vertex is None:
            continue
        vertex_properties = {prop.name for prop in vertex.properties}
        if property_name not in vertex_properties:
            _add_error(
                errors,
                "unknown_metric_dimension_property",
                f"metric {metric.name} dimension {dimension} uses unknown property {vertex_name}.{property_name}",
                f"metrics.{metric.name}.valid_dimensions",
            )


def _validate_type(
    property_type: str,
    errors: list[GraphModelValidationIssue],
    location: str,
    label: str,
) -> None:
    if property_type in ALLOWED_PROPERTY_TYPES:
        return
    list_match = re.fullmatch(r"list<([^>]+)>", property_type)
    if list_match and list_match.group(1) in ALLOWED_PROPERTY_TYPES:
        return
    _add_error(
        errors,
        "invalid_property_type",
        f"{label} type {property_type} is not allowed",
        location,
    )


def _validate_unique(
    code: str,
    location: str,
    values: Iterable[str],
    errors: list[GraphModelValidationIssue],
) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    for duplicate in sorted(duplicates):
        _add_error(errors, code, f"{location} contains duplicate {duplicate}", location)


def _extract_vertex_references(pattern: str) -> list[tuple[str | None, str]]:
    return [
        (match.group("alias"), match.group("label"))
        for match in VERTEX_REFERENCE_RE.finditer(pattern)
    ]


def _extract_relationship_types(pattern: str) -> set[str]:
    relationship_types: set[str] = set()
    for match in RELATIONSHIP_TYPES_RE.finditer(pattern):
        relationship_types.update(part.strip() for part in match.group("types").split("|"))
    return relationship_types


def _extract_metric_expression_aliases(expression: str) -> set[str]:
    return {match.group("alias") for match in AGGREGATE_ALIAS_RE.finditer(expression)}


def _add_error(
    errors: list[GraphModelValidationIssue],
    code: str,
    message: str,
    location: str,
) -> None:
    errors.append(GraphModelValidationIssue(code=code, message=message, location=location))
