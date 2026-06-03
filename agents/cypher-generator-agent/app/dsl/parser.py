from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pydantic import ValidationError

from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry, RegistryLookupError
from services.cypher_generator_agent.app.semantic_model.model import MetricDefinition

from .ast import (
    AggregateOperation,
    Dimension,
    EdgeReference,
    Filter,
    FilterSubqueryOperation,
    LimitOperation,
    Measure,
    MetricAggregateOperation,
    MetricDimension,
    Predicate,
    Projection,
    ProjectionItem,
    PropertyReference,
    RestrictedQueryAst,
    RoleReference,
    SortItem,
    SortOperation,
    SourceReference,
    SubqueryOperation,
    TraverseEdgeOperation,
    UsePathPatternOperation,
    ValueLiteral,
    VariablePathOperation,
)
from .models import (
    AggregateOperationModel,
    DimensionModel,
    FilterModel,
    FilterSubqueryOperationModel,
    LimitOperationModel,
    MeasureModel,
    MetricAggregateOperationModel,
    OperationType,
    PropertyReferenceModel,
    QueryShape,
    RestrictedQueryDslModel,
    SortOperationModel,
    SubqueryOperationModel,
    TraverseEdgeOperationModel,
    UsePathPatternOperationModel,
    ValueModel,
    VariablePathOperationModel,
)


RAW_CYPHER_KEYS = frozenset({"raw_cypher", "cypher_fragment", "where_text"})
NUMERIC_PROPERTY_TYPES = frozenset({"int", "integer", "float", "number"})


@dataclass(frozen=True)
class RestrictedDslValidationIssue:
    code: str
    message: str
    location: str


class RestrictedDslValidationError(ValueError):
    def __init__(self, errors: Sequence[RestrictedDslValidationIssue]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(f"{error.code} at {error.location}: {error.message}" for error in self.errors))


def parse_restricted_query_dsl(
    payload: Mapping[str, Any],
    registry: GraphSemanticRegistry,
) -> RestrictedQueryAst:
    raw_issue = _find_raw_cypher_attribute(payload)
    if raw_issue is not None:
        raise RestrictedDslValidationError([raw_issue])

    try:
        dsl = RestrictedQueryDslModel.model_validate(payload)
    except ValidationError as exc:
        raise RestrictedDslValidationError(_issues_from_pydantic(exc)) from exc

    _validate_operation_sequence(dsl)
    _validate_semantics(dsl, registry)
    return _build_ast(dsl, registry)


def restricted_query_dsl_json_schema() -> dict[str, Any]:
    return RestrictedQueryDslModel.model_json_schema()


def _find_raw_cypher_attribute(value: Any, location: str = "$") -> RestrictedDslValidationIssue | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            child_location = str(key) if location == "$" else f"{location}.{key}"
            if key in RAW_CYPHER_KEYS:
                return RestrictedDslValidationIssue(
                    code="raw_cypher_attribute",
                    message=f"raw Cypher attribute is not allowed: {key}",
                    location=child_location,
                )
            issue = _find_raw_cypher_attribute(item, child_location)
            if issue is not None:
                return issue
    elif isinstance(value, list):
        for index, item in enumerate(value):
            issue = _find_raw_cypher_attribute(item, f"{location}[{index}]")
            if issue is not None:
                return issue
    return None


def _issues_from_pydantic(exc: ValidationError) -> list[RestrictedDslValidationIssue]:
    return [
        RestrictedDslValidationIssue(
            code="model_parse_error",
            message=f"{_format_location(error['loc'])}: {error['msg']}",
            location=_format_location(error["loc"]),
        )
        for error in exc.errors()
    ]


def _format_location(location: tuple[Any, ...]) -> str:
    parts: list[str] = []
    for item in location:
        if isinstance(item, int):
            if not parts:
                parts.append(f"[{item}]")
            else:
                parts[-1] = f"{parts[-1]}[{item}]"
            continue
        text = str(item)
        if text.startswith("function-after") or text.startswith("tagged-union"):
            continue
        parts.append(text)
    return ".".join(parts)


