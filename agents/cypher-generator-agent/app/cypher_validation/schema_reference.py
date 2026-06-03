from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from services.cypher_generator_agent.app.semantic_model import (
    GraphSemanticRegistry,
    RegistryLookupError,
)

from .models import CypherValidationIssue, validation_error
from .parser import ParsedCypher


SCHEMA_FAILURE_CODE = "cypher_schema_reference_invalid"
OwnerKind = Literal["vertex", "edge"]

IDENTIFIER_RE = r"[A-Za-z_][A-Za-z0-9_]*"
NODE_PATTERN_RE = re.compile(r"\((?P<body>[^()]*)\)")
REL_PATTERN_RE = re.compile(r"\[(?P<body>[^\[\]]*)\]")
DIRECTED_PATTERN_RE = re.compile(
    r"(?="
    r"(?P<left>\([^()]*\))\s*(?P<left_arrow><-|-)\s*"
    r"(?P<rel>\[[^\[\]]*\])\s*(?P<right_arrow>->|-)\s*(?P<right>\([^()]*\))"
    r")"
)
VAR_PROPERTY_RE = re.compile(rf"(?<!\$)\b(?P<var>{IDENTIFIER_RE})\.(?P<property>{IDENTIFIER_RE})\b")
NUMERIC_AGG_START_RE = re.compile(rf"\b(?P<function>sum|avg)\s*\(", re.IGNORECASE)
WITH_ALIAS_RE = re.compile(rf"(?P<expression>.+?)\s+AS\s+(?P<alias>{IDENTIFIER_RE})\s*$", re.IGNORECASE)
IDENTIFIER_TOKEN_RE = re.compile(rf"\b(?P<identifier>{IDENTIFIER_RE})\b")
PROPERTY_OPERATOR_RE = re.compile(
    rf"\b(?P<var>{IDENTIFIER_RE})\.(?P<property>{IDENTIFIER_RE})\s*"
    rf"(?P<operator>>=|<=|<>|=|>|<|\bIN\b|\bCONTAINS\b)",
    re.IGNORECASE,
)
OPERATOR_PROPERTY_RE = re.compile(
    rf"(?P<operator>>=|<=|<>|=|>|<|\bIN\b|\bCONTAINS\b)\s*"
    rf"(?P<var>{IDENTIFIER_RE})\.(?P<property>{IDENTIFIER_RE})\b",
    re.IGNORECASE,
)
MAP_KEY_RE = re.compile(rf"(?P<key>{IDENTIFIER_RE})\s*:")
NODE_LABEL_RE = re.compile(rf":\s*(?P<label>{IDENTIFIER_RE})")
REL_TYPE_RE = re.compile(rf"[:|]\s*(?P<type>{IDENTIFIER_RE})")
NUMERIC_PROPERTY_TYPES = frozenset({"int", "integer", "float", "double", "number", "decimal"})
RANGE_PROPERTY_TYPES = NUMERIC_PROPERTY_TYPES | frozenset({"date", "datetime", "time", "timestamp"})


@dataclass(frozen=True)
class Binding:
    kind: OwnerKind
    owner: str


@dataclass(frozen=True)
class NodePattern:
    variable: str | None
    label: str | None
    labels: list[str]
    properties: list[str]
    raw: str
    uses_backtick_identifier: bool


@dataclass(frozen=True)
class RelationshipPattern:
    variable: str | None
    edge_type: str | None
    edge_types: list[str]
    properties: list[str]
    raw: str
    uses_backtick_identifier: bool


def validate_schema_references(
    parsed: ParsedCypher,
    registry: GraphSemanticRegistry,
) -> list[CypherValidationIssue]:
    errors: list[CypherValidationIssue] = []
    bindings: dict[str, Binding] = {}

    for match_clause in [clause for clause in parsed.clauses if clause.name == "MATCH"]:
        _validate_match_clause(match_clause.text, registry, bindings, errors)

    _validate_var_properties(parsed.cypher, registry, bindings, errors)
    _validate_numeric_aggregate_properties(parsed, registry, bindings, errors)
    _validate_operator_property_types(parsed.cypher, registry, bindings, errors)
    return errors


