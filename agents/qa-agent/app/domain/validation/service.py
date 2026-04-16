from __future__ import annotations

from app.domain.difficulty import DifficultyService
from app.domain.models import (
    CanonicalSchemaSpec,
    CypherCandidate,
    ResultSignature,
    RuntimeMeta,
    ValidatedSample,
    ValidationConfig,
    ValidationResult,
)
from app.domain.validation.structure_rules import StructureRuleValidator
from app.integrations.tugraph.graph_executor import GraphExecutor


class ValidationService:
    def __init__(
        self,
        graph_executor: GraphExecutor | None = None,
        difficulty_service: DifficultyService | None = None,
        structure_rule_validator: StructureRuleValidator | None = None,
    ) -> None:
        self.graph_executor = graph_executor or GraphExecutor()
        self.difficulty_service = difficulty_service or DifficultyService()
        self.structure_rule_validator = structure_rule_validator or StructureRuleValidator()

    def validate(self, candidate: CypherCandidate, schema: CanonicalSchemaSpec, config: ValidationConfig, tugraph_config) -> ValidatedSample:
        result = ValidationResult()

        result.syntax = candidate.cypher.strip().upper().startswith("MATCH")
        result.schema_valid = all(node in schema.node_types for node in candidate.bound_schema_items.get("nodes", []))
        result.type_value = True
        structure_check = self.structure_rule_validator.validate(
            query_type=candidate.query_types[0],
            structure_family=candidate.structure_family,
            cypher=candidate.cypher,
        )
        result.query_type_valid = structure_check["query_type_valid"]
        result.family_valid = structure_check["family_valid"]
        classified_difficulty = self.difficulty_service.classify(candidate.cypher)
        result.difficulty_valid = candidate.difficulty == classified_difficulty

        runtime_meta = RuntimeMeta()
        result_signature = ResultSignature()
        if (
            result.syntax
            and result.schema_valid
            and result.type_value
            and result.query_type_valid
            and result.family_valid
            and result.difficulty_valid
        ):
            runtime_meta, result_signature, runtime_ok = self.graph_executor.execute(candidate.cypher, tugraph_config)
            if runtime_meta.planner == "mock":
                runtime_ok = False
            result.runtime = runtime_ok if config.require_runtime_validation else True
            result.result_sanity = runtime_ok and (
                config.allow_empty_results or result_signature.row_count > 0 or bool(result_signature.result_preview)
            )

        return ValidatedSample(
            candidate=candidate,
            validation=result,
            runtime_meta=runtime_meta,
            result_signature=result_signature,
            classified_difficulty=classified_difficulty,
        )
