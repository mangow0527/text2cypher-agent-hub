from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import ValidationError

from .model import GraphSemanticModel
from .registry import GraphSemanticRegistry
from .validator import (
    GraphModelValidationError,
    GraphModelValidationIssue,
    GraphModelValidationResult,
    validate_graph_model,
)


_ARTIFACT_VALIDATION_CACHE: set[str] = set()


@dataclass(frozen=True)
class GraphModelLoadResult:
    registry: GraphSemanticRegistry
    model_checksum: str
    validation_result: GraphModelValidationResult


def load_graph_semantic_model(source: str | Path | Mapping[str, Any]) -> GraphModelLoadResult:
    payload = _extract_model_payload(_load_source(source))
    try:
        model = GraphSemanticModel.model_validate(payload)
    except ValidationError as exc:
        raise GraphModelValidationError(_validation_result_from_pydantic(exc)) from exc

    validation_result = validate_graph_model(model)
    if not validation_result.is_valid:
        raise GraphModelValidationError(validation_result)

    registry = GraphSemanticRegistry(model)
    model_checksum = _model_checksum(model)
    _validate_model_artifacts(registry, model_checksum)

    return GraphModelLoadResult(
        registry=registry,
        model_checksum=model_checksum,
        validation_result=validation_result,
    )


def _load_source(source: str | Path | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(source, Mapping):
        return source
    path = Path(source)
    with path.open(encoding="utf-8") as file:
        document = yaml.safe_load(file)
    if not isinstance(document, Mapping):
        raise GraphModelValidationError(
            GraphModelValidationResult(
                is_valid=False,
                errors=[
                    GraphModelValidationIssue(
                        code="invalid_document",
                        message="graph semantic model document must be a mapping",
                        location="$",
                    )
                ],
            )
        )
    return document


def _extract_model_payload(document: Mapping[str, Any]) -> Mapping[str, Any]:
    semantic_model = document.get("semantic_model")
    if semantic_model is None:
        return document
    if not isinstance(semantic_model, list) or len(semantic_model) != 1:
        raise GraphModelValidationError(
            GraphModelValidationResult(
                is_valid=False,
                errors=[
                    GraphModelValidationIssue(
                        code="invalid_semantic_model_wrapper",
                        message="semantic_model wrapper must contain exactly one model",
                        location="semantic_model",
                    )
                ],
            )
        )
    model_payload = semantic_model[0]
    if not isinstance(model_payload, Mapping):
        raise GraphModelValidationError(
            GraphModelValidationResult(
                is_valid=False,
                errors=[
                    GraphModelValidationIssue(
                        code="invalid_semantic_model_wrapper",
                        message="semantic_model item must be a mapping",
                        location="semantic_model[0]",
                    )
                ],
            )
        )
    return model_payload


def _validation_result_from_pydantic(exc: ValidationError) -> GraphModelValidationResult:
    return GraphModelValidationResult(
        is_valid=False,
        errors=[
            GraphModelValidationIssue(
                code="model_parse_error",
                message=f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}",
                location=".".join(str(part) for part in error["loc"]),
            )
            for error in exc.errors()
        ],
    )


def _model_checksum(model: GraphSemanticModel) -> str:
    canonical_json = json.dumps(
        model.model_dump(mode="json", by_alias=True, exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _validate_model_artifacts(registry: GraphSemanticRegistry, model_checksum: str) -> None:
    if model_checksum in _ARTIFACT_VALIDATION_CACHE:
        return

    from services.cypher_generator_agent.app.cypher_validation import CypherSelfValidator

    validator = CypherSelfValidator(registry)
    errors: list[GraphModelValidationIssue] = []

    for path_pattern in registry.model.path_patterns:
        result = validator.validate_model_artifact(
            path_pattern.cypher,
            source_kind="path_pattern",
            source_name=path_pattern.name,
        )
        _extend_artifact_errors(
            errors,
            result,
            location=f"path_patterns.{path_pattern.name}.cypher",
        )

    for metric in registry.model.metrics:
        if not metric.full_cypher:
            continue
        result = validator.validate_model_artifact(
            metric.full_cypher,
            source_kind="metric_full_cypher",
            source_name=metric.name,
        )
        _extend_artifact_errors(
            errors,
            result,
            location=f"metrics.{metric.name}.full_cypher",
        )

    if errors:
        raise GraphModelValidationError(GraphModelValidationResult(is_valid=False, errors=errors))

    _ARTIFACT_VALIDATION_CACHE.add(model_checksum)


def _extend_artifact_errors(
    errors: list[GraphModelValidationIssue],
    result: Any,
    *,
    location: str,
) -> None:
    for issue in result.errors:
        errors.append(
            GraphModelValidationIssue(
                code=issue.code,
                message=(
                    f"{location}: {issue.message} "
                    f"(self_validation_check={issue.check}, self_validation_location={issue.location})"
                ),
                location=location,
            )
        )