def _validate_operation_sequence(dsl: RestrictedQueryDslModel) -> None:
    ops = [OperationType(operation.op) for operation in dsl.operations]
    shape = dsl.query_shape

    valid = False
    if shape is QueryShape.VERTEX_LOOKUP:
        valid = ops == [] or ops == [OperationType.LIMIT]
    elif shape is QueryShape.SINGLE_HOP_TRAVERSAL:
        valid = _matches_traverse_chain_optional_tail(ops)
    elif shape is QueryShape.VARIABLE_PATH_TRAVERSAL:
        valid = _matches_primary_optional_tail(ops, OperationType.VARIABLE_PATH)
    elif shape is QueryShape.NAMED_PATH_PATTERN:
        valid = _matches_primary_optional_tail(ops, OperationType.USE_PATH_PATTERN)
    elif shape is QueryShape.METRIC_AGGREGATE:
        valid = _matches_primary_optional_tail(ops, OperationType.METRIC_AGGREGATE)
    elif shape is QueryShape.AD_HOC_AGGREGATE:
        valid = _matches_primary_optional_tail(ops, OperationType.AGGREGATE)
    elif shape is QueryShape.TOP_N:
        valid = _matches_top_n_sequence(ops)
    elif shape is QueryShape.TWO_STEP_AGGREGATE:
        valid = _matches_two_step_sequence(ops)

    if not valid:
        sequence_message = f"{shape.value} does not allow operation sequence {[op.value for op in ops]}"
        if shape is QueryShape.TOP_N:
            sequence_message = f"{shape.value} requires aggregate, sort, and limit operations in that order"
        raise RestrictedDslValidationError(
            [
                RestrictedDslValidationIssue(
                    code="invalid_operation_sequence",
                    message=sequence_message,
                    location="operations",
                )
            ]
        )


def _matches_primary_optional_tail(ops: list[OperationType], primary: OperationType) -> bool:
    return ops in (
        [primary],
        [primary, OperationType.SORT],
        [primary, OperationType.LIMIT],
        [primary, OperationType.SORT, OperationType.LIMIT],
    )


def _matches_traverse_chain_optional_tail(ops: list[OperationType]) -> bool:
    if not ops or ops[0] is not OperationType.TRAVERSE_EDGE:
        return False
    index = 0
    while index < len(ops) and ops[index] is OperationType.TRAVERSE_EDGE:
        index += 1
    tail = ops[index:]
    return tail in (
        [],
        [OperationType.SORT],
        [OperationType.LIMIT],
        [OperationType.SORT, OperationType.LIMIT],
    )


def _matches_top_n_sequence(ops: list[OperationType]) -> bool:
    if (
        len(ops) == 3
        and ops[0] in {OperationType.METRIC_AGGREGATE, OperationType.AGGREGATE}
        and ops[1] is OperationType.SORT
        and ops[2] is OperationType.LIMIT
    ):
        return True
    if not ops or ops[0] is not OperationType.TRAVERSE_EDGE:
        return False
    index = 0
    while index < len(ops) and ops[index] is OperationType.TRAVERSE_EDGE:
        index += 1
    return (
        len(ops) == index + 3
        and ops[index] is OperationType.AGGREGATE
        and ops[index + 1] is OperationType.SORT
        and ops[index + 2] is OperationType.LIMIT
    )


def _matches_two_step_sequence(ops: list[OperationType]) -> bool:
    if not ops or ops[0] is not OperationType.SUBQUERY:
        return False
    index = 1
    while index < len(ops) and ops[index] is OperationType.TRAVERSE_EDGE:
        index += 1
    if index > 1:
        return (
            len(ops) >= index + 1
            and ops[index] is OperationType.AGGREGATE
            and ops[index + 1 :] in (
                [],
                [OperationType.SORT],
                [OperationType.LIMIT],
                [OperationType.SORT, OperationType.LIMIT],
            )
        )
    allowed = (
        [OperationType.SUBQUERY],
        [OperationType.SUBQUERY, OperationType.FILTER_SUBQUERY],
        [OperationType.SUBQUERY, OperationType.SORT],
        [OperationType.SUBQUERY, OperationType.LIMIT],
        [OperationType.SUBQUERY, OperationType.FILTER_SUBQUERY, OperationType.SORT],
        [OperationType.SUBQUERY, OperationType.FILTER_SUBQUERY, OperationType.LIMIT],
        [OperationType.SUBQUERY, OperationType.SORT, OperationType.LIMIT],
        [OperationType.SUBQUERY, OperationType.FILTER_SUBQUERY, OperationType.SORT, OperationType.LIMIT],
    )
    return ops in allowed


def _validate_semantics(dsl: RestrictedQueryDslModel, registry: GraphSemanticRegistry) -> None:
    issues: list[RestrictedDslValidationIssue] = []
    vertex_bindings, edge_bindings = _validate_bindings(dsl, registry, issues)

    for index, operation in enumerate(dsl.operations):
        location = f"operations[{index}]"
        if isinstance(operation, TraverseEdgeOperationModel):
            _validate_traverse_edge(operation, registry, vertex_bindings, edge_bindings, location, issues)
        elif isinstance(operation, VariablePathOperationModel):
            _validate_variable_path(operation, registry, vertex_bindings, location, issues)
        elif isinstance(operation, UsePathPatternOperationModel):
            _validate_path_pattern(operation, registry, location, issues)
        elif isinstance(operation, MetricAggregateOperationModel):
            _validate_metric_aggregate(operation, registry, location, issues)
        elif isinstance(operation, AggregateOperationModel):
            _validate_aggregate(operation, registry, vertex_bindings, location, issues)
        elif isinstance(operation, SubqueryOperationModel):
            _validate_subquery(operation, registry, vertex_bindings, edge_bindings, location, issues)

    _validate_filters(dsl.filters, registry, "filters", issues, vertex_bindings=vertex_bindings)
    _validate_projection(dsl, registry, vertex_bindings, _source_outputs(dsl, registry), issues)

    if issues:
        raise RestrictedDslValidationError(issues)


