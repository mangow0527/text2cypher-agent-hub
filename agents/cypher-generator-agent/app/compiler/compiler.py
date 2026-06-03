from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import re

from services.cypher_generator_agent.app.cypher_validation import (
    CypherSelfValidationResult,
    CypherSelfValidator,
)
from services.cypher_generator_agent.app.dsl.ast import (
    AggregateOperation,
    Dimension,
    Filter,
    FilterSubqueryOperation,
    LimitOperation,
    Measure,
    MetricAggregateOperation,
    Projection,
    ProjectionItem,
    RestrictedQueryAst,
    RoleReference,
    SortOperation,
    SubqueryOperation,
    TraverseEdgeOperation,
    UsePathPatternOperation,
    ValueLiteral,
    VariablePathOperation,
)
from services.cypher_generator_agent.app.dsl.models import QueryShape
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry

from .literals import inline_cypher_parameters
from .projection import projection_aliases, projection_item_alias
from .projection import extract_parameter_names, is_cypher_identifier
from .templates import get_path_pattern_template


CYPHER_COMPILATION_RESULT_SCHEMA_VERSION = "cypher_compilation_result_v1"
SUPPORTED_QUERY_SHAPES = {
    QueryShape.VERTEX_LOOKUP,
    QueryShape.SINGLE_HOP_TRAVERSAL,
    QueryShape.NAMED_PATH_PATTERN,
    QueryShape.VARIABLE_PATH_TRAVERSAL,
    QueryShape.METRIC_AGGREGATE,
    QueryShape.AD_HOC_AGGREGATE,
    QueryShape.TOP_N,
    QueryShape.TWO_STEP_AGGREGATE,
}
ROLE_VARIABLES = {
    "Service": "svc",
    "Tunnel": "tun",
    "NetworkElement": "ne",
    "Port": "port",
}
OPERATOR_CYPHER = {
    "eq": "=",
    "neq": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "in": "IN",
    "contains": "CONTAINS",
}
UNRESOLVED_MATCH_TYPES = {"unresolved"}
RESOLVED_MATCH_TYPES = {
    "exact",
    "id_exact",
    "manual_fixture",
    "synonym",
    "text_exact",
    "value_index_exact",
    "literal_passthrough",
    "value_model",
    "value_synonym",
}


class CypherCompilerError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        validation_result: CypherSelfValidationResult | None = None,
    ) -> None:
        self.validation_result = validation_result
        super().__init__(message)


@dataclass(frozen=True)
class CypherCompilationDraft:
    schema_version: str
    cypher_template: str
    cypher_executable: str
    parameters: dict[str, object]
    expected_return_aliases: list[str]
    parameter_sources: list[dict[str, object]] = field(default_factory=list)

    @property
    def cypher(self) -> str:
        return self.cypher_template


@dataclass(frozen=True)
class CypherCompilationResult:
    schema_version: str
    cypher_template: str
    cypher: str
    cypher_executable: str
    parameters: dict[str, object]
    parameter_sources: list[dict[str, object]]
    validation_result: CypherSelfValidationResult


