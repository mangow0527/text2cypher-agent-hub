from __future__ import annotations

from collections.abc import Iterable


def normalize_terms(raw_terms: Iterable[str]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw_term in raw_terms:
        term = str(raw_term).strip()
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms
