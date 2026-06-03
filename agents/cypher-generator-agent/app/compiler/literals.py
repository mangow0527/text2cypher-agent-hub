from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
import re


PARAMETER_RE = re.compile(r"(?<![A-Za-z0-9_])\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)")


def escape_cypher_literal(value: object) -> str:
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if value is None:
        return "null"
    if isinstance(value, datetime):
        return f"datetime('{value.isoformat()}')"
    if isinstance(value, date):
        return f"datetime('{value.isoformat()}')"
    if isinstance(value, list):
        return "[" + ", ".join(escape_cypher_literal(item) for item in value) + "]"
    raise NotImplementedError(f"unsupported Cypher literal type: {type(value).__name__}")


def inline_cypher_parameters(cypher_template: str, parameters: Mapping[str, object]) -> str:
    template_parameters = extract_parameter_names(cypher_template)
    provided_parameters = set(parameters)
    missing = sorted(template_parameters - provided_parameters)
    extra = sorted(provided_parameters - template_parameters)
    if missing:
        raise ValueError(f"missing parameters for Cypher template: {missing}")
    if extra:
        raise ValueError(f"extra parameters not used by Cypher template: {extra}")

    def replace(match: re.Match[str]) -> str:
        return escape_cypher_literal(parameters[match.group("name")])

    cypher = PARAMETER_RE.sub(replace, cypher_template)
    remaining = extract_parameter_names(cypher)
    if remaining:
        raise ValueError(f"Cypher still contains parameter placeholders after inlining: {sorted(remaining)}")
    return cypher


def extract_parameter_names(cypher: str) -> set[str]:
    return {match.group("name") for match in PARAMETER_RE.finditer(cypher)}

