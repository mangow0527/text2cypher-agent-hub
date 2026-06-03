from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from services.cypher_generator_agent.app.compiler.literals import (
    escape_cypher_literal,
    inline_cypher_parameters,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Gold", "'Gold'"),
        ("金牌", "'金牌'"),
        ("Tom's", r"'Tom\'s'"),
        (r"a\b", r"'a\\b'"),
        ("", "''"),
        (-3, "-3"),
        (0, "0"),
        (1.25, "1.25"),
        (True, "true"),
        (False, "false"),
        (None, "null"),
        ([], "[]"),
        (["Gold"], "['Gold']"),
        (["Gold", 2, False], "['Gold', 2, false]"),
        (date(2026, 5, 29), "datetime('2026-05-29')"),
        (datetime(2026, 5, 29, 13, 14, 15), "datetime('2026-05-29T13:14:15')"),
    ],
)
def test_escape_cypher_literal_formats_supported_values(value: object, expected: str) -> None:
    assert escape_cypher_literal(value) == expected


def test_escape_cypher_literal_rejects_unsupported_values() -> None:
    with pytest.raises(NotImplementedError, match="unsupported Cypher literal type"):
        escape_cypher_literal(Decimal("1.23"))


def test_inline_cypher_parameters_replaces_all_template_parameters() -> None:
    cypher = "MATCH (svc:Service) WHERE svc.quality_of_service = $qos AND svc.id IN $ids RETURN svc"

    executable = inline_cypher_parameters(
        cypher,
        {"qos": "Gold", "ids": ["svc-001", "svc-002"]},
    )

    assert executable == (
        "MATCH (svc:Service) "
        "WHERE svc.quality_of_service = 'Gold' AND svc.id IN ['svc-001', 'svc-002'] "
        "RETURN svc"
    )
    assert "$qos" not in executable
    assert "$ids" not in executable


def test_inline_cypher_parameters_rejects_missing_parameters() -> None:
    with pytest.raises(ValueError, match="missing parameters"):
        inline_cypher_parameters("MATCH (n) WHERE n.id = $id RETURN n", {})


def test_inline_cypher_parameters_rejects_extra_parameters() -> None:
    with pytest.raises(ValueError, match="extra parameters"):
        inline_cypher_parameters("MATCH (n) RETURN n", {"id": "ne-0001"})