class CypherCompiler:
    def __init__(
        self,
        registry: GraphSemanticRegistry,
        *,
        _path_pattern_template_overrides_for_tests: Mapping[str, str] | None = None,
    ) -> None:
        self.registry = registry
        self._path_pattern_template_overrides_for_tests = _path_pattern_template_overrides_for_tests
        self.validator = CypherSelfValidator(registry)

    def compile_draft(self, ast: RestrictedQueryAst) -> CypherCompilationDraft:
        if ast.query_shape not in SUPPORTED_QUERY_SHAPES:
            raise CypherCompilerError(f"unsupported query_shape: {ast.query_shape.value}")

        if ast.query_shape is QueryShape.VERTEX_LOOKUP:
            cypher, parameters = self._compile_vertex_lookup(ast)
        elif ast.query_shape is QueryShape.SINGLE_HOP_TRAVERSAL:
            cypher, parameters = self._compile_single_hop(ast)
        elif ast.query_shape is QueryShape.NAMED_PATH_PATTERN:
            cypher, parameters = self._compile_named_path_pattern(ast)
        elif ast.query_shape is QueryShape.VARIABLE_PATH_TRAVERSAL:
            cypher, parameters = self._compile_variable_path(ast)
        elif ast.query_shape is QueryShape.METRIC_AGGREGATE:
            cypher, parameters = self._compile_metric_aggregate(ast)
        elif ast.query_shape is QueryShape.AD_HOC_AGGREGATE:
            cypher, parameters = self._compile_ad_hoc_aggregate(ast)
        elif ast.query_shape is QueryShape.TOP_N:
            cypher, parameters = self._compile_top_n(ast)
        elif ast.query_shape is QueryShape.TWO_STEP_AGGREGATE:
            cypher, parameters = self._compile_two_step_aggregate(ast)
        else:
            raise CypherCompilerError(f"unsupported query_shape: {ast.query_shape.value}")

        return CypherCompilationDraft(
            schema_version=CYPHER_COMPILATION_RESULT_SCHEMA_VERSION,
            cypher_template=cypher,
            cypher_executable=inline_cypher_parameters(cypher, parameters.values),
            parameters=parameters.values,
            expected_return_aliases=projection_aliases(ast.projection),
            parameter_sources=parameters.sources,
        )

    def compile(self, ast: RestrictedQueryAst) -> CypherCompilationResult:
        draft = self.compile_draft(ast)
        validation_result = self.validator.validate_generated_query(
            draft.cypher_executable,
            expected_return_aliases=draft.expected_return_aliases,
        )
        if not validation_result.valid:
            raise CypherCompilerError("compiled Cypher self-validation failed", validation_result=validation_result)
        return CypherCompilationResult(
            schema_version=CYPHER_COMPILATION_RESULT_SCHEMA_VERSION,
            cypher_template=draft.cypher_template,
            cypher=draft.cypher_executable,
            cypher_executable=draft.cypher_executable,
            parameters=draft.parameters,
            parameter_sources=draft.parameter_sources,
            validation_result=validation_result,
        )

    def _compile_vertex_lookup(self, ast: RestrictedQueryAst) -> tuple[str, "_CompiledParameters"]:
        role = _single_vertex_role(ast)
        variable = _variable_for_owner(role.vertex_name, used=set())
        role_variables = {role.alias: variable}
        parameters = _ParameterBuilder()

        clauses = [f"MATCH ({variable}:{role.vertex_name})"]
        where = _compile_filters(ast.filters, role_variables, parameters)
        if where:
            clauses.append(f"WHERE {' AND '.join(where)}")
        clauses.append(_compile_return(ast.projection, role_variables))
        _append_order_limit(clauses, ast, {})
        return "\n".join(clauses), parameters.result

    def _compile_metric_aggregate(self, ast: RestrictedQueryAst) -> tuple[str, "_CompiledParameters"]:
        operation = _single_operation(ast, MetricAggregateOperation)
        metric = self.registry.get_metric(operation.metric_name)
        if not metric.pattern or not metric.expression:
            raise CypherCompilerError("metric_aggregate compiler requires pattern + expression metric")

        aliases = _metric_aliases(metric.pattern)
        parameters = _ParameterBuilder()
        role_variables = {
            dimension.target_alias: dimension.target_alias for dimension in operation.group_by
        }
        for filter_item in operation.filters:
            if filter_item.target is None:
                raise CypherCompilerError("metric_aggregate filters require metric target alias")
            role_variables[filter_item.target.alias] = filter_item.target.alias
        for alias in aliases:
            role_variables.setdefault(alias, alias)

        source_expressions: dict[tuple[str, str], str] = {
            ("metric", operation.metric_name): metric.expression,
        }
        for dimension in operation.group_by:
            source_expressions[("group", dimension.alias)] = (
                f"{dimension.target_alias}.{dimension.property.name}"
            )

        clauses = [f"MATCH {metric.pattern}"]
        where = _compile_filters(operation.filters, role_variables, parameters)
        if where:
            clauses.append(f"WHERE {' AND '.join(where)}")
        clauses.append(_compile_source_return(ast.projection, source_expressions))
        _append_order_limit(clauses, ast, source_expressions)
        return "\n".join(clauses), parameters.result

    def _compile_ad_hoc_aggregate(self, ast: RestrictedQueryAst) -> tuple[str, "_CompiledParameters"]:
        operation = _single_operation(ast, AggregateOperation)
        roles = _aggregate_roles(operation, ast.filters)
        role_variables = _role_variables(roles.values())
        parameters = _ParameterBuilder()
        source_expressions = _aggregate_source_expressions(
            operation.group_by,
            operation.measures,
            role_variables,
        )

        clauses = [_compile_aggregate_match(roles, role_variables)]
        where = _compile_filters(ast.filters, role_variables, parameters)
        if where:
            clauses.append(f"WHERE {' AND '.join(where)}")
        clauses.append(_compile_source_return(ast.projection, source_expressions))
        _append_order_limit(clauses, ast, source_expressions)
        return "\n".join(clauses), parameters.result

    def _compile_top_n(self, ast: RestrictedQueryAst) -> tuple[str, "_CompiledParameters"]:
        if ast.operations and isinstance(ast.operations[0], TraverseEdgeOperation):
            return self._compile_path_top_n(ast)
        primary_operation = ast.operations[0] if ast.operations else None
        if isinstance(primary_operation, MetricAggregateOperation):
            return self._compile_metric_aggregate(ast)
        if isinstance(primary_operation, AggregateOperation):
            return self._compile_ad_hoc_aggregate(ast)
        raise CypherCompilerError("top_n requires aggregate or metric_aggregate operation")

    def _compile_path_top_n(self, ast: RestrictedQueryAst) -> tuple[str, "_CompiledParameters"]:
        traverse_operations: list[TraverseEdgeOperation] = []
        aggregate: AggregateOperation | None = None
        for operation in ast.operations:
            if isinstance(operation, TraverseEdgeOperation) and aggregate is None:
                traverse_operations.append(operation)
                continue
            if isinstance(operation, AggregateOperation) and aggregate is None:
                aggregate = operation
                continue
            if isinstance(operation, SortOperation | LimitOperation):
                continue
            raise CypherCompilerError("path top_n requires traverse_edge chain, aggregate, sort, and limit")
        if not traverse_operations or aggregate is None:
            raise CypherCompilerError("path top_n requires traverse_edge chain and aggregate operation")

        role_variables = _traverse_chain_role_variables(traverse_operations)
        clauses = [_compile_traverse_chain_match(traverse_operations, role_variables)]
        parameters = _ParameterBuilder()
        where = _compile_filters(ast.filters, role_variables, parameters)
        if where:
            clauses.append(f"WHERE {' AND '.join(where)}")

        source_expressions = _aggregate_source_expressions(
            aggregate.group_by,
            aggregate.measures,
            role_variables,
        )
        clauses.append(_compile_source_return(ast.projection, source_expressions))
        _append_order_limit(clauses, ast, source_expressions)
        return "\n".join(clauses), parameters.result

    def _compile_two_step_aggregate(self, ast: RestrictedQueryAst) -> tuple[str, "_CompiledParameters"]:
        subquery = _single_operation(ast, SubqueryOperation)
        if subquery.operations:
            return self._compile_two_step_path_aggregate(ast, subquery)
        roles = _aggregate_roles_from_parts(subquery.group_by, subquery.measures, [])
        role_variables = _role_variables(roles.values())
        parameters = _ParameterBuilder()
        source_expressions = _aggregate_source_expressions(
            subquery.group_by,
            subquery.measures,
            role_variables,
        )

        clauses = [_compile_aggregate_match(roles, role_variables)]
        clauses.append(_compile_with(subquery.group_by, subquery.measures, source_expressions))

        filter_where = _compile_filter_subqueries(ast, subquery, parameters)
        if filter_where:
            clauses.append(f"WHERE {' AND '.join(filter_where)}")

        subquery_sources = {
            (subquery.bind_as, dimension.alias): dimension.alias for dimension in subquery.group_by
        }
        subquery_sources.update(
            {
                (subquery.bind_as, measure.alias): measure.alias
                for measure in subquery.measures
            }
        )
        clauses.append(_compile_source_return(ast.projection, subquery_sources))
        _append_order_limit(clauses, ast, subquery_sources)
        return "\n".join(clauses), parameters.result

    def _compile_two_step_path_aggregate(
        self,
        ast: RestrictedQueryAst,
        subquery: SubqueryOperation,
    ) -> tuple[str, "_CompiledParameters"]:
        second_traverses: list[TraverseEdgeOperation] = []
        second_aggregate: AggregateOperation | None = None
        seen_subquery = False
        for operation in ast.operations:
            if operation is subquery:
                seen_subquery = True
                continue
            if not seen_subquery:
                continue
            if isinstance(operation, TraverseEdgeOperation) and second_aggregate is None:
                second_traverses.append(operation)
                continue
            if isinstance(operation, AggregateOperation) and second_aggregate is None:
                second_aggregate = operation
                continue
            if isinstance(operation, SortOperation | LimitOperation):
                continue
            raise CypherCompilerError("two_step path aggregate supports subquery, traverse chain, aggregate, sort, limit")
        if not second_traverses or second_aggregate is None:
            raise CypherCompilerError("two_step path aggregate requires second traverse chain and aggregate")

        role_variables = _traverse_chain_role_variables([*subquery.operations, *second_traverses])
        parameters = _ParameterBuilder()

        first_source_expressions = _aggregate_source_expressions(
            subquery.group_by,
            subquery.measures,
            role_variables,
        )
        clauses = [_compile_traverse_chain_match(subquery.operations, role_variables)]
        first_where = _compile_filters(ast.filters, role_variables, parameters)
        if first_where:
            clauses.append(f"WHERE {' AND '.join(first_where)}")
        clauses.append(
            _compile_path_aggregate_with(
                subquery,
                role_variables,
                first_source_expressions,
            )
        )

        clauses.append(_compile_traverse_chain_match(second_traverses, role_variables))
        second_source_expressions = _aggregate_source_expressions(
            second_aggregate.group_by,
            second_aggregate.measures,
            role_variables,
        )
        source_expressions = {
            **{
                (subquery.bind_as, dimension.alias): dimension.alias
                for dimension in subquery.group_by
            },
            **{
                (subquery.bind_as, measure.alias): measure.alias
                for measure in subquery.measures
            },
            **second_source_expressions,
        }
        clauses.append(_compile_mixed_return(ast.projection, role_variables, source_expressions))
        _append_order_limit(clauses, ast, source_expressions)
        return "\n".join(clauses), parameters.result

    def _compile_single_hop(self, ast: RestrictedQueryAst) -> tuple[str, "_CompiledParameters"]:
        operations = [operation for operation in ast.operations if isinstance(operation, TraverseEdgeOperation)]
        if not operations:
            raise CypherCompilerError("single_hop_traversal requires at least one traverse_edge operation")
        role_variables = _traverse_chain_role_variables(operations)
        parameters = _ParameterBuilder()
        clauses = [_compile_traverse_chain_match(operations, role_variables)]
        where = _compile_filters(ast.filters, role_variables, parameters)
        if where:
            clauses.append(f"WHERE {' AND '.join(where)}")
        clauses.append(_compile_return(ast.projection, role_variables))
        return "\n".join(clauses), parameters.result

    def _compile_named_path_pattern(self, ast: RestrictedQueryAst) -> tuple[str, "_CompiledParameters"]:
        operation = _single_operation(ast, UsePathPatternOperation)
        cypher = _get_path_pattern_template_for_compile(
            self.registry,
            operation.path_pattern_name,
            self._path_pattern_template_overrides_for_tests,
        )
        parameters = _compiled_path_pattern_parameters(operation.parameters)
        _validate_template_parameters(cypher, parameters.values)
        return cypher, parameters

    def _compile_variable_path(self, ast: RestrictedQueryAst) -> tuple[str, "_CompiledParameters"]:
        operation = _single_operation(ast, VariablePathOperation)
        if len(operation.allowed_edges) != 1:
            raise CypherCompilerError("variable_path compiler MVP requires exactly one allowed edge")
        if operation.max_hops > 8:
            raise CypherCompilerError("variable_path max_hops must be <= 8")

        used: set[str] = set()
        start_var = _variable_for_owner(operation.start.vertex_name, used=used)
        through_var = _variable_for_owner(operation.through.vertex_name, used=used)
        role_variables = {
            operation.start.alias: start_var,
            operation.through.alias: through_var,
        }
        parameters = _ParameterBuilder()
        edge_name = operation.allowed_edges[0]

        clauses = [
            (
                f"MATCH {operation.bind_as} = ({start_var}:{operation.start.vertex_name})"
                f"-[:{edge_name}*{operation.min_hops}..{operation.max_hops}]->"
                f"({through_var}:{operation.through.vertex_name})"
            )
        ]
        where = _compile_filters([*operation.through_filters, *ast.filters], role_variables, parameters)
        if where:
            clauses.append(f"WHERE {' AND '.join(where)}")
        clauses.append(_compile_return(ast.projection, role_variables))
        return "\n".join(clauses), parameters.result


@dataclass(frozen=True)
class _CompiledParameters:
    values: dict[str, object]
    sources: list[dict[str, object]]


class _ParameterBuilder:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.sources: list[dict[str, object]] = []

    @property
    def result(self) -> _CompiledParameters:
        return _CompiledParameters(values=dict(self.values), sources=list(self.sources))

    def add(
        self,
        base_name: str,
        literal: ValueLiteral,
        *,
        source: str,
    ) -> str:
        value = _validated_literal_value(literal)
        name = _sanitize_identifier(base_name)
        if name not in self.values:
            self.values[name] = value
            self.sources.append(_parameter_source(name, value, literal, source=source))
            return name

        index = 2
        while f"{name}_{index}" in self.values:
            index += 1
        unique_name = f"{name}_{index}"
        self.values[unique_name] = value
        self.sources.append(_parameter_source(unique_name, value, literal, source=source))
        return unique_name


def _compiled_path_pattern_parameters(parameters: Mapping[str, ValueLiteral]) -> _CompiledParameters:
    values: dict[str, object] = {}
    sources: list[dict[str, object]] = []
    for name, literal in parameters.items():
        value = _validated_literal_value(literal)
        values[name] = value
        sources.append(_parameter_source(name, value, literal, source="path_pattern_parameter"))
    return _CompiledParameters(values=values, sources=sources)