def _validate_bindings(
    dsl: RestrictedQueryDslModel,
    registry: GraphSemanticRegistry,
    issues: list[RestrictedDslValidationIssue],
) -> tuple[dict[str, str], dict[str, str]]:
    vertex_bindings: dict[str, str] = {}
    edge_bindings: dict[str, str] = {}
    for alias, binding in dsl.bindings.items():
        if binding.vertex_name is not None:
            if _lookup(lambda: registry.get_vertex(binding.vertex_name), "unknown_vertex", f"bindings.{alias}.vertex_name", issues):
                vertex_bindings[alias] = binding.vertex_name
        if binding.edge_name is not None:
            if _lookup(lambda: registry.get_edge(binding.edge_name), "unknown_edge", f"bindings.{alias}.edge_name", issues):
                edge_bindings[alias] = binding.edge_name
        if binding.metric_name is not None:
            _lookup(lambda: registry.get_metric(binding.metric_name), "unknown_metric", f"bindings.{alias}.metric_name", issues)
        if binding.property is not None:
            _validate_property(binding.property, registry, f"bindings.{alias}.property", issues)
    return vertex_bindings, edge_bindings


def _validate_traverse_edge(
    operation: TraverseEdgeOperationModel,
    registry: GraphSemanticRegistry,
    vertex_bindings: dict[str, str],
    edge_bindings: dict[str, str],
    location: str,
    issues: list[RestrictedDslValidationIssue],
) -> None:
    from_vertex = vertex_bindings.get(operation.from_ref)
    to_vertex = vertex_bindings.get(operation.to)
    edge_name = edge_bindings.get(operation.edge)
    if from_vertex is None:
        issues.append(_issue("unknown_role_alias", f"unknown vertex role alias {operation.from_ref}", f"{location}.from"))
    if to_vertex is None:
        issues.append(_issue("unknown_role_alias", f"unknown vertex role alias {operation.to}", f"{location}.to"))
    if edge_name is None:
        issues.append(_issue("unknown_edge_alias", f"unknown edge alias {operation.edge}", f"{location}.edge"))
    if from_vertex is None or to_vertex is None or edge_name is None:
        return

    registry_direction = "forward" if operation.direction == "forward" else "reverse"
    if not registry.edge_connects(edge_name, from_vertex, to_vertex, registry_direction):
        issues.append(
            _issue(
                "invalid_edge_endpoint",
                f"edge {edge_name} does not connect {from_vertex} to {to_vertex} with {operation.direction} direction",
                location,
            )
        )


def _validate_variable_path(
    operation: VariablePathOperationModel,
    registry: GraphSemanticRegistry,
    vertex_bindings: dict[str, str],
    location: str,
    issues: list[RestrictedDslValidationIssue],
) -> None:
    if operation.start not in vertex_bindings:
        issues.append(_issue("unknown_role_alias", f"unknown start role alias {operation.start}", f"{location}.start"))
    if operation.through.vertex_ref not in vertex_bindings:
        issues.append(
            _issue(
                "unknown_role_alias",
                f"unknown through role alias {operation.through.vertex_ref}",
                f"{location}.through.vertex_ref",
            )
        )
    for edge_index, edge_name in enumerate(operation.allowed_edges):
        _lookup(
            lambda edge_name=edge_name: registry.get_edge(edge_name),
            "unknown_edge",
            f"{location}.allowed_edges[{edge_index}]",
            issues,
        )
    through_bindings = (
        {operation.through.vertex_ref: vertex_bindings[operation.through.vertex_ref]}
        if operation.through.vertex_ref in vertex_bindings
        else None
    )
    _validate_filters(
        operation.through.filters,
        registry,
        f"{location}.through.filters",
        issues,
        vertex_bindings=through_bindings,
    )


