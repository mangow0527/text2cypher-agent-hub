from __future__ import annotations

import re
from typing import Protocol

from .models import GrammarMetric


class ParserAdapter(Protocol):
    def parse(self, query: str) -> tuple[bool, str | None]: ...


class Antlr4CypherParserAdapter:
    def parse(self, query: str) -> tuple[bool, str | None]:
        content = query.strip()
        if not content:
            return False, "Cypher query is empty."
        if not re.match(r"^(MATCH|WITH|CALL)\b", content, flags=re.IGNORECASE):
            return False, "Cypher must start with MATCH, WITH, or CALL."
        if _has_unbalanced_pairs(content):
            return False, "Cypher contains unbalanced brackets or quotes."
        if ";" in _strip_quoted_text(content):
            return False, "Cypher must contain exactly one statement."
        if not re.search(r"\bRETURN\b", content, flags=re.IGNORECASE):
            return False, "Cypher must include RETURN."
        return True, None


class GrammarExplainer(Protocol):
    async def explain(self, generated_cypher: str, parser_error: str) -> str: ...


class GrammarChecker:
    def __init__(self, parser: ParserAdapter) -> None:
        self.parser = parser

    def check(self, generated_cypher: str) -> tuple[int, str | None]:
        success, parser_error = self.parser.parse(generated_cypher)
        return (1, None) if success else (0, parser_error)


async def build_grammar_metric(
    *,
    generated_cypher: str,
    checker: GrammarChecker,
    explainer: GrammarExplainer,
) -> GrammarMetric:
    score, parser_error = checker.check(generated_cypher)
    if score == 1:
        return GrammarMetric(score=1, parser_error=None, message=None)
    if not parser_error:
        raise RuntimeError("Grammar parser returned failure without parser_error.")
    message = await explainer.explain(generated_cypher, parser_error)
    if not message:
        raise RuntimeError("Grammar explanation returned empty message.")
    return GrammarMetric(score=0, parser_error=parser_error, message=message)


def _has_unbalanced_pairs(content: str) -> bool:
    pairs = {")": "(",
        "]": "[",
        "}": "{",
    }
    stack: list[str] = []
    in_single = False
    in_double = False
    escaped = False

    for char in content:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if in_single or in_double:
            continue
        if char in "([{":
            stack.append(char)
            continue
        if char in pairs:
            if not stack or stack.pop() != pairs[char]:
                return True
    return in_single or in_double or bool(stack)


def _strip_quoted_text(content: str) -> str:
    result: list[str] = []
    in_single = False
    in_double = False
    escaped = False

    for char in content:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if not in_single and not in_double:
            result.append(char)
    return "".join(result)