def _validated_literal_value(literal: ValueLiteral) -> object:
    if literal.resolver_match_type in UNRESOLVED_MATCH_TYPES:
        raise CypherCompilerError("unresolved literal cannot be inlined into executable Cypher")
    if not _literal_has_resolution_evidence(literal):
        raise CypherCompilerError("literal requires resolution evidence before it can be inlined into executable Cypher")
    return literal.effective_value


def _parameter_source(
    name: str,
    value: object,
    literal: ValueLiteral,
    *,
    source: str,
) -> dict[str, object]:
    return {
        "name": name,
        "value": value,
        "source": source,
        "resolver_match_type": literal.resolver_match_type,
        "resolved": _literal_has_resolution_evidence(literal),
    }


def _literal_has_resolution_evidence(literal: ValueLiteral) -> bool:
    if literal.resolver_match_type in UNRESOLVED_MATCH_TYPES:
        return False
    if literal.normalized is not None:
        return True
    return literal.resolver_match_type in RESOLVED_MATCH_TYPES


def compile_restricted_query_ast(
    ast: RestrictedQueryAst,
    registry: GraphSemanticRegistry,
) -> CypherCompilationResult:
    return CypherCompiler(registry).compile(ast)


def _single_operation(ast: RestrictedQueryAst, expected_type):
    matches = [operation for operation in ast.operations if isinstance(operation, expected_type)]
    if len(matches) != 1:
        raise CypherCompilerError(f"expected exactly one {expected_type.__name__} operation")
    return matches[0]