def _validate_path_pattern(
    operation: UsePathPatternOperationModel,
    registry: GraphSemanticRegistry,
    location: str,
    issues: list[RestrictedDslValidationIssue],
) -> None:
    try:
        path_pattern = registry.get_path_pattern(operation.path_pattern_name)
    except RegistryLookupError:
        issues.append(
            _issue(
                "unknown_path_pattern",
                f"path_pattern not found: {operation.path_pattern_name}",
                f"{location}.path_pattern_name",
            )
        )
        return

    expected_parameters = {parameter.name for parameter in path_pattern.parameters}
    extra_parameters = set(operation.parameters) - expected_parameters
    for parameter_name in sorted(extra_parameters):
        issues.append(
            _issue(
                "unknown_path_pattern_parameter",
                f"path_pattern {path_pattern.name} does not declare parameter {parameter_name}",
                f"{location}.parameters.{parameter_name}",
            )
        )

    for parameter in path_pattern.parameters:
        if parameter.name not in operation.parameters:
            issues.append(
                _issue(
                    "missing_path_pattern_parameter",
                    f"path_pattern {path_pattern.name} requires parameter {parameter.name}",
                    f"{location}.parameters.{parameter.name}",
                )
            )
            continue
        value = operation.parameters[parameter.name]
        actual_value = value.normalized if value.normalized is not None else value.raw
        if actual_value is None:
            issues.append(
                _issue(
                    "missing_path_pattern_parameter_value",
                    f"path_pattern {path_pattern.name} parameter {parameter.name} must have a value",
                    f"{location}.parameters.{parameter.name}",
                )
            )
            continue
        if not _parameter_type_matches(actual_value, parameter.type):
            issues.append(
                _issue(
                    "path_pattern_parameter_type",
                    f"parameter {parameter.name} must be {parameter.type}",
                    f"{location}.parameters.{parameter.name}",
                )
            )


def _validate_metric_aggregate(
    operation: MetricAggregateOperationModel,
    registry: GraphSemanticRegistry,
    location: str,
    issues: list[RestrictedDslValidationIssue],
) -> None:
    try:
        metric = registry.get_metric(operation.metric_name)
    except RegistryLookupError:
        issues.append(_issue("unknown_metric", f"metric not found: {operation.metric_name}", f"{location}.metric_name"))
        return

    aliases = _metric_aliases(metric)
    valid_dimensions = set(metric.valid_dimensions)
    for index, dimension in enumerate(operation.group_by):
        _validate_metric_dimension(dimension, registry, aliases, valid_dimensions, f"{location}.group_by[{index}]", issues)
    for index, filter_item in enumerate(operation.filters):
        _validate_property(filter_item.property, registry, f"{location}.filters[{index}].property", issues)
        if filter_item.target is None:
            issues.append(_issue("missing_filter_target", "metric filter must include target", f"{location}.filters[{index}].target"))
            continue
        _validate_metric_dimension_target(
            filter_item.target,
            filter_item.property,
            aliases,
            valid_dimensions,
            f"{location}.filters[{index}]",
            issues,
        )


def _validate_metric_dimension(
    dimension: DimensionModel,
    registry: GraphSemanticRegistry,
    aliases: dict[str, str],
    valid_dimensions: set[str],
    location: str,
    issues: list[RestrictedDslValidationIssue],
) -> None:
    _validate_property(dimension.property, registry, f"{location}.property", issues)
    _validate_metric_dimension_target(dimension.target, dimension.property, aliases, valid_dimensions, location, issues)


def _validate_metric_dimension_target(
    target: str,
    property_ref: PropertyReferenceModel,
    aliases: dict[str, str],
    valid_dimensions: set[str],
    location: str,
    issues: list[RestrictedDslValidationIssue],
) -> None:
    if target not in aliases:
        issues.append(_issue("unknown_metric_alias", f"metric target alias not found: {target}", f"{location}.target"))
        return
    if aliases[target] != property_ref.owner:
        issues.append(
            _issue(
                "metric_dimension_owner_mismatch",
                f"metric alias {target} is {aliases[target]}, not {property_ref.owner}",
                f"{location}.property.owner",
            )
        )
        return
    dimension_name = f"{target}.{property_ref.name}"
    if dimension_name not in valid_dimensions:
        issues.append(
            _issue(
                "invalid_metric_dimension",
                f"metric dimension {dimension_name} is not listed in valid_dimensions",
                location,
            )
        )


def _validate_aggregate(
    operation: AggregateOperationModel,
    registry: GraphSemanticRegistry,
    vertex_bindings: dict[str, str],
    location: str,
    issues: list[RestrictedDslValidationIssue],
) -> None:
    for index, dimension in enumerate(operation.group_by):
        dimension_location = f"{location}.group_by[{index}]"
        if _validate_aggregate_target(dimension.target, dimension.property, vertex_bindings, dimension_location, issues):
            _validate_property(dimension.property, registry, f"{dimension_location}.property", issues)
    for index, measure in enumerate(operation.measures):
        property_location = f"{location}.measures[{index}].property"
        measure_location = f"{location}.measures[{index}]"
        target_valid = _validate_aggregate_target(measure.target, measure.property, vertex_bindings, measure_location, issues)
        if target_valid and _validate_property(measure.property, registry, property_location, issues):
            _validate_measure_function_type(measure.function, measure.property, registry, f"{location}.measures[{index}]", issues)


def _validate_aggregate_target(
    target: str,
    property_ref: PropertyReferenceModel,
    vertex_bindings: dict[str, str],
    location: str,
    issues: list[RestrictedDslValidationIssue],
) -> bool:
    owner = vertex_bindings.get(target)
    if owner is None:
        issues.append(_issue("unknown_aggregate_target", f"aggregate target not found: {target}", f"{location}.target"))
        return False
    if owner != property_ref.owner:
        issues.append(
            _issue(
                "aggregate_owner_mismatch",
                f"aggregate target {target} is {owner}, not {property_ref.owner}",
                f"{location}.property.owner",
            )
        )
        return False
    return True


