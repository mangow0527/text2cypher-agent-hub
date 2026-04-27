from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .models import GenerationFailureReason


@dataclass(frozen=True)
class ParsedModelOutput:
    parsed_cypher: str
    parse_summary: str
    reason: GenerationFailureReason | None = None


def parse_model_output(raw_output: str) -> ParsedModelOutput:
    content = raw_output.strip()
    if not content:
        return ParsedModelOutput(parsed_cypher="", parse_summary="empty_output", reason="empty_output")
    if content.startswith("```") or content.endswith("```"):
        return ParsedModelOutput(parsed_cypher="", parse_summary="wrapped_in_markdown", reason="wrapped_in_markdown")
    if _looks_like_json(content):
        return ParsedModelOutput(parsed_cypher="", parse_summary="wrapped_in_json", reason="wrapped_in_json")
    if _starts_with_supported_clause(content) and _contains_explanation_after_cypher(content):
        return ParsedModelOutput(parsed_cypher="", parse_summary="contains_explanation", reason="contains_explanation")
    if _starts_with_supported_clause(content):
        return ParsedModelOutput(parsed_cypher=content, parse_summary="direct_cypher")
    if _contains_cypher_line(content):
        return ParsedModelOutput(parsed_cypher="", parse_summary="contains_explanation", reason="contains_explanation")
    return ParsedModelOutput(parsed_cypher="", parse_summary="no_cypher_found", reason="no_cypher_found")


def _starts_with_supported_clause(content: str) -> bool:
    return re.match(r"^(MATCH|WITH|CALL)\b", content.lstrip(), re.IGNORECASE) is not None


def _looks_like_json(content: str) -> bool:
    if not ((content.startswith("{") and content.endswith("}")) or (content.startswith("[") and content.endswith("]"))):
        return False
    try:
        json.loads(content)
    except json.JSONDecodeError:
        return False
    return True


def _contains_cypher_line(content: str) -> bool:
    for line in content.splitlines():
        if _starts_with_supported_clause(line.strip()):
            return True
    return False


def _contains_explanation_after_cypher(content: str) -> bool:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) <= 1:
        return False
    allowed_continuations = (
        "MATCH",
        "OPTIONAL MATCH",
        "WITH",
        "WHERE",
        "RETURN",
        "ORDER BY",
        "SKIP",
        "LIMIT",
        "UNWIND",
    )
    return any(not line.upper().startswith(allowed_continuations) for line in lines[1:])