def _single_vertex_role(ast: RestrictedQueryAst):
    roles = []
    for filter_item in ast.filters:
        if filter_item.target is not None:
            roles.append(filter_item.target)
    for item in ast.projection.items:
        if item.target is not None:
            roles.append(item.target)

    unique: dict[str, object] = {}
    for role in roles:
        unique[role.alias] = role
    if len(unique) != 1:
        raise CypherCompilerError("vertex_lookup requires exactly one vertex role")
    return next(iter(unique.values()))


def _compile_filters(
    filters: list[Filter],
    role_variables: Mapping[str, str],
    parameters: _ParameterBuilder,
) -> list[str]:
    compiled: list[str] = []
    for filter_item in filters:
        if filter_item.target is None:
            raise CypherCompilerError("filter target is required for compiler MVP")
        variable = role_variables[filter_item.target.alias]
        if filter_item.operator == "is_not_null":
            compiled.append(f"{variable}.{filter_item.property.name} IS NOT NULL")
            continue
        operator = OPERATOR_CYPHER.get(filter_item.operator)
        if operator is None:
            raise CypherCompilerError(f"unsupported filter operator: {filter_item.operator}")
        parameter_name = parameters.add(filter_item.property.name, filter_item.value, source="dsl_filter")
        compiled.append(f"{variable}.{filter_item.property.name} {operator} ${parameter_name}")
    return compiled


