from __future__ import annotations

import re

from .models import CypherValidationIssue, validation_error
from .parser import ParsedCypher


SHAPE_FAILURE_CODE = "compiler_shape_mismatch"
RETURN_ALIAS_RE = re.compile(r"\bAS\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\s*$", flags=re.IGNORECASE)
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROPERTY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.(?P<property>[A-Za-z_][A-Za-z0-9_]*)$")


def validate_compiler_shape(
    parsed: ParsedCypher,
    expected_return_aliases: list[str],
) -> list[CypherValidationIssue]:
    actual_return_aliases = _extract_final_return_aliases(parsed)
    if actual_return_aliases == expected_return_aliases:
        return []
    return [
        validation_error(
            SHAPE_FAILURE_CODE,
            (
                "RETURN aliases must match DSL projection aliases in order; "
                f"expected {expected_return_aliases}, actual {actual_return_aliases}."
            ),
            "shape",
        )
    ]


def _extract_final_return_aliases(parsed: ParsedCypher) -> list[str]:
    return_clauses = [clause for clause in parsed.clauses if clause.name == "RETURN"]
    if not return_clauses:
        return []
    return _extract_return_aliases(return_clauses[-1].text)


def _extract_return_aliases(return_clause_text: str) -> list[str]:
    body = return_clause_text.removeprefix("RETURN").strip()
    aliases: list[str] = []
    for raw_item in _split_top_level_commas(body):
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


def _split_top_level_commas(text: str) -> list[str]:
    items: list[str] = []
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
            items.append(text[start:index])
            start = index + 1
    items.append(text[start:])
    return items