def _validate_match_clause(
    text: str,
    registry: GraphSemanticRegistry,
    bindings: dict[str, Binding],
    errors: list[CypherValidationIssue],
) -> None:
    for node_match in NODE_PATTERN_RE.finditer(text):
        node = _parse_node_pattern(node_match.group(0))
        if node.uses_backtick_identifier:
            errors.append(
                validation_error(
                    SCHEMA_FAILURE_CODE,
                    "backtick identifiers are not supported by Cypher self-validation MVP",
                    "schema_reference",
                    node.raw,
                )
            )
            continue
        for label in node.labels:
            _validate_vertex(label, registry, node.raw, errors)
        if len(node.labels) > 1:
            errors.append(
                validation_error(
                    SCHEMA_FAILURE_CODE,
                    "multiple node labels are not supported by Cypher self-validation MVP",
                    "schema_reference",
                    node.raw,
                )
            )
            continue
        if node.label is not None and not _has_reference_error(errors, node.label):
            if node.variable is not None:
                bindings[node.variable] = Binding(kind="vertex", owner=node.label)
            for property_name in node.properties:
                _validate_property(registry, node.label, property_name, node.raw, errors)

    for rel_match in REL_PATTERN_RE.finditer(text):
        relationship = _parse_relationship_pattern(rel_match.group(0))
        if relationship.uses_backtick_identifier:
            errors.append(
                validation_error(
                    SCHEMA_FAILURE_CODE,
                    "backtick identifiers are not supported by Cypher self-validation MVP",
                    "schema_reference",
                    relationship.raw,
                )
            )
            continue
        if len(relationship.edge_types) > 1:
            errors.append(
                validation_error(
                    SCHEMA_FAILURE_CODE,
                    "multiple edge types are not supported by Cypher self-validation MVP",
                    "schema_reference",
                    relationship.raw,
                )
            )
            continue
        for edge_type in relationship.edge_types:
            _validate_edge(edge_type, registry, relationship.raw, errors)
        if relationship.edge_type is not None and not _has_reference_error(errors, relationship.edge_type):
            if relationship.variable is not None:
                bindings[relationship.variable] = Binding(kind="edge", owner=relationship.edge_type)
            for property_name in relationship.properties:
                _validate_property(registry, relationship.edge_type, property_name, relationship.raw, errors)

    for pattern_match in DIRECTED_PATTERN_RE.finditer(text):
        left = _parse_node_pattern(pattern_match.group("left"))
        rel = _parse_relationship_pattern(pattern_match.group("rel"))
        right = _parse_node_pattern(pattern_match.group("right"))
        left_owner = _node_owner(left, bindings)
        right_owner = _node_owner(right, bindings)
        if rel.edge_type is None or left_owner is None or right_owner is None:
            continue
        if _has_reference_error(errors, rel.edge_type) or _has_reference_error(errors, left_owner):
            continue
        if _has_reference_error(errors, right_owner):
            continue

        direction = _pattern_direction(pattern_match.group("left_arrow"), pattern_match.group("right_arrow"))
        if direction is None:
            continue
        if not registry.edge_connects(rel.edge_type, left_owner, right_owner, direction=direction):
            errors.append(
                validation_error(
                    SCHEMA_FAILURE_CODE,
                    (
                        f"edge {rel.edge_type} endpoints do not match "
                        f"{left_owner} {pattern_match.group('left_arrow')}[]"
                        f"{pattern_match.group('right_arrow')} {right_owner}"
                    ),
                    "schema_reference",
                    rel.raw,
                )
            )


def _validate_var_properties(
    cypher: str,
    registry: GraphSemanticRegistry,
    bindings: dict[str, Binding],
    errors: list[CypherValidationIssue],
) -> None:
    for match in VAR_PROPERTY_RE.finditer(cypher):
        if _inside_string(cypher, match.start()):
            continue
        variable = match.group("var")
        property_name = match.group("property")
        binding = bindings.get(variable)
        if binding is None:
            errors.append(
                validation_error(
                    SCHEMA_FAILURE_CODE,
                    f"property reference {variable}.{property_name} cannot be resolved to a MATCH binding",
                    "schema_reference",
                    match.group(0),
                )
            )
            continue
        _validate_property(registry, binding.owner, property_name, match.group(0), errors)