def _compile_return(projection: Projection, role_variables: Mapping[str, str]) -> str:
    aliases = projection_aliases(projection)
    items = [
        _compile_projection_item(item, role_variables, alias=alias)
        for item, alias in zip(projection.items, aliases, strict=True)
    ]
    return f"RETURN {', '.join(items)}"


def _compile_source_return(
    projection: Projection,
    source_expressions: Mapping[tuple[str, str], str],
) -> str:
    aliases = projection_aliases(projection)
    items = [
        _compile_source_projection_item(item, source_expressions, alias=alias)
        for item, alias in zip(projection.items, aliases, strict=True)
    ]
    return f"RETURN {', '.join(items)}"


def _compile_source_projection_item(
    item: ProjectionItem,
    source_expressions: Mapping[tuple[str, str], str],
    *,
    alias: str | None = None,
) -> str:
    if item.source is None:
        raise CypherCompilerError("source projection is required for aggregate compiler MVP")
    expression = source_expressions.get((item.source.namespace, item.source.name))
    if expression is None:
        raise CypherCompilerError(f"unknown aggregate projection source: {item.source.raw}")
    alias = alias or projection_item_alias(item)
    if not is_cypher_identifier(alias):
        raise CypherCompilerError(f"invalid projection alias: {alias}")
    return f"{expression} AS {alias}"


def _compile_mixed_return(
    projection: Projection,
    role_variables: Mapping[str, str],
    source_expressions: Mapping[tuple[str, str], str],
) -> str:
    aliases = projection_aliases(projection)
    items = [
        _compile_source_projection_item(item, source_expressions, alias=alias)
        if item.source is not None
        else _compile_projection_item(item, role_variables, alias=alias)
        for item, alias in zip(projection.items, aliases, strict=True)
    ]
    return f"RETURN {', '.join(items)}"


def _append_order_limit(
    clauses: list[str],
    ast: RestrictedQueryAst,
    source_expressions: Mapping[tuple[str, str], str],
) -> None:
    if ast.sort:
        source_aliases = _projection_source_aliases(ast.projection)
        order_items = []
        for sort_item in ast.sort:
            key = (sort_item.source.namespace, sort_item.source.name)
            expression = source_aliases.get(key) or source_expressions.get(key)
            if expression is None:
                raise CypherCompilerError(f"unknown sort source: {sort_item.source.raw}")
            order_items.append(f"{expression} {sort_item.direction.upper()}")
        clauses.append(f"ORDER BY {', '.join(order_items)}")
    if ast.limit is not None:
        clauses.append(f"LIMIT {ast.limit}")


def _projection_source_aliases(projection: Projection) -> dict[tuple[str, str], str]:
    aliases: dict[tuple[str, str], str] = {}
    projection_alias_plan = projection_aliases(projection)
    for item, alias in zip(projection.items, projection_alias_plan, strict=True):
        if item.source is None:
            continue
        if not is_cypher_identifier(alias):
            raise CypherCompilerError(f"invalid projection alias: {alias}")
        aliases[(item.source.namespace, item.source.name)] = alias
    return aliases


