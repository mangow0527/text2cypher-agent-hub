from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any

from services.cypher_generator_agent.app.binding.models import BindingPlan
from services.cypher_generator_agent.app.dsl.models import QueryShape
from services.cypher_generator_agent.app.semantic_model.registry import GraphSemanticRegistry, RegistryLookupError

from .coverage import CoverageReport, build_coverage_report
from .models import SemanticValidationIssue, SemanticValidationResult


SUPPORTED_QUERY_SHAPES = frozenset(shape.value for shape in QueryShape)
METRIC_PATTERN_ALIAS_RE = re.compile(r"\((?:(?P<alias>\w+)\s*)?:(?P<label>[A-Z][A-Za-z0-9]*)\)")
NUMERIC_PROPERTY_TYPES = frozenset({"int", "integer", "float", "number"})


class SemanticValidator:
    def __init__(self, registry: GraphSemanticRegistry) -> None:
        self.registry = registry

    def validate(
        self,
        plan: BindingPlan,
        *,
        coverage: CoverageReport | Mapping[str, Any] | None = None,
    ) -> SemanticValidationResult:
        errors: list[SemanticValidationIssue] = []
        warnings: list[SemanticValidationIssue] = []
        assumptions = [dict(assumption) for assumption in plan.assumptions]

        coverage_report = build_coverage_report(coverage) if coverage is not None else None
        if coverage_report is not None:
            coverage_report = self._merge_plan_slot_coverage(plan, coverage_report)
            self._validate_coverage(coverage_report, errors, warnings, assumptions)

        self._validate_dsl_support(plan, errors)
        self._validate_edge_endpoints(plan, errors)
        self._validate_property_owners(plan, errors)
        self._validate_metric_dimensions(plan, errors)
        self._validate_aggregate_function_types(plan, errors)

        return SemanticValidationResult(
            errors=errors,
            warnings=warnings,
            assumptions=assumptions,
        )

    def _validate_coverage(
        self,
        coverage: CoverageReport,
        errors: list[SemanticValidationIssue],
        warnings: list[SemanticValidationIssue],
        assumptions: list[dict[str, Any]],
    ) -> None:
        uncovered = list(coverage.substantive_terms.uncovered)
        time_unresolved = list(coverage.time_terms.unresolved)
        unparsed_unresolved = list(coverage.unparsed_terms.unresolved)

        terms = [*uncovered, *time_unresolved, *unparsed_unresolved]
        if terms:
            errors.append(
                SemanticValidationIssue(
                    code="coverage_failure",
                    message=f"Coverage failed for unresolved semantic terms: {', '.join(terms)}",
                    severity="error",
                    recoverability="non_repairable",
                    action="ask_user",
                    details={
                        "substantive_uncovered": uncovered,
                        "time_unresolved": time_unresolved,
                        "unparsed_unresolved": unparsed_unresolved,
                    },
                )
            )

        for term in coverage.modality_terms.warning_only:
            message = f"问题中的“{term}”没有被解释为查询约束。"
            warnings.append(
                SemanticValidationIssue(
                    code="modality_warning",
                    message=message,
                    severity="warning",
                    recoverability="warning_only",
                    action="continue_with_assumption",
                    details={"term": term},
                )
            )
            assumptions.append(
                {
                    "type": "modality_warning",
                    "term": term,
                    "message": message,
                }
            )

        projection_uncovered = list(coverage.projection_terms.uncovered)
        if projection_uncovered:
            errors.append(
                SemanticValidationIssue(
                    code="projection_coverage_missing",
                    message=(
                        "Projection coverage failed for required return terms: "
                        f"{', '.join(projection_uncovered)}"
                    ),
                    severity="error",
                    recoverability="repairable",
                    action="repair_binding",
                    details={
                        "required": list(coverage.projection_terms.required),
                        "covered": list(coverage.projection_terms.covered),
                        "uncovered": projection_uncovered,
                    },
                )
            )

    def _merge_plan_slot_coverage(
        self,
        plan: BindingPlan,
        coverage: CoverageReport,
    ) -> CoverageReport:
        projection = coverage.projection_terms
        if not projection.required:
            return coverage

        covered = list(projection.covered)
        for item in plan.projection:
            raw_terms = item.get("projection_terms") if isinstance(item, Mapping) else None
            if not isinstance(raw_terms, list | tuple):
                continue
            for raw_term in raw_terms:
                term = str(raw_term).strip()
                if term and term not in covered:
                    covered.append(term)

        uncovered = [term for term in projection.required if term not in covered]
        return coverage.model_copy(
            update={
                "projection_terms": projection.model_copy(
                    update={"covered": covered, "uncovered": uncovered}
                )
            }
        )

    def _validate_dsl_support(
        self,
        plan: BindingPlan,
        errors: list[SemanticValidationIssue],
    ) -> None:
        if plan.query_shape in SUPPORTED_QUERY_SHAPES:
            return
        errors.append(
            SemanticValidationIssue(
                code="unsupported_query_shape",
                message=f"Query shape {plan.query_shape!r} is not supported by restricted_query_dsl_v1.",
                severity="error",
                recoverability="non_repairable",
                action="unsupported_query_shape",
                details={"query_shape": plan.query_shape},
            )
        )

    def _validate_edge_endpoints(
        self,
        plan: BindingPlan,
        errors: list[SemanticValidationIssue],
    ) -> None:
        if plan.query_shape != "single_hop_traversal":
            return

        missing: list[str] = []
        if not plan.vertex_bindings:
            missing.extend(["from_vertex", "to_vertex"])
        elif len(plan.vertex_bindings) == 1:
            missing.append("to_vertex")
        if not plan.edge_bindings:
            missing.append("edge_bindings")
        if len(plan.edge_bindings) > 1 and len(plan.vertex_bindings) != len(plan.edge_bindings) + 1:
            missing.append("traversal_chain_vertices")
        if missing:
            errors.append(
                SemanticValidationIssue(
                    code="binding_plan_incomplete",
                    message=f"single_hop_traversal binding plan is missing {', '.join(missing)}.",
                    severity="error",
                    recoverability="repairable",
                    action="repair_binding",
                    details={"query_shape": plan.query_shape, "missing": missing},
                )
            )
            return

        for edge_index, edge_binding in enumerate(plan.edge_bindings):
            from_vertex = plan.vertex_bindings[edge_index].name
            to_vertex = plan.vertex_bindings[edge_index + 1].name
            try:
                edge = self.registry.get_edge(edge_binding.name)
            except RegistryLookupError:
                errors.append(
                    SemanticValidationIssue(
                        code="edge_endpoint_mismatch",
                        message=f"Edge {edge_binding.name} is not declared in the semantic model.",
                        severity="error",
                        recoverability="repairable",
                        action="repair_binding",
                        details={"edge": edge_binding.name, "edge_index": edge_index},
                    )
                )
                continue

            registry_direction = "reverse" if edge_binding.direction == "backward" else "forward"
            if self.registry.edge_connects(edge_binding.name, from_vertex, to_vertex, registry_direction):
                continue

            expected_from = edge.to_vertex if edge_binding.direction == "backward" else edge.from_vertex
            expected_to = edge.from_vertex if edge_binding.direction == "backward" else edge.to_vertex
            errors.append(
                SemanticValidationIssue(
                    code="edge_endpoint_mismatch",
                    message=(
                        f"Edge {edge_binding.name} expects {expected_from} -> {expected_to} "
                        f"for {edge_binding.direction} traversal but binding plan connects "
                        f"{from_vertex} -> {to_vertex}."
                    ),
                    severity="error",
                    recoverability="repairable",
                    action="repair_binding",
                    details={
                        "edge": edge_binding.name,
                        "edge_index": edge_index,
                        "direction": edge_binding.direction,
                        "expected_from": expected_from,
                        "expected_to": expected_to,
                        "actual_from": from_vertex,
                        "actual_to": to_vertex,
                    },
                )
            )

    def _validate_property_owners(
        self,
        plan: BindingPlan,
        errors: list[SemanticValidationIssue],
    ) -> None:
        seen: set[tuple[str, str, str]] = set()
        for owner, property_name, location in _property_references(plan):
            key = (owner, property_name, location)
            if key in seen:
                continue
            seen.add(key)
            try:
                self.registry.get_property(owner, property_name)
            except RegistryLookupError:
                errors.append(
                    SemanticValidationIssue(
                        code="property_owner_mismatch",
                        message=f"Property {owner}.{property_name} is not declared on owner {owner}.",
                        severity="error",
                        recoverability="repairable",
                        action="repair_binding",
                        details={
                            "owner": owner,
                            "property": property_name,
                            "location": location,
                        },
                    )
                )

    def _validate_metric_dimensions(
        self,
        plan: BindingPlan,
        errors: list[SemanticValidationIssue],
    ) -> None:
        if plan.query_shape not in {"metric_aggregate", "top_n"}:
            return
        if not plan.metric_bindings:
            return

        for metric_binding in plan.metric_bindings:
            try:
                metric = self.registry.get_metric(metric_binding.name)
            except RegistryLookupError:
                errors.append(
                    SemanticValidationIssue(
                        code="metric_dimension_invalid",
                        message=f"Metric {metric_binding.name} is not declared in the semantic model.",
                        severity="error",
                        recoverability="repairable",
                        action="repair_binding",
                        details={"metric": metric_binding.name},
                    )
                )
                continue

            valid_dimensions = set(metric.valid_dimensions)
            metric_aliases = _metric_pattern_aliases(metric.pattern or "")
            for index, item in enumerate(plan.group_by):
                dimension = _dimension_key(item)
                if dimension is None:
                    errors.append(
                        SemanticValidationIssue(
                            code="metric_group_by_invalid",
                            message=(
                                "metric_aggregate group_by item must include alias, target, "
                                "and property owner/name."
                            ),
                            severity="error",
                            recoverability="repairable",
                            action="repair_binding",
                            details={
                                "metric": metric_binding.name,
                                "location": f"group_by[{index}]",
                                "group_by": dict(item),
                            },
                        )
                    )
                    continue
                canonical_dimension = _canonical_metric_dimension(
                    item,
                    valid_dimensions=valid_dimensions,
                    metric_aliases=metric_aliases,
                )
                if canonical_dimension is not None:
                    owner = _dimension_owner(item)
                    target = canonical_dimension.split(".", 1)[0]
                    expected_owner = metric_aliases.get(target)
                    if expected_owner is not None and owner != expected_owner:
                        errors.append(
                            SemanticValidationIssue(
                                code="metric_dimension_invalid",
                                message=(
                                    f"Metric {metric_binding.name} dimension {canonical_dimension} uses "
                                    f"property owner {owner}, but alias {target} refers to {expected_owner}."
                                ),
                                severity="error",
                                recoverability="repairable",
                                action="repair_binding",
                                details={
                                    "metric": metric_binding.name,
                                    "dimension": canonical_dimension,
                                    "location": f"group_by[{index}]",
                                    "expected_owner": expected_owner,
                                    "actual_owner": owner,
                                },
                            )
                        )
                    continue
                errors.append(
                    SemanticValidationIssue(
                        code="metric_dimension_invalid",
                        message=(
                            f"Metric {metric_binding.name} does not allow group_by dimension {dimension}; "
                            f"valid dimensions are {sorted(valid_dimensions)}."
                        ),
                        severity="error",
                        recoverability="repairable",
                        action="repair_binding",
                        details={
                            "metric": metric_binding.name,
                            "dimension": dimension,
                            "location": f"group_by[{index}]",
                            "valid_dimensions": sorted(valid_dimensions),
                        },
                    )
                )

            alias_by_owner = {owner: alias for alias, owner in metric_aliases.items()}
            for index, filter_item in enumerate(plan.filters):
                alias = alias_by_owner.get(filter_item.owner)
                dimension = f"{alias}.{filter_item.property}" if alias is not None else None
                if dimension in valid_dimensions:
                    continue
                errors.append(
                    SemanticValidationIssue(
                        code="metric_dimension_invalid",
                        message=(
                            f"Metric {metric_binding.name} does not allow filter dimension "
                            f"{dimension or filter_item.owner + '.' + filter_item.property}; "
                            f"valid dimensions are {sorted(valid_dimensions)}."
                        ),
                        severity="error",
                        recoverability="repairable",
                        action="repair_binding",
                        details={
                            "metric": metric_binding.name,
                            "dimension": dimension,
                            "location": f"filters[{index}]",
                            "valid_dimensions": sorted(valid_dimensions),
                        },
                    )
                )

    def _validate_aggregate_function_types(
        self,
        plan: BindingPlan,
        errors: list[SemanticValidationIssue],
    ) -> None:
        for index, item in enumerate(plan.measures):
            function = item.get("function")
            if function == "count":
                continue
            if function not in {"sum", "avg", "min", "max"}:
                continue

            reference = _property_reference(item)
            if reference is None:
                continue
            owner, property_name = reference
            try:
                property_type = self.registry.property_type(owner, property_name)
            except RegistryLookupError:
                continue
            if function in {"sum", "avg"} and property_type not in NUMERIC_PROPERTY_TYPES:
                errors.append(
                    SemanticValidationIssue(
                        code="invalid_aggregate_property_type",
                        message=(
                            f"{function} requires a numeric property, got "
                            f"{owner}.{property_name}:{property_type}."
                        ),
                        severity="error",
                        recoverability="repairable",
                        action="repair_binding",
                        details={
                            "location": f"measures[{index}]",
                            "function": function,
                            "owner": owner,
                            "property": property_name,
                            "property_type": property_type,
                        },
                    )
                )


