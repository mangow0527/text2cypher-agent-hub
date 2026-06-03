from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ParsedLiteral:
    resolved_value: Any
    normalized_value: Any
    source: str
    confidence: float
    target: Any


_RELATIVE_TIME_RE = re.compile(
    r"^(?:最近|近)\s*(?P<amount>\d+)\s*(?P<unit>天|日|周|星期|月|年)$",
    re.IGNORECASE,
)
_EN_RELATIVE_TIME_RE = re.compile(
    r"^last\s+(?P<amount>\d+)\s+(?P<unit>day|days|week|weeks|month|months|year|years)$",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(
    r"(?P<number>[+-]?\d+(?:,\d{3})*(?:\.\d+)?|[+-]?\d+(?:\.\d+)?)\s*(?P<unit>[kmgt]?)(?:bps|b)?",
    re.IGNORECASE,
)


def parse_typed_literal(
    raw_literal: str,
    property_type: str,
    literal_kind_hint: str | None = None,
    property_description: str | None = None,
) -> ParsedLiteral | None:
    del property_description
    normalized_type = property_type.strip().lower()
    raw = raw_literal.strip()

    if literal_kind_hint == "time" or normalized_type in {"date", "datetime", "timestamp"}:
        parsed_time = _parse_relative_time_range(raw)
        if parsed_time is not None:
            return parsed_time

    if normalized_type in {"int", "integer", "long"}:
        return _parse_integer(raw)

    if normalized_type in {"float", "double", "number", "decimal"}:
        return _parse_float_or_capacity(raw)

    return None


def _parse_relative_time_range(raw_literal: str) -> ParsedLiteral | None:
    match = _RELATIVE_TIME_RE.match(raw_literal)
    unit_map = {
        "天": "day",
        "日": "day",
        "周": "week",
        "星期": "week",
        "月": "month",
        "年": "year",
    }
    if match is None:
        match = _EN_RELATIVE_TIME_RE.match(raw_literal)
        unit_map = {
            "day": "day",
            "days": "day",
            "week": "week",
            "weeks": "week",
            "month": "month",
            "months": "month",
            "year": "year",
            "years": "year",
        }
    if match is None:
        return None

    amount = int(match.group("amount"))
    unit = unit_map[match.group("unit").lower()]
    normalized = {
        "type": "relative_time_range",
        "direction": "last",
        "amount": amount,
        "unit": unit,
    }
    return ParsedLiteral(
        resolved_value=normalized,
        normalized_value=normalized,
        source="typed_parser.relative_time_range",
        confidence=0.99,
        target=normalized,
    )


def _parse_integer(raw_literal: str) -> ParsedLiteral | None:
    match = _NUMBER_RE.search(_strip_numeric_words(raw_literal))
    if match is None:
        return None
    number = _number_as_float(match.group("number"))
    if not number.is_integer():
        return None
    value = int(number)
    return ParsedLiteral(
        resolved_value=value,
        normalized_value=value,
        source="typed_parser.numeric",
        confidence=0.97,
        target=value,
    )


def _parse_float_or_capacity(raw_literal: str) -> ParsedLiteral | None:
    match = _NUMBER_RE.search(_strip_numeric_words(raw_literal))
    if match is None:
        return None
    value = _number_as_float(match.group("number"))
    unit = match.group("unit").lower()
    multiplier = {
        "": 1.0,
        "k": 0.001,
        "m": 1.0,
        "g": 1000.0,
        "t": 1_000_000.0,
    }[unit]
    normalized = value * multiplier
    return ParsedLiteral(
        resolved_value=normalized,
        normalized_value=normalized,
        source="typed_parser.numeric",
        confidence=0.97,
        target=normalized,
    )


def _strip_numeric_words(raw_literal: str) -> str:
    return (
        raw_literal.strip()
        .removeprefix("大于")
        .removeprefix("超过")
        .removeprefix("前")
        .removesuffix("个")
        .strip()
    )


def _number_as_float(raw_number: str) -> float:
    return float(raw_number.replace(",", ""))
