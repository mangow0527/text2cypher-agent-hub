from __future__ import annotations

import re

from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry

from .models import (
    CypherSelfValidationRequest,
    CypherSelfValidationResult,
    CypherValidationCheck,
    CypherValidationIssue,
    SourceKind,
    validation_error,
)
from .dialect import validate_target_dialect
from .parser import _inside_string, parse_cypher
from .readonly import validate_readonly
from .schema_reference import validate_schema_references
from .shape import validate_compiler_shape


class CypherSelfValidator:
    def __init__(self, registry: GraphSemanticRegistry) -> None:
        self.registry = registry

    def validate(self, request: CypherSelfValidationRequest) -> CypherSelfValidationResult:
        return self._validate(request)

    def validate_generated_query(
        self,
        cypher: str,
        *,
        expected_return_aliases: list[str] | None = None,
    ) -> CypherSelfValidationResult:
        return self._validate(
            CypherSelfValidationRequest(
                mode="generated_query",
                source_kind="compiled_query",
                cypher=cypher,
                expected_return_aliases=expected_return_aliases,
            )
        )

    def validate_model_artifact(
        self,
        cypher: str,
        *,
        source_kind: SourceKind,
        source_name: str,
    ) -> CypherSelfValidationResult:
        return self._validate(
            CypherSelfValidationRequest(
                mode="model_artifact",
                source_kind=source_kind,
                source_name=source_name,
                cypher=cypher,
            )
        )

    def _validate(self, request: CypherSelfValidationRequest) -> CypherSelfValidationResult:
        checks: list[CypherValidationCheck] = []
        errors: list[CypherValidationIssue] = []
        warnings: list[CypherValidationIssue] = []

        parsed, syntax_errors = parse_cypher(request.cypher)
        if syntax_errors:
            errors.extend(syntax_errors)
            checks.append(CypherValidationCheck(name="syntax", status="failed"))
            checks.extend(_skipped_follow_up_checks(request, model_artifact_status="failed"))
            return _result(request, checks, errors, warnings)
        if parsed is None:
            checks.append(CypherValidationCheck(name="syntax", status="failed"))
            checks.extend(_skipped_follow_up_checks(request, model_artifact_status="failed"))
            return _result(request, checks, errors, warnings)
        checks.append(CypherValidationCheck(name="syntax", status="passed"))

        readonly_errors = validate_readonly(parsed)
        if readonly_errors:
            errors.extend(readonly_errors)
            checks.append(CypherValidationCheck(name="readonly", status="failed"))
            checks.extend(_skipped_after_readonly_checks(request, model_artifact_status="failed"))
            return _result(request, checks, errors, warnings)
        checks.append(CypherValidationCheck(name="readonly", status="passed"))

        dialect_errors = validate_target_dialect(parsed)
        if dialect_errors:
            errors.extend(dialect_errors)
            checks.append(CypherValidationCheck(name="dialect", status="failed"))
            checks.append(CypherValidationCheck(name="schema_reference", status="skipped"))
            checks.extend(_ir03b_check_slots(request, model_artifact_status="failed"))
            return _result(request, checks, errors, warnings)
        checks.append(CypherValidationCheck(name="dialect", status="passed"))

        schema_errors = validate_schema_references(parsed, self.registry)
        if schema_errors:
            errors.extend(schema_errors)
            checks.append(CypherValidationCheck(name="schema_reference", status="failed"))
            checks.extend(_ir03b_check_slots(request, model_artifact_status="failed"))
            return _result(request, checks, errors, warnings)
        checks.append(CypherValidationCheck(name="schema_reference", status="passed"))

        if request.mode == "generated_query":
            parameter_names = _unquoted_parameter_names(request.cypher)
            if parameter_names:
                errors.append(
                    validation_error(
                        "cypher_parameter_placeholder_not_allowed",
                        "generated executable Cypher must be inline and must not contain parameter placeholders: "
                        + ", ".join(f"${name}" for name in sorted(parameter_names)),
                        "parameters_inline",
                    )
                )
                checks.append(CypherValidationCheck(name="parameters_inline", status="failed"))
                checks.extend(_ir03b_check_slots(request, model_artifact_status="failed"))
                return _result(request, checks, errors, warnings)
            checks.append(CypherValidationCheck(name="parameters_inline", status="passed"))
        else:
            checks.append(CypherValidationCheck(name="parameters_inline", status="skipped"))

        if request.expected_return_aliases is None:
            checks.append(CypherValidationCheck(name="shape", status="skipped"))
        else:
            shape_errors = validate_compiler_shape(parsed, request.expected_return_aliases)
            if shape_errors:
                errors.extend(shape_errors)
                checks.append(CypherValidationCheck(name="shape", status="failed"))
                checks.append(_model_artifact_check(request, status="failed"))
                return _result(request, checks, errors, warnings)
            checks.append(CypherValidationCheck(name="shape", status="passed"))

        checks.append(_model_artifact_check(request, status="passed"))

        return _result(request, checks, errors, warnings)


def _result(
    request: CypherSelfValidationRequest,
    checks: list[CypherValidationCheck],
    errors: list[CypherValidationIssue],
    warnings: list[CypherValidationIssue],
) -> CypherSelfValidationResult:
    return CypherSelfValidationResult(
        valid=not errors,
        mode=request.mode,
        checks=checks,
        errors=errors,
        warnings=warnings,
    )


def _skipped_follow_up_checks(
    request: CypherSelfValidationRequest,
    *,
    model_artifact_status: str,
) -> list[CypherValidationCheck]:
    return [
        CypherValidationCheck(name="readonly", status="skipped"),
        CypherValidationCheck(name="dialect", status="skipped"),
        CypherValidationCheck(name="schema_reference", status="skipped"),
        CypherValidationCheck(name="parameters_inline", status="skipped"),
        *_ir03b_check_slots(request, model_artifact_status=model_artifact_status),
    ]


def _skipped_after_readonly_checks(
    request: CypherSelfValidationRequest,
    *,
    model_artifact_status: str,
) -> list[CypherValidationCheck]:
    return [
        CypherValidationCheck(name="dialect", status="skipped"),
        CypherValidationCheck(name="schema_reference", status="skipped"),
        CypherValidationCheck(name="parameters_inline", status="skipped"),
        *_ir03b_check_slots(request, model_artifact_status=model_artifact_status),
    ]


def _ir03b_check_slots(
    request: CypherSelfValidationRequest,
    *,
    model_artifact_status: str,
) -> list[CypherValidationCheck]:
    return [
        CypherValidationCheck(name="parameters_inline", status="skipped"),
        CypherValidationCheck(name="shape", status="skipped"),
        _model_artifact_check(request, status=model_artifact_status),
    ]


def _model_artifact_check(
    request: CypherSelfValidationRequest,
    *,
    status: str,
) -> CypherValidationCheck:
    return CypherValidationCheck(
        name="model_artifact",
        status=status if request.mode == "model_artifact" else "skipped",
    )


PARAMETER_RE = re.compile(r"(?<![A-Za-z0-9_])\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)")


def _unquoted_parameter_names(cypher: str) -> set[str]:
    return {
        match.group("name")
        for match in PARAMETER_RE.finditer(cypher)
        if not _inside_string(cypher, match.start())
    }
