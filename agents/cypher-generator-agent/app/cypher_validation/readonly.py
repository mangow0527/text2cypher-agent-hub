from __future__ import annotations

from .models import CypherValidationIssue, validation_error
from .parser import ALLOWED_READ_CLAUSES, READONLY_DIALECT_CLAUSES, ParsedCypher


READONLY_FAILURE_CODE = "cypher_readonly_violation"


def validate_readonly(parsed: ParsedCypher) -> list[CypherValidationIssue]:
    errors: list[CypherValidationIssue] = []
    for clause in parsed.clauses:
        if clause.name in ALLOWED_READ_CLAUSES or clause.name in READONLY_DIALECT_CLAUSES:
            continue
        errors.append(
            validation_error(
                READONLY_FAILURE_CODE,
                f"clause {clause.name} is not allowed in readonly Cypher",
                "readonly",
                f"char:{clause.start}",
            )
        )
    return errors
