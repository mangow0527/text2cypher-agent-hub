from __future__ import annotations

import math
import re
from difflib import SequenceMatcher
from typing import Any

from .models import (
    EvaluationSummary,
    ExecutionAccuracy,
    GLEUSignal,
    GrammarMetric,
    JaroWinklerSimilaritySignal,
    PrimaryMetrics,
    SecondarySignals,
    SemanticCheck,
    StrictCheck,
)

TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|<=|>=|<>|!=|[()\\[\\],.:=*<>+-/]")
ORDER_BY_PATTERN = re.compile(r"\\bORDER\\s+BY\\b", re.IGNORECASE)


def is_order_sensitive(question: str, gold_cypher: str) -> bool:
    return bool(ORDER_BY_PATTERN.search(gold_cypher or ""))


def build_execution_accuracy(
    *,
    grammar_score: int,
    strict_check_status: str,
    semantic_check_status: str,
    strict_check: StrictCheck | None = None,
    semantic_check: SemanticCheck | None = None,
) -> ExecutionAccuracy:
    strict_check = strict_check or StrictCheck(status=strict_check_status, order_sensitive=False)
    semantic_check = semantic_check or SemanticCheck(status=semantic_check_status)

    if grammar_score == 0:
        return ExecutionAccuracy(
            score=0,
            reason="grammar_failed",
            strict_check=strict_check,
            semantic_check=semantic_check,
        )
    if strict_check.status == "pass":
        return ExecutionAccuracy(
            score=1,
            reason="strict_equal",
            strict_check=strict_check,
            semantic_check=semantic_check,
        )
    if semantic_check.status == "pass":
        return ExecutionAccuracy(
            score=1,
            reason="semantic_equivalent",
            strict_check=strict_check,
            semantic_check=semantic_check,
        )
    if strict_check.status == "not_run":
        return ExecutionAccuracy(
            score=0,
            reason="execution_failed",
            strict_check=strict_check,
            semantic_check=semantic_check,
        )
    return ExecutionAccuracy(
        score=0,
        reason="not_equivalent",
        strict_check=strict_check,
        semantic_check=semantic_check,
    )


def build_secondary_signals(*, generated_cypher: str, gold_cypher: str) -> SecondarySignals:
    generated_tokens = TOKEN_PATTERN.findall(generated_cypher or "")
    golden_tokens = TOKEN_PATTERN.findall(gold_cypher or "")
    gleu_score = _simple_gleu(generated_tokens, golden_tokens)
    jw_score = _jaro_winkler(generated_cypher or "", gold_cypher or "")
    return SecondarySignals(
        gleu=GLEUSignal(score=gleu_score, tokenizer="cypher_tokenizer_v1", min_n=1, max_n=4),
        jaro_winkler_similarity=JaroWinklerSimilaritySignal(
            score=jw_score,
            normalization="query_text_lightweight_v1",
            library="internal",
        ),
    )


def build_evaluation_summary(
    *,
    grammar: GrammarMetric,
    execution_accuracy: ExecutionAccuracy,
    secondary_signals: SecondarySignals,
) -> EvaluationSummary:
    verdict = "pass" if grammar.score == 1 and execution_accuracy.score == 1 else "fail"
    return EvaluationSummary(
        verdict=verdict,
        primary_metrics=PrimaryMetrics(
            grammar=grammar,
            execution_accuracy=execution_accuracy,
        ),
        secondary_signals=secondary_signals,
    )


def _simple_gleu(generated_tokens: list[str], golden_tokens: list[str]) -> float:
    if not generated_tokens and not golden_tokens:
        return 1.0
    if not generated_tokens or not golden_tokens:
        return 0.0
    total = 0.0
    count = 0
    for n in range(1, 5):
        generated = _ngrams(generated_tokens, n)
        golden = _ngrams(golden_tokens, n)
        if not generated and not golden:
            total += 1.0
            count += 1
            continue
        if not generated or not golden:
            count += 1
            continue
        overlap = sum((generated & golden).values())
        precision = overlap / max(1, sum(generated.values()))
        recall = overlap / max(1, sum(golden.values()))
        total += min(precision, recall)
        count += 1
    return round(total / max(1, count), 4)


def _ngrams(tokens: list[str], n: int):
    from collections import Counter

    return Counter(tuple(tokens[index : index + n]) for index in range(len(tokens) - n + 1))


def _jaro_winkler(left: str, right: str) -> float:
    if left == right:
        return 1.0
    ratio = SequenceMatcher(a=left, b=right).ratio()
    prefix = 0
    for l_char, r_char in zip(left[:4], right[:4]):
        if l_char != r_char:
            break
        prefix += 1
    score = ratio + 0.1 * prefix * (1 - ratio)
    return round(min(1.0, max(0.0, score)), 4)