def _validate_numeric_aggregate_properties(
    parsed: ParsedCypher,
    registry: GraphSemanticRegistry,
    bindings: dict[str, Binding],
    errors: list[CypherValidationIssue],
) -> None:
    aliases = _infer_with_alias_types(parsed, registry, bindings)
    for function_name, argument, location in _iter_numeric_aggregate_calls(parsed.cypher):
        for property_match in VAR_PROPERTY_RE.finditer(argument):
            variable = property_match.group("var")
            property_name = property_match.group("property")
            binding = bindings.get(variable)
            if binding is None:
                continue

            try:
                property_type = registry.property_type(binding.owner, property_name)
            except RegistryLookupError:
                continue

            if property_type.lower() in NUMERIC_PROPERTY_TYPES:
                continue

            errors.append(
                validation_error(
                    SCHEMA_FAILURE_CODE,
                    (
                        f"{function_name} requires a numeric property, "
                        f"but {binding.owner}.{property_name} has type {property_type}"
                    ),
                    "schema_reference",
                    location,
                )
            )

        for alias_match in IDENTIFIER_TOKEN_RE.finditer(argument):
            alias = alias_match.group("identifier")
            property_type = aliases.get(alias)
            if property_type is None or property_type.lower() in NUMERIC_PROPERTY_TYPES:
                continue

            errors.append(
                validation_error(
                    SCHEMA_FAILURE_CODE,
                    f"{function_name} requires a numeric expression, but alias {alias} has type {property_type}",
                    "schema_reference",
                    location,
                )
            )


def _validate_operator_property_types(
    cypher: str,
    registry: GraphSemanticRegistry,
    bindings: dict[str, Binding],
    errors: list[CypherValidationIssue],
) -> None:
    for operator_match in _iter_property_operator_matches(cypher):
        variable = operator_match.group("var")
        property_name = operator_match.group("property")
        operator = operator_match.group("operator").upper()
        binding = bindings.get(variable)
        if binding is None:
            continue

        try:
            property_type = registry.property_type(binding.owner, property_name)
        except RegistryLookupError:
            continue

        if operator in {">", ">=", "<", "<="}:
            if property_type.lower() in RANGE_PROPERTY_TYPES:
                continue
            errors.append(
                validation_error(
                    SCHEMA_FAILURE_CODE,
                    (
                        f"range operator {operator} requires a numeric or temporal property, "
                        f"but {binding.owner}.{property_name} has type {property_type}"
                    ),
                    "schema_reference",
                    operator_match.group(0),
                )
            )
            continue

        if operator == "CONTAINS":
            if property_type.lower() == "string":
                continue
            errors.append(
                validation_error(
                    SCHEMA_FAILURE_CODE,
                    (
                        f"CONTAINS requires a string property, "
                        f"but {binding.owner}.{property_name} has type {property_type}"
                    ),
                    "schema_reference",
                    operator_match.group(0),
                )
            )


def _iter_property_operator_matches(cypher: str):
    for regex in (PROPERTY_OPERATOR_RE, OPERATOR_PROPERTY_RE):
        for match in regex.finditer(cypher):
            if _inside_string(cypher, match.start()):
                continue
            yield match


def _iter_numeric_aggregate_calls(cypher: str) -> list[tuple[str, str, str]]:
    calls: list[tuple[str, str, str]] = []
    for match in NUMERIC_AGG_START_RE.finditer(cypher):
        if _inside_string(cypher, match.start()):
            continue
        open_index = cypher.find("(", match.start())
        close_index = _matching_parenthesis(cypher, open_index)
        if close_index is None:
            continue
        function_name = match.group("function").lower()
        argument = cypher[open_index + 1 : close_index]
        calls.append((function_name, argument, cypher[match.start() : close_index + 1]))
    return calls


