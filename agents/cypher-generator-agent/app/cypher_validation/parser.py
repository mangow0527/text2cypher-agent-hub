from __future__ import annotations

from dataclasses import dataclass
import re

from .models import CypherValidationIssue, validation_error


ALLOWED_READ_CLAUSES = {"MATCH", "WHERE", "WITH", "RETURN", "ORDER BY", "LIMIT", "SKIP", "UNWIND"}
UNSUPPORTED_DIALECT_CLAUSES = {"OPTIONAL MATCH", "DROP DATABASE"}
READONLY_DIALECT_CLAUSES = {"OPTIONAL MATCH"}
MUTATING_CLAUSES = {
    "CREATE",
    "MERGE",
    "SET",
    "DELETE",
    "DETACH DELETE",
    "REMOVE",
    "CALL",
    "LOAD CSV",
    "FOREACH",
    "CREATE INDEX",
    "DROP INDEX",
    "CREATE CONSTRAINT",
    "DROP CONSTRAINT",
}
KNOWN_CLAUSES = ALLOWED_READ_CLAUSES | UNSUPPORTED_DIALECT_CLAUSES | MUTATING_CLAUSES
CLAUSE_PHRASES = tuple(sorted(KNOWN_CLAUSES, key=lambda value: (-len(value.split()), -len(value))))
SYNTAX_FAILURE_CODE = "cypher_syntax_invalid"


@dataclass(frozen=True)
class Clause:
    name: str
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class ParsedCypher:
    cypher: str
    clauses: list[Clause]


def parse_cypher(cypher: str) -> tuple[ParsedCypher | None, list[CypherValidationIssue]]:
    statement = cypher.strip()
    if not statement:
        return None, [_syntax_error("cypher must not be empty")]

    if _contains_unquoted_semicolon(cypher):
        return None, [_syntax_error("cypher must contain exactly one statement without semicolon chaining")]

    starts = _find_clause_starts(cypher)
    if not starts:
        return None, [_syntax_error("cypher must start with a supported clause")]

    leading_text = cypher[: starts[0][0]].strip()
    if leading_text:
        return None, [_syntax_error("cypher must start with a supported clause", f"char:{0}")]

    clauses: list[Clause] = []
    for index, (start, name) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(cypher)
        text = cypher[start:end].strip()
        clauses.append(Clause(name=name, text=text, start=start, end=end))

    statement_boundary_error = _statement_boundary_error(clauses)
    if statement_boundary_error is not None:
        return None, [_syntax_error(statement_boundary_error)]

    return ParsedCypher(cypher=cypher, clauses=clauses), []


def _syntax_error(message: str, location: str = "$") -> CypherValidationIssue:
    return validation_error(SYNTAX_FAILURE_CODE, message, "syntax", location)


def _contains_unquoted_semicolon(text: str) -> bool:
    for index, char in enumerate(text):
        if char == ";" and not _inside_string(text, index):
            return True
    return False


def _find_clause_starts(cypher: str) -> list[tuple[int, str]]:
    starts: list[tuple[int, str]] = []
    upper = cypher.upper()
    index = 0
    while index < len(cypher):
        if _inside_string(cypher, index) or not _is_word_boundary(cypher, index, left=True):
            index += 1
            continue
        matched = _match_clause_at(upper, cypher, index)
        if matched is None:
            index += 1
            continue
        starts.append((index, matched))
        index += len(matched)
    return starts


def _match_clause_at(upper: str, original: str, index: int) -> str | None:
    for phrase in CLAUSE_PHRASES:
        pattern = r"\s+".join(re.escape(part) for part in phrase.split())
        match = re.match(pattern, upper[index:])
        if match is None:
            continue
        end = index + match.end()
        if end <= len(original) and _is_word_boundary(original, end, left=False):
            return phrase
    return None


def _statement_boundary_error(clauses: list[Clause]) -> str | None:
    saw_return = False
    for index, clause in enumerate(clauses):
        if clause.name == "RETURN":
            saw_return = True
            continue
        if saw_return and clause.name in {"MATCH", "WITH", "UNWIND", "OPTIONAL MATCH"}:
            previous_text = clauses[index - 1].text.upper()
            if _contains_union_fragment(previous_text):
                continue
            return "cypher must contain exactly one statement without implicit clause chaining after RETURN"
    return None


def _contains_union_fragment(text: str) -> bool:
    index = 0
    while index < len(text):
        if _inside_string(text, index) or not _is_word_boundary(text, index, left=True):
            index += 1
            continue
        match = re.match(r"UNION(?:\s+ALL)?", text[index:])
        if match is not None:
            end = index + match.end()
            if end <= len(text) and _is_word_boundary(text, end, left=False):
                return True
        index += 1
    return False


def _is_word_boundary(text: str, index: int, *, left: bool) -> bool:
    if left:
        return index == 0 or not _is_identifier_char(text[index - 1])
    return index >= len(text) or not _is_identifier_char(text[index])


def _is_identifier_char(char: str) -> bool:
    return char.isalnum() or char == "_"


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
