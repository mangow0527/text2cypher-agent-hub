from __future__ import annotations

from collections import Counter
import re

from services.cypher_generator_agent.app.dsl.ast import Projection, ProjectionItem


RETURN_BODY_RE = re.compile(
    r"\bRETURN\b(?P<body>.*?)(?:\bORDER\s+BY\b|\bLIMIT\b|\bSKIP\b|$)",
    flags=re.IGNORECASE | re.DOTALL,
)
RETURN_ALIAS_RE = re.compile(r"\bAS\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\s*$", flags=re.IGNORECASE)
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROPERTY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.(?P<property>[A-Za-z_][A-Za-z0-9_]*)$")
PARAMETER_RE = re.compile(r"(?<![A-Za-z0-9_])\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
CYPHER_RESERVED_ALIASES = {
    "all",
    "and",
    "any",
    "as",
    "asc",
    "ascending",
    "by",
    "call",
    "case",
    "commit",
    "contains",
    "count",
    "create",
    "delete",
    "desc",
    "descending",
    "detach",
    "distinct",
    "else",
    "end",
    "exists",
    "extract",
    "false",
    "filter",
    "foreach",
    "in",
    "is",
    "limit",
    "match",
    "merge",
    "none",
    "not",
    "null",
    "optional",
    "or",
    "order",
    "remove",
    "return",
    "set",
    "single",
    "skip",
    "start",
    "then",
    "true",
    "union",
    "unwind",
    "when",
    "where",
    "with",
    "xor",
    "yield",
}


def projection_aliases(projection: Projection) -> list[str]:
    base_aliases = [projection_item_alias(item) for item in projection.items]
    base_counts = Counter(base_aliases)
    aliases: list[str] = []
    used: set[str] = set()
    for item, base_alias in zip(projection.items, base_aliases, strict=True):
        alias = base_alias
        if base_counts[base_alias] > 1:
            owner_prefix = _owner_prefix(item)
            if owner_prefix:
                alias = f"{owner_prefix}_{base_alias}"
        alias = _safe_projection_alias(alias, item)
        alias = _dedupe_alias(alias, used)
        used.add(alias)
        aliases.append(alias)
    return aliases


def projection_item_alias(item: ProjectionItem) -> str:
    if item.alias and is_cypher_identifier(item.alias):
        return item.alias
    if item.property is not None:
        return _sanitize_identifier(item.property.name)
    if item.source is not None:
        return _sanitize_identifier(item.source.name)
    if item.vertex_full and item.target is not None:
        return _sanitize_identifier(item.target.alias)
    raise ValueError("projection item must include alias, property, or source")


def extract_return_aliases(cypher: str) -> list[str]:
    match = RETURN_BODY_RE.search(cypher)
    if match is None:
        return []

    aliases: list[str] = []
    for raw_item in match.group("body").split(","):
        expression = raw_item.strip()
        if not expression:
            continue
        alias_match = RETURN_ALIAS_RE.search(expression)
        if alias_match:
            aliases.append(alias_match.group("alias"))
            continue
        if IDENTIFIER_RE.fullmatch(expression):
            aliases.append(expression)
            continue
        property_match = PROPERTY_RE.fullmatch(expression)
        if property_match:
            aliases.append(property_match.group("property"))
    return aliases


def extract_parameter_names(cypher: str) -> set[str]:
    return {match.group("name") for match in PARAMETER_RE.finditer(cypher)}


def is_cypher_identifier(value: str) -> bool:
    return IDENTIFIER_RE.fullmatch(value) is not None


def _owner_prefix(item: ProjectionItem) -> str | None:
    if item.property is not None:
        return _snake_case_identifier(item.property.owner)
    if item.vertex_full and item.target is not None:
        return _snake_case_identifier(item.target.vertex_name)
    if item.source is not None and item.source.namespace:
        return _snake_case_identifier(item.source.namespace)
    return None


def _dedupe_alias(alias: str, used: set[str]) -> str:
    if alias not in used:
        return alias
    index = 2
    while f"{alias}_{index}" in used:
        index += 1
    return f"{alias}_{index}"


def _safe_projection_alias(alias: str, item: ProjectionItem) -> str:
    alias = _sanitize_identifier(alias)
    if not _is_reserved_alias(alias):
        return alias
    if item.vertex_full and item.target is not None:
        vertex_alias = _snake_case_identifier(item.target.vertex_name)
        if not _is_reserved_alias(vertex_alias):
            return vertex_alias
        return f"{vertex_alias}_value"
    return f"{alias}_value"


def _is_reserved_alias(alias: str) -> bool:
    return alias.lower() in CYPHER_RESERVED_ALIASES


def _sanitize_identifier(value: str) -> str:
    if is_cypher_identifier(value):
        return value
    sanitized = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    if not sanitized:
        return "field"
    if sanitized[0].isdigit():
        sanitized = f"field_{sanitized}"
    return sanitized


def _snake_case_identifier(value: str) -> str:
    pieces = re.sub(r"(?<!^)(?=[A-Z])", "_", value).split("_")
    return _sanitize_identifier("_".join(piece.lower() for piece in pieces if piece))