def _validate_subquery(
    operation: SubqueryOperationModel,
    registry: GraphSemanticRegistry,
    vertex_bindings: dict[str, str],
    edge_bindings: dict[str, str],
    location: str,
    issues: list[RestrictedDslValidationIssue],
) -> None:
    if operation.query_shape not in {QueryShape.AD_HOC_AGGREGATE, QueryShape.SINGLE_HOP_TRAVERSAL}:
        issues.append(
            _issue(
                "invalid_subquery_shape",
                "subquery.query_shape must be ad_hoc_aggregate or single_hop_traversal",
                f"{location}.query_shape",
            )
        )
    if operation.operations:
        operation_models = []
        for index, raw_operation in enumerate(operation.operations):
            try:
                operation_model = TraverseEdgeOperationModel.model_validate(raw_operation)
            except ValidationError:
                issues.append(
                    _issue(
                        "nested_subquery_not_allowed",
                        "subquery operations can only contain traverse_edge operations in restricted_query_dsl_v1",
                        f"{location}.operations[{index}]",
                    )
                )
                continue
            operation_models.append(operation_model)
            _validate_traverse_edge(
                operation_model,
                registry,
                vertex_bindings,
                edge_bindings,
                f"{location}.operations[{index}]",
                issues,
            )
    if not operation.measures:
        issues.append(_issue("missing_subquery_measures", "subquery must include at least one measure", f"{location}.measures"))
        return

    target_aliases = {dimension.target for dimension in operation.group_by} | {
        measure.target for measure in operation.measures
    }
    if len(target_aliases) > 1:
        issues.append(
            _issue(
                "unsupported_subquery_vertex_roles",
                "two_step_aggregate subquery v1 must reference exactly one vertex role",
                location,
            )
        )
        return

    aggregate = AggregateOperationModel(op="aggregate", group_by=operation.group_by, measures=operation.measures)
    _validate_aggregate(aggregate, registry, vertex_bindings, location, issues)

    for carry_index, role in enumerate(operation.carry_roles):
        if role not in vertex_bindings:
            issues.append(
                _issue(
                    "unknown_carry_role",
                    f"subquery carry role not found: {role}",
                    f"{location}.carry_roles[{carry_index}]",
                )
            )


def _validate_filters(
    filters: list[FilterModel],
    registry: GraphSemanticRegistry,
    location: str,
    issues: list[RestrictedDslValidationIssue],
    *,
    vertex_bindings: dict[str, str] | None,
) -> None:
    for index, filter_item in enumerate(filters):
        item_location = f"{location}[{index}]"
        if (
            filter_item.target is not None
            and vertex_bindings is not None
            and filter_item.target not in vertex_bindings
        ):
            issues.append(_issue("unknown_filter_target", f"filter target not found: {filter_item.target}", f"{item_location}.target"))
            continue
        if (
            filter_item.target is not None
            and vertex_bindings is not None
            and vertex_bindings[filter_item.target] != filter_item.property.owner
        ):
            issues.append(
                _issue(
                    "filter_owner_mismatch",
                    f"filter target {filter_item.target} is {vertex_bindings[filter_item.target]}, not {filter_item.property.owner}",
                    f"{item_location}.property.owner",
                )
            )
            continue
        _validate_property(filter_item.property, registry, f"{item_location}.property", issues)


def _validate_projection(
    dsl: RestrictedQueryDslModel,
    registry: GraphSemanticRegistry,
    vertex_bindings: dict[str, str],
    source_outputs: dict[str, set[str]],
    issues: list[RestrictedDslValidationIssue],
) -> None:
    for index, item in enumerate(dsl.projection.items):
        location = f"projection.items[{index}]"
        if item.vertex_full:
            if item.target is None or item.target not in vertex_bindings:
                issues.append(
                    _issue(
                        "unknown_projection_target",
                        f"projection target not found: {item.target}",
                        f"{location}.target",
                    )
                )
            continue
        if item.property is not None:
            if item.target is not None and item.target not in vertex_bindings:
                issues.append(
                    _issue(
                        "unknown_projection_target",
                        f"projection target not found: {item.target}",
                        f"{location}.target",
                    )
                )
                continue
            if item.target is not None and vertex_bindings[item.target] != item.property.owner:
                issues.append(
                    _issue(
                        "projection_owner_mismatch",
                        f"projection target {item.target} is {vertex_bindings[item.target]}, not {item.property.owner}",
                        f"{location}.property.owner",
                    )
                )
            _validate_property(item.property, registry, f"{location}.property", issues)
        if item.source is not None:
            _validate_source_reference(item.source, source_outputs, location, issues)

    for order_index, sort_item in enumerate(dsl.order_by):
        _validate_source_reference(sort_item.source, source_outputs, f"order_by[{order_index}]", issues)

    for op_index, operation in enumerate(dsl.operations):
        if isinstance(operation, SortOperationModel):
            for sort_index, sort_item in enumerate(operation.by):
                _validate_source_reference(
                    sort_item.source,
                    source_outputs,
                    f"operations[{op_index}].by[{sort_index}]",
                    issues,
                )
        if isinstance(operation, FilterSubqueryOperationModel):
            output_aliases = source_outputs.get(operation.source)
            if output_aliases is None:
                issues.append(
                    _issue(
                        "unknown_subquery_source",
                        f"filter_subquery source not found: {operation.source}",
                        f"operations[{op_index}].source",
                    )
                )
            elif operation.predicate.property not in output_aliases:
                issues.append(
                    _issue(
                        "unknown_subquery_output",
                        f"subquery output not found: {operation.predicate.property}",
                        f"operations[{op_index}].predicate.property",
                    )
                )