def _property_references(plan: BindingPlan) -> Iterable[tuple[str, str, str]]:
    for index, binding in enumerate(plan.property_bindings):
        yield binding.owner, binding.name, f"property_bindings[{index}]"
    for index, binding in enumerate(plan.filters):
        yield binding.owner, binding.property, f"filters[{index}]"
    for field_name in ("projection", "sort", "group_by", "measures"):
        values = getattr(plan, field_name, [])
        for index, item in enumerate(values):
            reference = _property_reference(item)
            if reference is not None:
                owner, property_name = reference
                yield owner, property_name, f"{field_name}[{index}]"


def _property_reference(item: Mapping[str, Any]) -> tuple[str, str] | None:
    nested = item.get("property")
    if isinstance(nested, Mapping):
        owner = nested.get("owner")
        name = nested.get("name") or nested.get("property_name")
        if owner and name:
            return str(owner), str(name)

    owner = item.get("owner")
    name = item.get("name") or item.get("property") or item.get("property_name")
    semantic_id = item.get("semantic_id")
    if (owner is None or name is None) and isinstance(semantic_id, str) and "." in semantic_id:
        owner, name = semantic_id.split(".", 1)
    if owner and name:
        return str(owner), str(name)
    return None


def _dimension_key(item: Mapping[str, Any]) -> str | None:
    alias = item.get("alias")
    target = item.get("target")
    nested = item.get("property")
    if not alias or not target or not isinstance(nested, Mapping):
        return None
    name = nested.get("name") or nested.get("property_name")
    owner = nested.get("owner")
    if not owner or not name:
        return None
    return f"{target}.{name}"


def _dimension_owner(item: Mapping[str, Any]) -> str | None:
    nested = item.get("property")
    if not isinstance(nested, Mapping):
        return None
    owner = nested.get("owner")
    return str(owner) if owner else None


def _canonical_metric_dimension(
    item: Mapping[str, Any],
    *,
    valid_dimensions: set[str],
    metric_aliases: Mapping[str, str],
) -> str | None:
    dimension = _dimension_key(item)
    if dimension in valid_dimensions:
        return dimension
    property_ref = item.get("property")
    if not isinstance(property_ref, Mapping):
        return None
    owner = property_ref.get("owner")
    name = property_ref.get("name") or property_ref.get("property_name")
    if not owner or not name:
        return None
    for alias, alias_owner in metric_aliases.items():
        candidate = f"{alias}.{name}"
        if alias_owner == owner and candidate in valid_dimensions:
            return candidate
    return None


def _metric_pattern_aliases(pattern: str) -> dict[str, str]:
    return {
        match.group("alias"): match.group("label")
        for match in METRIC_PATTERN_ALIAS_RE.finditer(pattern)
        if match.group("alias")
    }
