from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


REQUEST_SCHEMA_VERSION = "cypher_self_validation_request_v1"
RESULT_SCHEMA_VERSION = "cypher_self_validation_result_v1"

ValidationMode = Literal["model_artifact", "generated_query"]
SourceKind = Literal["compiled_query", "path_pattern", "metric_full_cypher"]
CheckStatus = Literal["passed", "failed", "skipped"]
FailureCode = Literal[
    "cypher_syntax_invalid",
    "cypher_readonly_violation",
    "cypher_schema_reference_invalid",
    "cypher_parameter_placeholder_not_allowed",
    "target_dialect_static_error",
    "compiler_shape_mismatch",
]


class CypherValidationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CypherSelfValidationRequest(CypherValidationBase):
    schema_version: Literal["cypher_self_validation_request_v1"] = REQUEST_SCHEMA_VERSION
    mode: ValidationMode
    source_kind: SourceKind
    source_name: Optional[str] = None
    cypher: str
    graph_model_checksum: Optional[str] = None
    target_dialect: Optional[str] = None
    expected_return_aliases: list[str] | None = None


class CypherValidationCheck(CypherValidationBase):
    name: str
    status: CheckStatus


class CypherValidationIssue(CypherValidationBase):
    code: FailureCode
    severity: Literal["error", "warning"]
    message: str
    check: str
    location: str = Field(default="$")


class CypherSelfValidationResult(CypherValidationBase):
    schema_version: Literal["cypher_self_validation_result_v1"] = RESULT_SCHEMA_VERSION
    valid: bool
    mode: ValidationMode
    checks: list[CypherValidationCheck] = Field(default_factory=list)
    errors: list[CypherValidationIssue] = Field(default_factory=list)
    warnings: list[CypherValidationIssue] = Field(default_factory=list)


def validation_error(
    code: FailureCode,
    message: str,
    check: str,
    location: str = "$",
) -> CypherValidationIssue:
    return CypherValidationIssue(
        code=code,
        severity="error",
        message=message,
        check=check,
        location=location,
    )
