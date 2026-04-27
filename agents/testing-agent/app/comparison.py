from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List

from .models import StrictCheck, StrictDiff, StrictEvidence


def normalize_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, dict):
        if "label" in value and "properties" in value:
            return {
                "__type__": "node",
                "labels": sorted([str(value["label"])]),
                "properties": {
                    key: normalize_value(val)
                    for key, val in sorted(dict(value.get("properties", {})).items())
                },
            }
        if "type" in value and "properties" in value:
            return {
                "__type__": "relationship",
                "rel_type": str(value["type"]),
                "properties": {
                    key: normalize_value(val)
                    for key, val in sorted(dict(value.get("properties", {})).items())
                },
            }
        if "nodes" in value and "relationships" in value:
            return {
                "__type__": "path",
                "nodes": [normalize_value(node) for node in value["nodes"]],
                "relationships": [normalize_value(rel) for rel in value["relationships"]],
            }
        return {key: normalize_value(val) for key, val in sorted(value.items()) if key != "identity"}
    return value


def _canonical_row(value: Any) -> str:
    return json.dumps(normalize_value(value), ensure_ascii=False, sort_keys=True)


def compare_answers(*, golden_answer: Any, actual_answer: Any, order_sensitive: bool) -> StrictCheck:
    golden_rows = golden_answer if isinstance(golden_answer, list) else [golden_answer]
    actual_rows = actual_answer if isinstance(actual_answer, list) else [actual_answer]

    canonical_golden = [_canonical_row(row) for row in golden_rows]
    canonical_actual = [_canonical_row(row) for row in actual_rows]

    if order_sensitive:
        passed = canonical_golden == canonical_actual
        order_mismatch = (Counter(canonical_golden) == Counter(canonical_actual)) and not passed
    else:
        passed = Counter(canonical_golden) == Counter(canonical_actual)
        order_mismatch = None

    if passed:
        return StrictCheck(
            status="pass",
            message=None,
            order_sensitive=order_sensitive,
            expected_row_count=len(golden_rows),
            actual_row_count=len(actual_rows),
            evidence=None,
        )

    missing = list((Counter(canonical_golden) - Counter(canonical_actual)).elements())
    unexpected = list((Counter(canonical_actual) - Counter(canonical_golden)).elements())
    missing_rows = [json.loads(row) for row in missing]
    unexpected_rows = [json.loads(row) for row in unexpected]

    if order_mismatch is True:
        message = "行顺序不一致"
    elif len(golden_rows) != len(actual_rows):
        message = "结果行数不一致"
    else:
        message = "返回字段或字段值不一致"

    return StrictCheck(
        status="fail",
        message=message,
        order_sensitive=order_sensitive,
        expected_row_count=len(golden_rows),
        actual_row_count=len(actual_rows),
        evidence=StrictEvidence(
            golden_answer=[normalize_value(row) for row in golden_rows],
            actual_answer=[normalize_value(row) for row in actual_rows],
            diff=StrictDiff(
                missing_rows=missing_rows,
                unexpected_rows=unexpected_rows,
                order_mismatch=order_mismatch,
            ),
        ),
    )