def _validate_source_reference(
    source: str,
    source_outputs: dict[str, set[str]],
    location: str,
    issues: list[RestrictedDslValidationIssue],
) -> None:
    namespace, separator, name = source.partition(".")
    if not separator or not namespace or not name:
        issues.append(_issue("invalid_source_reference", f"source must use namespace.name form: {source}", f"{location}.source"))
        return
    if namespace not in source_outputs:
        issues.append(_issue("unknown_source_namespace", f"source namespace not found: {namespace}", f"{location}.source"))
        return
    if name not in source_outputs[namespace]:
        issues.append(_issue("unknown_source_output", f"source output not found: {source}", f"{location}.source"))


def _subquery_outputs(operations: list[Any]) -> dict[str, set[str]]:
    outputs: dict[str, set[str]] = {}
    for operation in operations:
        if isinstance(operation, SubqueryOperationModel):
            outputs[operation.bind_as] = {item.alias for item in operation.group_by} | {item.alias for item in operation.measures}
    return outputs


def _source_outputs(dsl: RestrictedQueryDslModel, registry: GraphSemanticRegistry) -> dict[str, set[str]]:
    outputs: dict[str, set[str]] = {"group": set(), "measure": set(), "metric": set()}
    for operation in dsl.operations:
        if isinstance(operation, UsePathPatternOperationModel):
            try:
                path_pattern = registry.get_path_pattern(operation.path_pattern_name)
            except RegistryLookupError:
                continue
            outputs[operation.bind_as] = _path_pattern_outputs(path_pattern.cypher)
        elif isinstance(operation, MetricAggregateOperationModel):
            outputs["group"].update(item.alias for item in operation.group_by)
            outputs["metric"].add(operation.metric_name)
        elif isinstance(operation, AggregateOperationModel):
            outputs["group"].update(item.alias for item in operation.group_by)
            outputs["measure"].update(item.alias for item in operation.measures)
        elif isinstance(operation, SubqueryOperationModel):
            outputs[operation.bind_as] = {item.alias for item in operation.group_by} | {item.alias for item in operation.measures}
    return outputs


def _path_pattern_outputs(cypher: str) -> set[str]:
    match = re.search(
        r"\bRETURN\b(?P<body>.*?)(?:\bORDER\s+BY\b|\bLIMIT\b|\bSKIP\b|$)",
        cypher,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return set()
    outputs: set[str] = set()
    for item in match.group("body").split(","):
        expression = item.strip()
        if not expression:
            continue
        alias_match = re.search(r"\bAS\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\s*$", expression, flags=re.IGNORECASE)
        if alias_match:
            outputs.add(alias_match.group("alias"))
            expression = expression[: alias_match.start()].strip()
        identifier_match = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expression)
        if identifier_match:
            outputs.add(expression)
            continue
        property_match = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.(?P<property>[A-Za-z_][A-Za-z0-9_]*)", expression)
        if property_match:
            outputs.add(property_match.group("property"))
    return outputs


def _validate_property(
    property_ref: PropertyReferenceModel,
    registry: GraphSemanticRegistry,
    location: str,
    issues: list[RestrictedDslValidationIssue],
) -> bool:
    return _lookup(
        lambda: registry.get_property(property_ref.owner, property_ref.name),
        "unknown_property",
        location,
        issues,
    )


def _validate_measure_function_type(
    function: str,
    property_ref: PropertyReferenceModel,
    registry: GraphSemanticRegistry,
    location: str,
    issues: list[RestrictedDslValidationIssue],
) -> None:
    if function == "count":
        return
    property_type = registry.property_type(property_ref.owner, property_ref.name)
    if function in {"sum", "avg"} and property_type not in NUMERIC_PROPERTY_TYPES:
        issues.append(
            _issue(
                "invalid_aggregate_property_type",
                f"{function} requires a numeric property, got {property_ref.owner}.{property_ref.name}:{property_type}",
                f"{location}.function",
            )
        )