def _compile_projection_item(
    item: ProjectionItem,
    role_variables: Mapping[str, str],
    *,
    alias: str | None = None,
) -> str:
    if item.vertex_full:
        if item.target is None:
            raise CypherCompilerError("vertex_full projection requires target")
        variable = role_variables[item.target.alias]
        alias = alias or projection_item_alias(item)
        if not is_cypher_identifier(alias):
            raise CypherCompilerError(f"invalid projection alias: {alias}")
        return f"{variable} AS {alias}"
    if item.target is None or item.property is None:
        raise CypherCompilerError("target/property projection is required for generated query compiler MVP")
    variable = role_variables[item.target.alias]
    alias = alias or projection_item_alias(item)
    if not is_cypher_identifier(alias):
        raise CypherCompilerError(f"invalid projection alias: {alias}")
    return f"{variable}.{item.property.name} AS {alias}"


def _compile_with(
    group_by: list[Dimension],
    measures: list[Measure],
    source_expressions: Mapping[tuple[str, str], str],
) -> str:
    items: list[str] = []
    for dimension in group_by:
        items.append(f"{source_expressions[('group', dimension.alias)]} AS {dimension.alias}")
    for measure in measures:
        items.append(f"{source_expressions[('measure', measure.alias)]} AS {measure.alias}")
    return f"WITH {', '.join(items)}"


def _compile_path_aggregate_with(
    subquery: SubqueryOperation,
    role_variables: Mapping[str, str],
    source_expressions: Mapping[tuple[str, str], str],
) -> str:
    items = [role_variables[role.alias] for role in subquery.carry_roles]
    for dimension in subquery.group_by:
        items.append(f"{source_expressions[('group', dimension.alias)]} AS {dimension.alias}")
    for measure in subquery.measures:
        items.append(f"{source_expressions[('measure', measure.alias)]} AS {measure.alias}")
    if not items:
        raise CypherCompilerError("two_step path aggregate WITH requires carry role or aggregate output")
    return f"WITH {', '.join(items)}"


def _compile_filter_subqueries(
    ast: RestrictedQueryAst,
    subquery: SubqueryOperation,
    parameters: _ParameterBuilder,
) -> list[str]:
    output_aliases = {dimension.alias for dimension in subquery.group_by} | {
        measure.alias for measure in subquery.measures
    }
    compiled: list[str] = []
    for operation in ast.operations:
        if not isinstance(operation, FilterSubqueryOperation):
            continue
        if operation.source != subquery.bind_as:
            raise CypherCompilerError(f"filter_subquery source not found: {operation.source}")
        if operation.predicate.property not in output_aliases:
            raise CypherCompilerError(f"filter_subquery output not found: {operation.predicate.property}")
        operator = OPERATOR_CYPHER.get(operation.predicate.operator)
        if operator is None:
            raise CypherCompilerError(f"unsupported filter_subquery operator: {operation.predicate.operator}")
        parameter_name = parameters.add(
            operation.predicate.property,
            operation.predicate.value,
            source="filter_subquery",
        )
        compiled.append(f"{operation.predicate.property} {operator} ${parameter_name}")
    return compiled


def _aggregate_roles(
    operation: AggregateOperation,
    filters: list[Filter],
) -> dict[str, object]:
    return _aggregate_roles_from_parts(operation.group_by, operation.measures, filters)


def _aggregate_roles_from_parts(
    group_by: list[Dimension],
    measures: list[Measure],
    filters: list[Filter],
) -> dict[str, RoleReference]:
    roles: dict[str, object] = {}
    for dimension in group_by:
        roles[dimension.target.alias] = dimension.target
    for measure in measures:
        roles[measure.target.alias] = measure.target
    for filter_item in filters:
        if filter_item.target is not None:
            roles[filter_item.target.alias] = filter_item.target
    return roles


def _role_variables(roles: object) -> dict[str, str]:
    used: set[str] = set()
    return {
        role.alias: _variable_for_owner(role.vertex_name, used=used)
        for role in roles
    }


