from __future__ import annotations

from app.domain.difficulty import DifficultyService
from app.domain.models import (
    CanonicalSchemaSpec,
    CypherCandidate,
    QueryPlan,
    ResultSignature,
    RuntimeMeta,
    ValidatedSample,
    ValidationConfig,
    ValidationResult,
)
from app.domain.validation.structure_rules import StructureRuleValidator
from app.domain.validation.plan_validator import PlanValidator
from app.integrations.tugraph.graph_executor import GraphExecutor


class ValidationService:
    def __init__(
        self,
        graph_executor: GraphExecutor | None = None,
        difficulty_service: DifficultyService | None = None,
        structure_rule_validator: StructureRuleValidator | None = None,
        plan_validator: PlanValidator | None = None,
    ) -> None:
        self.graph_executor = graph_executor or GraphExecutor()
        self.difficulty_service = difficulty_service or DifficultyService()
        self.structure_rule_validator = structure_rule_validator or StructureRuleValidator()
        self.plan_validator = plan_validator or PlanValidator()

    def validate(self, candidate: CypherCandidate, schema: CanonicalSchemaSpec, config: ValidationConfig, tugraph_config) -> ValidatedSample:
        result = ValidationResult()

        result.syntax = candidate.cypher.strip().upper().startswith("MATCH")
        result.schema_valid = self._schema_items_valid(candidate, schema)
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
        query_plan = self._candidate_plan(candidate)
        if query_plan is not None:
            plan_result = self.plan_validator.validate(query_plan, candidate.cypher)
            result.plan_valid = bool(plan_result["ok"])
            result.plan_reasons = list(plan_result["reasons"])

        runtime_meta = RuntimeMeta()
        result_signature = ResultSignature()
        if (
            result.syntax
            and result.schema_valid
            and result.type_value
            and result.query_type_valid
            and result.family_valid
            and result.difficulty_valid
            and result.plan_valid
        ):
            runtime_meta, result_signature, runtime_ok = self.graph_executor.execute(candidate.cypher, tugraph_config)
            if runtime_meta.planner == "mock":
                runtime_ok = False
            result.runtime = runtime_ok if config.require_runtime_validation else True
            result.result_sanity = runtime_ok and (
                config.allow_empty_results
                or result_signature.row_count > 0
                or bool(result_signature.result_preview)
                or bool(result_signature.result_rows)
            )

        return ValidatedSample(
            candidate=candidate,
            validation=result,
            runtime_meta=runtime_meta,
            result_signature=result_signature,
            classified_difficulty=classified_difficulty,
        )

    def _candidate_plan(self, candidate: CypherCandidate) -> QueryPlan | None:
        plan_payload = candidate.query_plan
        if not plan_payload:
            return None
        if isinstance(plan_payload, QueryPlan):
            return plan_payload
        try:
            return QueryPlan.model_validate(plan_payload)
        except Exception:
            return None

    def _schema_items_valid(self, candidate: CypherCandidate, schema: CanonicalSchemaSpec) -> bool:
        nodes = [node for node in candidate.bound_schema_items.get("nodes", []) if node]
        edges = [edge for edge in candidate.bound_schema_items.get("edges", []) if edge]
        if any(node not in schema.node_types for node in nodes):
            return False
        if any(edge not in schema.edge_types for edge in edges):
            return False
        if not schema.edge_constraints:
            return True

        for index, edge in enumerate(edges):
            if index + 1 >= len(nodes):
                continue
            allowed_pairs = {tuple(pair) for pair in schema.edge_constraints.get(edge, [])}
            if allowed_pairs and (nodes[index], nodes[index + 1]) not in allowed_pairs:
                return False
        return True