def _infer_with_alias_types(
    parsed: ParsedCypher,
    registry: GraphSemanticRegistry,
    bindings: dict[str, Binding],
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for clause in parsed.clauses:
        if clause.name != "WITH":
            continue
        for item in _split_top_level_comma(clause.text[len("WITH") :].strip()):
            alias_match = WITH_ALIAS_RE.match(item)
            if alias_match is None:
                continue
            expression = alias_match.group("expression")
            property_match = VAR_PROPERTY_RE.fullmatch(expression.strip())
            if property_match is None:
                continue

            binding = bindings.get(property_match.group("var"))
            if binding is None:
                continue

            try:
                aliases[alias_match.group("alias")] = registry.property_type(
                    binding.owner,
                    property_match.group("property"),
                )
            except RegistryLookupError:
                continue
    return aliases


def _split_top_level_comma(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")" and depth > 0:
            depth -= 1
            continue
        if char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def _matching_parenthesis(text: str, open_index: int) -> int | None:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(open_index, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _inside_string(text: str, index: int) -> bool:
    quote: str | None = None
    escaped = False
    for char in text[:index]:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
    return quote is not None


def _parse_node_pattern(raw: str) -> NodePattern:
    body = raw.strip()[1:-1].strip()
    property_names = _extract_map_keys(body)
    body_without_map = _strip_map_literal(body).strip()
    labels = [match.group("label") for match in NODE_LABEL_RE.finditer(body_without_map)]
    label = labels[0] if len(labels) == 1 else None
    variable = None
    first_label = NODE_LABEL_RE.search(body_without_map)
    before_label = body_without_map[: first_label.start()] if first_label else body_without_map
    variable_match = re.match(rf"\s*(?P<var>{IDENTIFIER_RE})\s*$", before_label)
    if variable_match:
        variable = variable_match.group("var")
    return NodePattern(
        variable=variable,
        label=label,
        labels=labels,
        properties=property_names,
        raw=raw,
        uses_backtick_identifier="`" in body_without_map,
    )


def _parse_relationship_pattern(raw: str) -> RelationshipPattern:
    body = raw.strip()[1:-1].strip()
    property_names = _extract_map_keys(body)
    body_without_map = _strip_map_literal(body).strip()
    edge_types = [match.group("type") for match in REL_TYPE_RE.finditer(body_without_map)]
    edge_type = edge_types[0] if len(edge_types) == 1 else None
    variable = None
    first_type = REL_TYPE_RE.search(body_without_map)
    before_type = body_without_map[: first_type.start()] if first_type else body_without_map
    variable_match = re.match(rf"\s*(?P<var>{IDENTIFIER_RE})\s*$", before_type)
    if variable_match:
        variable = variable_match.group("var")
    return RelationshipPattern(
        variable=variable,
        edge_type=edge_type,
        edge_types=edge_types,
        properties=property_names,
        raw=raw,
        uses_backtick_identifier="`" in body_without_map,
    )


def _extract_map_keys(pattern_body: str) -> list[str]:
    map_match = re.search(r"\{(?P<body>[^{}]*)\}", pattern_body)
    if map_match is None:
        return []
    return [match.group("key") for match in MAP_KEY_RE.finditer(map_match.group("body"))]


def _strip_map_literal(pattern_body: str) -> str:
    return re.sub(r"\{[^{}]*\}", "", pattern_body)


def _validate_vertex(
    label: str,
    registry: GraphSemanticRegistry,
    location: str,
    errors: list[CypherValidationIssue],
) -> None:
    try:
        registry.get_vertex(label)
    except RegistryLookupError:
        errors.append(
            validation_error(
                SCHEMA_FAILURE_CODE,
                f"node label {label} is not declared in the graph semantic model",
                "schema_reference",
                location,
            )
        )


def _validate_edge(
    edge_type: str,
    registry: GraphSemanticRegistry,
    location: str,
    errors: list[CypherValidationIssue],
) -> None:
    try:
        registry.get_edge(edge_type)
    except RegistryLookupError:
        errors.append(
            validation_error(
                SCHEMA_FAILURE_CODE,
                f"edge type {edge_type} is not declared in the graph semantic model",
                "schema_reference",
                location,
            )
        )


def _validate_property(
    registry: GraphSemanticRegistry,
    owner: str,
    property_name: str,
    location: str,
    errors: list[CypherValidationIssue],
) -> None:
    try:
        registry.get_property(owner, property_name)
    except RegistryLookupError:
        errors.append(
            validation_error(
                SCHEMA_FAILURE_CODE,
                f"property {owner}.{property_name} is not declared in the graph semantic model",
                "schema_reference",
                location,
            )
        )


def _pattern_direction(left_arrow: str, right_arrow: str):
    if left_arrow == "-" and right_arrow == "->":
        return "forward"
    if left_arrow == "<-" and right_arrow == "-":
        return "reverse"
    if left_arrow == "-" and right_arrow == "-":
        return "either"
    return None


def _node_owner(node: NodePattern, bindings: dict[str, Binding]) -> str | None:
    if node.label is not None:
        return node.label
    if node.variable is None:
        return None
    binding = bindings.get(node.variable)
    if binding is None or binding.kind != "vertex":
        return None
    return binding.owner


def _has_reference_error(errors: list[CypherValidationIssue], name: str) -> bool:
    return any(error.code == SCHEMA_FAILURE_CODE and name in error.message for error in errors)