def _traverse_chain_role_variables(operations: list[TraverseEdgeOperation]) -> dict[str, str]:
    used: set[str] = set()
    role_variables: dict[str, str] = {}
    for operation in operations:
        if operation.from_role.alias not in role_variables:
            role_variables[operation.from_role.alias] = _variable_for_owner(
                operation.from_role.vertex_name,
                used=used,
            )
        if operation.to_role.alias not in role_variables:
            role_variables[operation.to_role.alias] = _variable_for_owner(
                operation.to_role.vertex_name,
                used=used,
            )
    return role_variables


def _compile_traverse_chain_match(
    operations: list[TraverseEdgeOperation],
    role_variables: Mapping[str, str],
) -> str:
    first = operations[0]
    parts = [f"({role_variables[first.from_role.alias]}:{first.from_role.vertex_name})"]
    current_alias = first.from_role.alias
    for operation in operations:
        if operation.from_role.alias != current_alias:
            raise CypherCompilerError(
                "traverse_edge operations must form one contiguous chain"
            )
        to_var = role_variables[operation.to_role.alias]
        to_node = f"({to_var}:{operation.to_role.vertex_name})"
        if operation.direction == "forward":
            parts.append(f"-[:{operation.edge_role.edge_name}]->{to_node}")
        elif operation.direction == "backward":
            parts.append(f"<-[:{operation.edge_role.edge_name}]-{to_node}")
        else:
            raise CypherCompilerError(f"unsupported traversal direction: {operation.direction}")
        current_alias = operation.to_role.alias
    return f"MATCH {''.join(parts)}"


def _aggregate_source_expressions(
    group_by: list[Dimension],
    measures: list[Measure],
    role_variables: Mapping[str, str],
) -> dict[tuple[str, str], str]:
    source_expressions: dict[tuple[str, str], str] = {}
    for dimension in group_by:
        source_expressions[("group", dimension.alias)] = (
            f"{role_variables[dimension.target.alias]}.{dimension.property.name}"
        )
    for measure in measures:
        variable = role_variables[measure.target.alias]
        source_expressions[("measure", measure.alias)] = (
            f"{measure.function}({variable}.{measure.property.name})"
        )
    return source_expressions


def _compile_aggregate_match(
    roles: Mapping[str, RoleReference],
    role_variables: Mapping[str, str],
) -> str:
    role_values = list(roles.values())
    if len(role_values) == 1:
        role = role_values[0]
        return f"MATCH ({role_variables[role.alias]}:{role.vertex_name})"
    raise CypherCompilerError(
        "ad_hoc aggregate compiler requires exactly one vertex role; use a metric for edge-connected aggregates"
    )


def _variable_for_owner(owner: str, *, used: set[str]) -> str:
    base = ROLE_VARIABLES.get(owner, owner[:1].lower() or "v")
    variable = base
    index = 2
    while variable in used:
        variable = f"{base}{index}"
        index += 1
    used.add(variable)
    return variable


def _sanitize_identifier(value: str) -> str:
    sanitized = "".join(char if char.isalnum() or char == "_" else "_" for char in value)
    if not sanitized:
        return "param"
    if sanitized[0].isdigit():
        return f"param_{sanitized}"
    return sanitized


def _metric_aliases(pattern: str) -> dict[str, str]:
    return dict(
        match.groups()
        for match in re.finditer(
            r"\(([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\)",
            pattern,
        )
    )


def _get_path_pattern_template_for_compile(
    registry: GraphSemanticRegistry,
    path_pattern_name: str,
    test_overrides: Mapping[str, str] | None,
) -> str:
    if test_overrides and path_pattern_name in test_overrides:
        return test_overrides[path_pattern_name].strip()
    return get_path_pattern_template(registry, path_pattern_name)


def _validate_template_parameters(cypher: str, parameters: Mapping[str, object]) -> None:
    template_parameters = extract_parameter_names(cypher)
    provided_parameters = set(parameters)
    if template_parameters != provided_parameters:
        raise CypherCompilerError(
            "path_pattern template parameters must match DSL parameters: "
            f"template={sorted(template_parameters)}, provided={sorted(provided_parameters)}"
        )