def _lookup(
    callback: Any,
    code: str,
    location: str,
    issues: list[RestrictedDslValidationIssue],
) -> bool:
    try:
        callback()
    except RegistryLookupError as exc:
        issues.append(_issue(code, str(exc), location))
        return False
    return True


def _metric_aliases(metric: MetricDefinition) -> dict[str, str]:
    if not metric.pattern:
        return {}
    return dict(re.findall(r"\(([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\)", metric.pattern))


def _parameter_type_matches(value: Any, parameter_type: str) -> bool:
    if parameter_type == "string":
        return isinstance(value, str)
    if parameter_type in {"int", "integer"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if parameter_type in {"float", "number"}:
        return isinstance(value, int | float) and not isinstance(value, bool)
    if parameter_type == "bool":
        return isinstance(value, bool)
    return True


def _build_ast(dsl: RestrictedQueryDslModel, registry: GraphSemanticRegistry) -> RestrictedQueryAst:
    vertex_bindings = {
        alias: binding.vertex_name
        for alias, binding in dsl.bindings.items()
        if binding.vertex_name is not None
    }
    edge_bindings = {
        alias: binding.edge_name
        for alias, binding in dsl.bindings.items()
        if binding.edge_name is not None
    }
    operations = []
    for operation in dsl.operations:
        op = OperationType(operation.op)
        if isinstance(operation, TraverseEdgeOperationModel):
            operations.append(
                TraverseEdgeOperation(
                    op=op,
                    from_role=RoleReference(alias=operation.from_ref, vertex_name=vertex_bindings[operation.from_ref]),
                    edge_role=EdgeReference(alias=operation.edge, edge_name=edge_bindings[operation.edge]),
                    to_role=RoleReference(alias=operation.to, vertex_name=vertex_bindings[operation.to]),
                    direction=operation.direction,
                )
            )
        elif isinstance(operation, VariablePathOperationModel):
            operations.append(
                VariablePathOperation(
                    op=op,
                    bind_as=operation.bind_as,
                    start=RoleReference(alias=operation.start, vertex_name=vertex_bindings[operation.start]),
                    through=RoleReference(
                        alias=operation.through.vertex_ref,
                        vertex_name=vertex_bindings[operation.through.vertex_ref],
                    ),
                    through_filters=_build_filters(operation.through.filters, vertex_bindings),
                    allowed_edges=list(operation.allowed_edges),
                    min_hops=operation.min_hops,
                    max_hops=operation.max_hops,
                )
            )
        elif isinstance(operation, UsePathPatternOperationModel):
            operations.append(
                UsePathPatternOperation(
                    op=op,
                    path_pattern_name=operation.path_pattern_name,
                    bind_as=operation.bind_as,
                    parameters={key: _build_value_literal(value) for key, value in operation.parameters.items()},
                )
            )
        elif isinstance(operation, MetricAggregateOperationModel):
            metric = registry.get_metric(operation.metric_name)
            aliases = _metric_aliases(metric)
            operations.append(
                MetricAggregateOperation(
                    op=op,
                    metric_name=operation.metric_name,
                    group_by=[
                        MetricDimension(
                            alias=item.alias,
                            target_alias=item.target,
                            target_owner=aliases[item.target],
                            property=_build_property(item.property),
                        )
                        for item in operation.group_by
                    ],
                    filters=_build_filters(operation.filters, {alias: owner for alias, owner in aliases.items()}),
                )
            )
        elif isinstance(operation, AggregateOperationModel):
            operations.append(
                AggregateOperation(
                    op=op,
                    group_by=_build_dimensions(operation.group_by, vertex_bindings),
                    measures=_build_measures(operation.measures, vertex_bindings),
                )
            )
        elif isinstance(operation, SortOperationModel):
            operations.append(SortOperation(op=op, by=_build_sort_items(operation.by)))
        elif isinstance(operation, LimitOperationModel):
            operations.append(LimitOperation(op=op, value=operation.value))
        elif isinstance(operation, SubqueryOperationModel):
            operations.append(
                SubqueryOperation(
                    op=op,
                    bind_as=operation.bind_as,
                    query_shape=operation.query_shape,
                    group_by=_build_dimensions(operation.group_by, vertex_bindings),
                    measures=_build_measures(operation.measures, vertex_bindings),
                    carry_roles=[
                        RoleReference(alias=role, vertex_name=vertex_bindings[role])
                        for role in operation.carry_roles
                    ],
                    operations=_build_subquery_traverse_operations(
                        operation.operations,
                        vertex_bindings,
                        edge_bindings,
                    ),
                )
            )
        elif isinstance(operation, FilterSubqueryOperationModel):
            operations.append(
                FilterSubqueryOperation(
                    op=op,
                    source=operation.source,
                    predicate=Predicate(
                        property=operation.predicate.property,
                        operator=operation.predicate.operator,
                        value=_build_value_literal(operation.predicate.value),
                    ),
                )
            )

    return RestrictedQueryAst(
        schema_version=dsl.schema_version,
        query_id=dsl.query_id,
        query_shape=dsl.query_shape,
        source_question=dsl.source_question,
        operations=operations,
        projection=_build_projection(dsl, vertex_bindings),
        filters=_build_filters(dsl.filters, vertex_bindings),
        sort=_build_sort(dsl),
        limit=_ast_limit(dsl),
    )


def _build_projection(
    dsl: RestrictedQueryDslModel,
    vertex_bindings: dict[str, str],
) -> Projection:
    items: list[ProjectionItem] = []
    for item in dsl.projection.items:
        items.append(
            ProjectionItem(
                alias=item.alias,
                target=(
                    RoleReference(alias=item.target, vertex_name=vertex_bindings[item.target])
                    if item.target is not None and item.target in vertex_bindings
                    else None
                ),
                property=(
                    PropertyReference(owner=item.property.owner, name=item.property.name)
                    if item.property is not None
                    else None
                ),
                source=SourceReference.from_text(item.source) if item.source is not None else None,
                vertex_full=item.vertex_full,
            )
        )
    return Projection(items=items)


def _build_property(property_ref: PropertyReferenceModel) -> PropertyReference:
    return PropertyReference(owner=property_ref.owner, name=property_ref.name)


def _build_value_literal(value: Any) -> ValueLiteral:
    if isinstance(value, ValueModel):
        return ValueLiteral(
            raw=value.raw,
            normalized=value.normalized,
            resolver_match_type=value.resolver_match_type,
        )
    if isinstance(value, Mapping):
        value_model = ValueModel.model_validate(value)
        return ValueLiteral(
            raw=value_model.raw,
            normalized=value_model.normalized,
            resolver_match_type=value_model.resolver_match_type,
        )
    return ValueLiteral(
        raw=value,
        normalized=value,
        resolver_match_type=None,
    )


def _build_filters(filters: list[FilterModel], vertex_bindings: dict[str, str]) -> list[Filter]:
    built: list[Filter] = []
    for item in filters:
        built.append(
            Filter(
                target=(
                    RoleReference(alias=item.target, vertex_name=vertex_bindings[item.target])
                    if item.target is not None and item.target in vertex_bindings
                    else None
                ),
                property=_build_property(item.property),
                operator=item.operator,
                value=_build_value_literal(item.value),
            )
        )
    return built


def _build_dimensions(dimensions: list[DimensionModel], vertex_bindings: dict[str, str]) -> list[Dimension]:
    return [
        Dimension(
            alias=item.alias,
            target=RoleReference(alias=item.target, vertex_name=vertex_bindings[item.target]),
            property=_build_property(item.property),
        )
        for item in dimensions
    ]


def _build_subquery_traverse_operations(
    operations: list[dict[str, Any]],
    vertex_bindings: dict[str, str],
    edge_bindings: dict[str, str],
) -> list[TraverseEdgeOperation]:
    built: list[TraverseEdgeOperation] = []
    for raw_operation in operations:
        operation = TraverseEdgeOperationModel.model_validate(raw_operation)
        built.append(
            TraverseEdgeOperation(
                op=OperationType.TRAVERSE_EDGE,
                from_role=RoleReference(
                    alias=operation.from_ref,
                    vertex_name=vertex_bindings[operation.from_ref],
                ),
                edge_role=EdgeReference(
                    alias=operation.edge,
                    edge_name=edge_bindings[operation.edge],
                ),
                to_role=RoleReference(
                    alias=operation.to,
                    vertex_name=vertex_bindings[operation.to],
                ),
                direction=operation.direction,
            )
        )
    return built


def _build_measures(measures: list[MeasureModel], vertex_bindings: dict[str, str]) -> list[Measure]:
    return [
        Measure(
            alias=item.alias,
            function=item.function,
            target=RoleReference(alias=item.target, vertex_name=vertex_bindings[item.target]),
            property=_build_property(item.property),
        )
        for item in measures
    ]


def _build_sort_items(items: list[Any]) -> list[SortItem]:
    return [SortItem(source=SourceReference.from_text(item.source), direction=item.direction) for item in items]


def _build_sort(dsl: RestrictedQueryDslModel) -> list[SortItem]:
    sort_items: list[SortItem] = _build_sort_items(dsl.order_by)
    for operation in dsl.operations:
        if isinstance(operation, SortOperationModel):
            sort_items.extend(_build_sort_items(operation.by))
    return sort_items


def _ast_limit(dsl: RestrictedQueryDslModel) -> int | None:
    for operation in reversed(dsl.operations):
        if isinstance(operation, LimitOperationModel):
            return operation.value
    return dsl.limit


def _issue(code: str, message: str, location: str) -> RestrictedDslValidationIssue:
    return RestrictedDslValidationIssue(code=code, message=message, location=location)
