from __future__ import annotations

import re

from .models import PreflightCheck


_WRITE_OPERATION_PATTERN = re.compile(
    r"(^|\s)(CREATE|MERGE|SET|DELETE|DETACH\s+DELETE|REMOVE|DROP|LOAD\s+CSV)\b",
    re.IGNORECASE,
)
_CALL_PATTERN = re.compile(r"(^|\s)CALL\b", re.IGNORECASE)
_READ_START_PATTERN = re.compile(r"^(MATCH|WITH)\b", re.IGNORECASE)
_CALL_START_PATTERN = re.compile(r"^\s*CALL\s+([A-Za-z0-9_.]+)\b", re.IGNORECASE)


def run_preflight_check(cypher: str, *, readonly_call_whitelist: set[str] | None = None) -> PreflightCheck:
    query = cypher.strip()
    normalized_call_whitelist = {item.strip().lower() for item in (readonly_call_whitelist or set()) if item.strip()}
    if not query:
        return PreflightCheck(accepted=False, reason="empty_output")
    if _has_multiple_statements(query):
        return PreflightCheck(accepted=False, reason="multiple_statements")
    if _has_unclosed_string(query):
        return PreflightCheck(accepted=False, reason="unclosed_string")
    if _has_unbalanced_brackets(query):
        return PreflightCheck(accepted=False, reason="unbalanced_brackets")
    if _WRITE_OPERATION_PATTERN.search(_mask_non_code_segments(query)):
        return PreflightCheck(accepted=False, reason="write_operation")

    masked_query = _mask_non_code_segments(query)
    if _query_starts_with_whitelisted_call(masked_query, normalized_call_whitelist):
        return PreflightCheck(accepted=True)
    if _CALL_PATTERN.search(masked_query):
        return PreflightCheck(accepted=False, reason="unsupported_call")
    if not _READ_START_PATTERN.match(masked_query.lstrip()):
        return PreflightCheck(accepted=False, reason="unsupported_start_clause")
    return PreflightCheck(accepted=True)


def _has_multiple_statements(query: str) -> bool:
    parts = [part.strip() for part in _split_semicolon_outside_strings(query)]
    return len([part for part in parts if part]) > 1


def _split_semicolon_outside_strings(query: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    comment: str | None = None
    escaped = False
    index = 0
    while index < len(query):
        char = query[index]
        next_char = query[index + 1] if index + 1 < len(query) else None
        if quote:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if comment == "line":
            current.append(char)
            if char == "\n":
                comment = None
            index += 1
            continue
        if comment == "block":
            current.append(char)
            if char == "*" and next_char == "/":
                current.append("/")
                comment = None
                index += 2
                continue
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
        elif char == "/" and next_char == "/":
            current.append(char)
            current.append("/")
            comment = "line"
            index += 2
            continue
        elif char == "/" and next_char == "*":
            current.append(char)
            current.append("*")
            comment = "block"
            index += 2
            continue
        elif char == ";":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    parts.append("".join(current))
    return parts


def _has_unclosed_string(query: str) -> bool:
    quote: str | None = None
    escaped = False
    for char in query:
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
    return quote is not None


def _has_unbalanced_brackets(query: str) -> bool:
    pairs = {")": "(", "]": "[", "}": "{"}
    openers = set(pairs.values())
    stack: list[str] = []
    quote: str | None = None
    escaped = False
    for char in query:
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in openers:
            stack.append(char)
        elif char in pairs:
            if not stack or stack.pop() != pairs[char]:
                return True
    return bool(stack)


def _mask_non_code_segments(query: str) -> str:
    masked: list[str] = []
    quote: str | None = None
    comment: str | None = None
    escaped = False
    index = 0
    while index < len(query):
        char = query[index]
        next_char = query[index + 1] if index + 1 < len(query) else None
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
                masked.append(char)
                index += 1
                continue
            masked.append(" ")
            index += 1
            continue
        if comment == "line":
            masked.append("\n" if char == "\n" else " ")
            if char == "\n":
                comment = None
            index += 1
            continue
        if comment == "block":
            if char == "*" and next_char == "/":
                masked.append(" ")
                masked.append(" ")
                comment = None
                index += 2
                continue
            masked.append(" ")
            index += 1
            continue
        if char == "/" and next_char == "/":
            masked.append(" ")
            masked.append(" ")
            comment = "line"
            index += 2
            continue
        if char == "/" and next_char == "*":
            masked.append(" ")
            masked.append(" ")
            comment = "block"
            index += 2
            continue
        elif char in {"'", '"'}:
            quote = char
            masked.append(char)
        else:
            masked.append(char)
        index += 1
    return "".join(masked)


def _query_starts_with_whitelisted_call(masked_query: str, readonly_call_whitelist: set[str]) -> bool:
    if not readonly_call_whitelist:
        return False
    match = _CALL_START_PATTERN.match(masked_query)
    if match is None:
        return False
    procedure_name = match.group(1).lower()
    return procedure_name in readonly_call_whitelist
