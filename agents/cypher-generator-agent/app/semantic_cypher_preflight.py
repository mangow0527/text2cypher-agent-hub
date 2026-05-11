from __future__ import annotations

import re

from .models import PreflightCheck
from .preflight import run_preflight_check
from .semantic_query import SemanticQuerySpec


_LABEL_PATTERN = re.compile(r"\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*([A-Za-z_][A-Za-z0-9_]*)")
_EDGE_PATTERN = re.compile(r"\[\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*([A-Za-z_][A-Za-z0-9_]*)")
_PROPERTY_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")
_NODE_PATTERN = re.compile(
    r"\(\s*"
    r"(?P<alias>[A-Za-z_][A-Za-z0-9_]*)?"
    r"\s*(?::\s*(?P<label>[A-Za-z_][A-Za-z0-9_]*))?"
    r"\s*(?P<properties>\{[^}]*\})?"
    r"\s*\)"
)
_MAP_PROPERTY_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:")


def run_semantic_cypher_preflight(cypher: str, *, semantic_query: SemanticQuerySpec) -> PreflightCheck:
    base_result = run_preflight_check(cypher)
    if not base_result.accepted:
        return base_result

    query = cypher.strip()
    if _has_unauthorized_schema_reference(query, semantic_query):
        return PreflightCheck(accepted=False, reason="unauthorized_schema_reference")
    if _has_logical_plan_mismatch(query, semantic_query):
        return PreflightCheck(accepted=False, reason="logical_plan_mismatch")
    return PreflightCheck(accepted=True)


def _has_unauthorized_schema_reference(query: str, semantic_query: SemanticQuerySpec) -> bool:
    allowed_labels = {entity.label for entity in semantic_query.entities}
    allowed_edges = {relationship.edge for relationship in semantic_query.relationships}
    allowed_properties = _allowed_properties(semantic_query)
    allowed_properties_by_label = _allowed_properties_by_label(semantic_query)

    labels = set(_LABEL_PATTERN.findall(query))
    if labels - allowed_labels:
        return True

    edges = set(_EDGE_PATTERN.findall(query))
    if edges - allowed_edges:
        return True

    property_refs = set(_PROPERTY_PATTERN.findall(_before_return_clause(query)))
    if property_refs - allowed_properties:
        return True
    for alias, label, property_names in _node_map_property_refs(query):
        if alias is not None:
            if any((alias, property_name) not in allowed_properties for property_name in property_names):
                return True
            continue
        if label is not None:
            if any(property_name not in allowed_properties_by_label.get(label, set()) for property_name in property_names):
                return True
    return False


def _has_logical_plan_mismatch(query: str, semantic_query: SemanticQuerySpec) -> bool:
    for entity in semantic_query.entities:
        if f":{entity.label}" not in query:
            return True
    for relationship in semantic_query.relationships:
        if f":{relationship.edge}" not in query:
            return True
    for filter_ref in semantic_query.filters:
        if filter_ref.left not in query:
            return True
        if filter_ref.operator not in query:
            return True
        if _literal(filter_ref.value) not in query:
            return True
    for field_ref in (*semantic_query.projections, *semantic_query.dimensions):
        if field_ref.expression not in query:
            return True
        if field_ref.output_alias not in query:
            return True
    for metric_ref in semantic_query.metrics:
        if metric_ref.expression not in query:
            return True
        if metric_ref.output_alias not in query:
            return True
    for order_ref in semantic_query.order_by:
        if "ORDER BY" not in query.upper():
            return True
        if order_ref.expression not in query:
            return True
        if order_ref.direction == "DESC" and "DESC" not in query.upper():
            return True
    if semantic_query.limit is not None and f"LIMIT {semantic_query.limit}" not in query.upper():
        return True
    if semantic_query.output_alias is not None and semantic_query.output_alias not in query:
        return True
    return False


def _allowed_properties(semantic_query: SemanticQuerySpec) -> set[tuple[str, str]]:
    allowed: set[tuple[str, str]] = set()
    for field_ref in (*semantic_query.projections, *semantic_query.dimensions):
        allowed.add((field_ref.alias, field_ref.property))
    for filter_ref in semantic_query.filters:
        allowed.add((filter_ref.alias, filter_ref.property))
    for metric_ref in semantic_query.metrics:
        if metric_ref.property is not None:
            allowed.add((metric_ref.alias, metric_ref.property))
    return allowed


def _allowed_properties_by_label(semantic_query: SemanticQuerySpec) -> dict[str, set[str]]:
    alias_to_label = {entity.alias: entity.label for entity in semantic_query.entities}
    allowed: dict[str, set[str]] = {}
    for alias, property_name in _allowed_properties(semantic_query):
        label = alias_to_label.get(alias)
        if label is not None:
            allowed.setdefault(label, set()).add(property_name)
    return allowed


def _node_map_property_refs(query: str) -> set[tuple[str | None, str | None, tuple[str, ...]]]:
    refs: set[tuple[str | None, str | None, tuple[str, ...]]] = set()
    for match in _NODE_PATTERN.finditer(query):
        properties = match.group("properties")
        if not properties:
            continue
        property_names = tuple(_MAP_PROPERTY_PATTERN.findall(properties))
        if property_names:
            refs.add((match.group("alias"), match.group("label"), property_names))
    return refs


def _before_return_clause(query: str) -> str:
    match = re.search(r"\bRETURN\b", query, flags=re.IGNORECASE)
    if match is None:
        return query
    return query[: match.start()]


def _literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"
