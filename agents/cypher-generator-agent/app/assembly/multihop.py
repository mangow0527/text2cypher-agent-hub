from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence

from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry, RegistryLookupError
from services.cypher_generator_agent.app.semantic_model.model import EdgeDefinition, VertexDefinition

from .direction import DirectionMapper, DirectionStatus


@dataclass(frozen=True)
class MultihopAssemblyResult:
    success: bool
    dsl: dict[str, Any] | None = None
    fallback_reason: str | None = None


class MultihopAssembler:
    def __init__(self, registry: GraphSemanticRegistry) -> None:
        self.registry = registry

    def assemble(
        self,
        shape: str,
        candidates: Sequence[Any],
        structural_requirements: Mapping[str, Any],
        literals: Sequence[Mapping[str, Any]] | None = None,
    ) -> MultihopAssemblyResult:
        requirements = _requirements_dict(structural_requirements)
        normalized_shape = _shape_family(shape)

        if normalized_shape not in {"F4", "F5", "F6"}:
            return _fallback("unsupported_multihop_shape")

        path = self._resolve_edge_chain(candidates, requirements)
        if isinstance(path, MultihopAssemblyResult):
            return path

        if normalized_shape == "F4":
            return self._assemble_f4(path, candidates, requirements)
        if normalized_shape == "F5":
            return self._assemble_f5(path, candidates, requirements, literals or [])
        return self._assemble_f6(path, candidates, requirements)

    def _assemble_f4(
        self,
        path: "_PathPlan",
        candidates: Sequence[Any],
        requirements: Mapping[str, Any],
    ) -> MultihopAssemblyResult:
        projection = self._projection_items(candidates, requirements, path.role_by_owner)
        if isinstance(projection, MultihopAssemblyResult):
            return projection
        return _success(_single_hop_dsl("multihop-f4", path, filters=[], projection=projection))

    def _assemble_f5(
        self,
        path: "_PathPlan",
        candidates: Sequence[Any],
        requirements: Mapping[str, Any],
        literals: Sequence[Mapping[str, Any]],
    ) -> MultihopAssemblyResult:
        filter_requirements = _as_list(requirements.get("filters"))
        if not filter_requirements:
            return _fallback("missing_filter_property")

        filters: list[dict[str, Any]] = []
        for item in filter_requirements:
            property_name = _property_name(item)
            if property_name is None:
                return _fallback("missing_filter_property")
            owner_hint = _owner_name(item)
            property_ref = self._unique_property(candidates, property_name, owner_hint=owner_hint)
            if isinstance(property_ref, MultihopAssemblyResult):
                return _fallback(
                    "ambiguous_filter_property"
                    if property_ref.fallback_reason == "ambiguous_projection_property"
                    else property_ref.fallback_reason or "invalid_filter_property"
                )
            owner, name = property_ref
            target = path.role_by_owner.get(owner)
            if target is None:
                return _fallback("filter_owner_mismatch")
            literal = _unique_literal(literals, owner, name)
            if literal is None:
                return _fallback("missing_filter_literal")
            operator = _operator(item)
            if operator is None:
                return _fallback("unsupported_filter_operator")
            filters.append(
                {
                    "target": target,
                    "property": {"owner": owner, "name": name},
                    "operator": operator,
                    "value": _literal_value(literal),
                }
            )

        projection = self._projection_items(candidates, requirements, path.role_by_owner)
        if isinstance(projection, MultihopAssemblyResult):
            return projection
        return _success(_single_hop_dsl("multihop-f5", path, filters=filters, projection=projection))

    def _assemble_f6(
        self,
        path: "_PathPlan",
        candidates: Sequence[Any],
        requirements: Mapping[str, Any],
    ) -> MultihopAssemblyResult:
        group_by = _as_list(requirements.get("group_by") or requirements.get("group_by_terms"))
        if len(group_by) != 1:
            return _fallback("ambiguous_group_by_requirement" if group_by else "missing_group_by_requirement")
        group_property = _property_name(group_by[0])
        if group_property is None:
            return _fallback("missing_group_by_property")
        group_ref = self._unique_property(candidates, group_property, owner_hint=_owner_name(group_by[0]))
        if isinstance(group_ref, MultihopAssemblyResult):
            return _fallback("ambiguous_group_by_property")
        group_owner, group_name = group_ref
        group_target = path.role_by_owner.get(group_owner)
        if group_target is None:
            return _fallback("group_by_owner_mismatch")

        measure = _aggregate_requirement(requirements)
        if measure is None:
            return _fallback("missing_aggregate_requirement")
        measure_property = _property_name(measure)
        if measure_property is None:
            return _fallback("missing_aggregate_property")
        measure_ref = self._unique_property(candidates, measure_property, owner_hint=_owner_name(measure))
        if isinstance(measure_ref, MultihopAssemblyResult):
            return _fallback("ambiguous_aggregate_property")
        measure_owner, measure_name = measure_ref
        measure_target = path.role_by_owner.get(measure_owner)
        if measure_target is None:
            return _fallback("aggregate_owner_mismatch")
        measure_function = _aggregate_function(measure)
        if measure_function not in {"count", "sum", "avg", "min", "max"}:
            return _fallback("unsupported_aggregate_function")

        order_by = _as_list(requirements.get("order_by") or requirements.get("order") or requirements.get("sort"))
        if len(order_by) != 1:
            return _fallback("ambiguous_order_by_requirement" if order_by else "missing_order_by_requirement")
        measure_alias = str(_alias(measure) or f"{_snake_case(measure_owner)}_{measure_function}")
        group_alias = str(_alias(group_by[0]) or f"{_snake_case(group_owner)}_{group_name}")
        sort_source = _sort_source(order_by[0])
        if sort_source is None:
            return _fallback("missing_order_by_source")
        if sort_source not in {measure_alias, f"measure.{measure_alias}"}:
            return _fallback("order_by_source_mismatch")
        sort_direction = _sort_direction(order_by[0], requirements)
        if sort_direction is None:
            return _fallback("missing_order_by_direction")

        limit_value = _limit_value(requirements)
        if limit_value is _AMBIGUOUS_LIMIT:
            return _fallback("ambiguous_limit_requirement")
        if limit_value is None:
            return _fallback("missing_limit_requirement")

        return _success(
            _path_group_topn_dsl(
                path,
                group={
                    "alias": group_alias,
                    "target": group_target,
                    "property": {"owner": group_owner, "name": group_name},
                    "projection_terms": _projection_terms(group_by[0]),
                },
                measure={
                    "alias": measure_alias,
                    "function": measure_function,
                    "target": measure_target,
                    "property": {"owner": measure_owner, "name": measure_name},
                    "projection_terms": _projection_terms(measure),
                },
                sort={"source": f"measure.{measure_alias}", "direction": sort_direction},
                limit=limit_value,
            )
        )

    def _resolve_edge_chain(
        self,
        candidates: Sequence[Any],
        requirements: Mapping[str, Any],
    ) -> "_PathPlan | MultihopAssemblyResult":
        path_terms = _path_term_texts(requirements)
        vertex_names = self._ordered_vertex_names(candidates, path_terms)
        if isinstance(vertex_names, MultihopAssemblyResult):
            return vertex_names
        if len(vertex_names) < 2:
            return _fallback("missing_vertex_candidate")

        edge_candidates = [
            candidate
            for candidate in candidates
            if _candidate_type(candidate) == "edge"
        ]
        if not edge_candidates:
            return _fallback("missing_path_candidate")

        vertex_names, extension_contexts = self._extend_vertex_names_for_projection_endpoints(
            vertex_names,
            candidates=candidates,
            requirements=requirements,
            edge_candidates=edge_candidates,
            path_terms=path_terms,
        )

        vertex_roles = {vertex_name: f"v{index}" for index, vertex_name in enumerate(vertex_names)}
        role_by_owner = dict(vertex_roles)
        steps: list[_EdgeStep] = []
        for index, (start, end) in enumerate(zip(vertex_names, vertex_names[1:])):
            step = self._resolve_step_edge(
                edge_candidates=edge_candidates,
                start=start,
                end=end,
                context=extension_contexts.get(
                    (start, end),
                    _path_segment_context(path_terms, start, end, self.registry),
                ),
                index=index,
                vertex_roles=vertex_roles,
            )
            if isinstance(step, MultihopAssemblyResult):
                return step
            steps.append(step)

        return _PathPlan(
            steps=steps,
            role_by_owner=role_by_owner,
        )

    def _ordered_vertex_names(
        self,
        candidates: Sequence[Any],
        path_terms: Sequence[str],
    ) -> list[str] | MultihopAssemblyResult:
        positions: list[tuple[int, str]] = []
        for candidate in candidates:
            if _candidate_type(candidate) != "vertex":
                continue
            vertex_name = _candidate_name(candidate)
            if not vertex_name:
                continue
            try:
                vertex = self.registry.get_vertex(vertex_name)
            except RegistryLookupError:
                return _fallback("unknown_vertex_candidate")
            position = _first_vertex_path_term_index(path_terms, vertex)
            if position is None:
                continue
            positions.append((position, vertex_name))
        unique_positions = sorted(set(positions))
        vertex_names = [vertex_name for _, vertex_name in unique_positions]
        if len(vertex_names) != len(set(vertex_names)):
            return _fallback("ambiguous_vertex_candidate")
        return vertex_names

    def _extend_vertex_names_for_projection_endpoints(
        self,
        vertex_names: list[str],
        *,
        candidates: Sequence[Any],
        requirements: Mapping[str, Any],
        edge_candidates: Sequence[Any],
        path_terms: Sequence[str],
    ) -> tuple[list[str], dict[tuple[str, str], str]]:
        extended = list(vertex_names)
        extension_contexts: dict[tuple[str, str], str] = {}
        candidate_vertices = _candidate_vertex_names(candidates)
        pending = [
            owner
            for owner in _required_path_owner_names(requirements)
            if owner not in extended and owner in candidate_vertices
        ]
        while pending and extended:
            for owner in list(pending):
                start = extended[-1]
                context = self._endpoint_extension_context(
                    edge_candidates=edge_candidates,
                    path_terms=path_terms,
                    start=start,
                    end=owner,
                )
                if context is None:
                    continue
                extension_contexts[(start, owner)] = context
                extended.append(owner)
                pending.remove(owner)
                break
            else:
                break
        return extended, extension_contexts

    def _endpoint_extension_context(
        self,
        *,
        edge_candidates: Sequence[Any],
        path_terms: Sequence[str],
        start: str,
        end: str,
    ) -> str | None:
        contexts = [
            _path_segment_context(path_terms, start, end, self.registry),
            _path_tail_context(path_terms, start, self.registry),
            "",
        ]
        seen: set[str] = set()
        for context in contexts:
            if context is None:
                continue
            if context in seen:
                continue
            seen.add(context)
            step = self._resolve_step_edge(
                edge_candidates=edge_candidates,
                start=start,
                end=end,
                context=context,
                index=0,
                vertex_roles={start: "start", end: "end"},
            )
            if not isinstance(step, MultihopAssemblyResult):
                return context
        return None

    def _resolve_step_edge(
        self,
        *,
        edge_candidates: Sequence[Any],
        start: str,
        end: str,
        context: str,
        index: int,
        vertex_roles: Mapping[str, str],
    ) -> "_EdgeStep | MultihopAssemblyResult":
        connecting: list[EdgeDefinition] = []
        seen: set[str] = set()
        for candidate in edge_candidates:
            edge_name = _candidate_name(candidate)
            if not edge_name or edge_name in seen:
                continue
            seen.add(edge_name)
            try:
                edge = self.registry.get_edge(edge_name)
            except RegistryLookupError:
                return _fallback("unknown_edge_candidate")
            if {edge.from_vertex, edge.to_vertex} == {start, end}:
                connecting.append(edge)

        if not connecting:
            return _fallback("missing_path_candidate")
        direction = DirectionMapper(self.registry).resolve_direction_terms(context)
        if direction.status == DirectionStatus.AMBIGUOUS:
            step_direction_edges = _direction_edge_names_for_step(
                direction.edge_names,
                start=start,
                end=end,
                registry=self.registry,
            )
            narrowed = [edge for edge in connecting if edge.name in step_direction_edges]
            if len(narrowed) == 1 and len(step_direction_edges) == 1:
                edge = narrowed[0]
            elif len(connecting) == 1 and not step_direction_edges:
                edge = connecting[0]
            else:
                return _fallback("ambiguous_direction_terms")
        elif len(connecting) == 1:
            edge = connecting[0]
            if direction.status == DirectionStatus.RESOLVED and edge.name not in direction.edge_names:
                step_direction_edges = _direction_edge_names_for_step(
                    direction.edge_names,
                    start=start,
                    end=end,
                    registry=self.registry,
                )
                if step_direction_edges:
                    return _fallback("direction_edge_mismatch")
        else:
            if direction.status == DirectionStatus.UNRESOLVED:
                return _fallback("unresolved_direction_terms")
            narrowed = [edge for edge in connecting if edge.name in direction.edge_names]
            if len(narrowed) != 1:
                return _fallback("direction_edge_mismatch" if not narrowed else "ambiguous_path_candidate")
            edge = narrowed[0]

        if self.registry.edge_connects(edge.name, start, end, "forward"):
            traversal_direction = "forward"
        elif self.registry.edge_connects(edge.name, start, end, "reverse"):
            traversal_direction = "backward"
        else:
            return _fallback("invalid_path_endpoints")

        return _EdgeStep(
            edge_name=edge.name,
            from_vertex=start,
            to_vertex=end,
            direction=traversal_direction,
            from_role=vertex_roles[start],
            to_role=vertex_roles[end],
            edge_role=f"edge_{index}",
        )

    def _projection_items(
        self,
        candidates: Sequence[Any],
        requirements: Mapping[str, Any],
        role_by_owner: Mapping[str, str],
    ) -> list[dict[str, Any]] | MultihopAssemblyResult:
        projection_requirements = _projection_requirements(requirements)
        if not projection_requirements:
            return _fallback("missing_projection_property")

        projection: list[dict[str, Any]] = []
        for item in projection_requirements:
            if isinstance(item, Mapping) and item.get("semantic_type") == "vertex_full":
                owner = str(item.get("name") or "")
                if not owner:
                    return _fallback("missing_projection_vertex")
                target = role_by_owner.get(owner)
                if target is None:
                    return _fallback("projection_owner_mismatch")
                projection.append(
                    {
                        "target": target,
                        "vertex_full": True,
                        "alias": str(_alias(item) or _snake_case(owner)),
                        "projection_terms": _projection_terms(item),
                    }
                )
                continue
            property_name = _property_name(item)
            if property_name is None:
                return _fallback("missing_projection_property")
            owner_hint = _owner_name(item)
            if owner_hint is not None:
                try:
                    self.registry.get_property(owner_hint, property_name)
                except RegistryLookupError:
                    return _fallback("unknown_projection_property")
                owner, name = owner_hint, property_name
            else:
                property_ref = self._unique_property(candidates, property_name)
                if isinstance(property_ref, MultihopAssemblyResult):
                    return property_ref
                owner, name = property_ref
            target = role_by_owner.get(owner)
            if target is None:
                return _fallback("projection_owner_mismatch")
            projection.append(_projection_item(target, owner, name, _alias(item), _projection_terms(item)))
        return projection

    def _unique_property(
        self,
        candidates: Sequence[Any],
        property_name: str,
        *,
        owner_hint: str | None = None,
    ) -> tuple[str, str] | MultihopAssemblyResult:
        matches = [
            (owner, name)
            for candidate in candidates
            if _candidate_type(candidate) == "property"
            for owner, name in [_candidate_property(candidate)]
            if owner is not None and name == property_name and (owner_hint is None or owner == owner_hint)
        ]
        unique_matches = sorted(set(matches))
        if len(unique_matches) != 1:
            return _fallback("ambiguous_projection_property")
        owner, name = unique_matches[0]
        try:
            self.registry.get_property(owner, name)
        except RegistryLookupError:
            return _fallback("unknown_projection_property")
        return owner, name


@dataclass(frozen=True)
class _EdgeStep:
    edge_name: str
    from_vertex: str
    to_vertex: str
    direction: str
    from_role: str
    to_role: str
    edge_role: str


@dataclass(frozen=True)
class _PathPlan:
    steps: list[_EdgeStep]
    role_by_owner: dict[str, str]


def _success(dsl: dict[str, Any]) -> MultihopAssemblyResult:
    return MultihopAssemblyResult(success=True, dsl=dsl)


def _fallback(reason: str) -> MultihopAssemblyResult:
    return MultihopAssemblyResult(success=False, fallback_reason=reason)


def _single_hop_dsl(
    query_id: str,
    path: _PathPlan,
    *,
    filters: list[dict[str, Any]],
    projection: list[dict[str, Any]],
) -> dict[str, Any]:
    bindings: dict[str, dict[str, str]] = {}
    for owner, role in path.role_by_owner.items():
        bindings[role] = {"vertex_name": owner}
    for step in path.steps:
        bindings[step.edge_role] = {"edge_name": step.edge_name}

    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": query_id,
        "query_shape": "single_hop_traversal",
        "source_question": "",
        "bindings": bindings,
        "operations": [
            {
                "op": "traverse_edge",
                "from": step.from_role,
                "edge": step.edge_role,
                "to": step.to_role,
                "direction": step.direction,
            }
            for step in path.steps
        ],
        "filters": filters,
        "projection": {"items": projection},
    }


def _path_group_topn_dsl(
    path: _PathPlan,
    *,
    group: dict[str, Any],
    measure: dict[str, Any],
    sort: dict[str, Any],
    limit: int,
) -> dict[str, Any]:
    bindings: dict[str, dict[str, str]] = {}
    for owner, role in path.role_by_owner.items():
        bindings[role] = {"vertex_name": owner}
    for step in path.steps:
        bindings[step.edge_role] = {"edge_name": step.edge_name}

    group_alias = str(group["alias"])
    measure_alias = str(measure["alias"])
    group_projection = {"alias": group_alias, "source": f"group.{group_alias}"}
    group_terms = _projection_terms(group)
    if group_terms:
        group_projection["projection_terms"] = group_terms
    measure_projection = {"alias": measure_alias, "source": f"measure.{measure_alias}"}
    measure_terms = _projection_terms(measure)
    if measure_terms:
        measure_projection["projection_terms"] = measure_terms
    aggregate_group = {
        "alias": group_alias,
        "target": group["target"],
        "property": group["property"],
    }
    aggregate_measure = {
        "alias": measure_alias,
        "function": measure["function"],
        "target": measure["target"],
        "property": measure["property"],
    }
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "multihop-f6",
        "query_shape": "top_n",
        "source_question": "",
        "bindings": bindings,
        "operations": [
            *[
                {
                    "op": "traverse_edge",
                    "from": step.from_role,
                    "edge": step.edge_role,
                    "to": step.to_role,
                    "direction": step.direction,
                }
                for step in path.steps
            ],
            {
                "op": "aggregate",
                "group_by": [aggregate_group],
                "measures": [aggregate_measure],
            },
            {"op": "sort", "by": [sort]},
            {"op": "limit", "value": limit},
        ],
        "filters": [],
        "projection": {
            "items": [
                group_projection,
                measure_projection,
            ]
        },
    }


def _projection_item(
    target: str,
    owner: str,
    property_name: str,
    alias: Any | None,
    projection_terms: Sequence[str] | None = None,
) -> dict[str, Any]:
    item = {
        "target": target,
        "property": {"owner": owner, "name": property_name},
    }
    if alias is not None:
        item["alias"] = str(alias)
    terms = [str(term).strip() for term in projection_terms or [] if str(term).strip()]
    if terms:
        item["projection_terms"] = terms
    return item


def _requirements_dict(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, Mapping) else {}
    return {}


def _shape_family(shape: str) -> str:
    normalized = str(shape).strip().upper()
    if normalized.startswith("F4"):
        return "F4"
    if normalized.startswith("F5"):
        return "F5"
    if normalized.startswith("F6"):
        return "F6"
    return normalized


def _projection_requirements(requirements: Mapping[str, Any]) -> list[Any]:
    projection = _as_list(requirements.get("projection"))
    if projection:
        return projection
    return _as_list(requirements.get("projection_terms"))


def _projection_owner_names(requirements: Mapping[str, Any]) -> list[str]:
    return _owner_names_from_items(_projection_requirements(requirements))


def _required_path_owner_names(requirements: Mapping[str, Any]) -> list[str]:
    owners: list[str] = []
    for item in [
        *_projection_requirements(requirements),
        *_as_list(requirements.get("group_by") or requirements.get("group_by_terms")),
        *_as_list(requirements.get("measures")),
        requirements.get("aggregate") or requirements.get("measure"),
    ]:
        owner = _owner_name(item)
        if owner is not None and owner not in owners:
            owners.append(owner)
    return owners


def _owner_names_from_items(items: Sequence[Any]) -> list[str]:
    owners: list[str] = []
    for item in items:
        owner = _owner_name(item)
        if owner is not None and owner not in owners:
            owners.append(owner)
    return owners


def _candidate_vertex_names(candidates: Sequence[Any]) -> set[str]:
    return {
        vertex_name
        for candidate in candidates
        if _candidate_type(candidate) == "vertex"
        for vertex_name in [_candidate_name(candidate)]
        if vertex_name
    }


def _direction_edge_names_for_step(
    edge_names: Sequence[str],
    *,
    start: str,
    end: str,
    registry: GraphSemanticRegistry,
) -> set[str]:
    step_edges: set[str] = set()
    for edge_name in edge_names:
        try:
            if registry.edge_connects(edge_name, start, end, "either"):
                step_edges.add(edge_name)
        except RegistryLookupError:
            continue
    return step_edges


def _aggregate_requirement(requirements: Mapping[str, Any]) -> Any | None:
    measures = _as_list(requirements.get("measures"))
    if len(measures) > 1:
        return None
    if measures:
        return measures[0]
    aggregate = requirements.get("aggregate") or requirements.get("measure")
    return aggregate if aggregate is not None else None


def _direction_context(requirements: Mapping[str, Any]) -> str:
    direct = (
        requirements.get("source_question")
        or requirements.get("original_question")
        or requirements.get("question")
    )
    if direct:
        return str(direct)

    terms = []
    for item in _as_list(requirements.get("path_terms")):
        text = _term_text(item)
        if text:
            terms.append(text)
    return " ".join(terms)


def _path_term_texts(requirements: Mapping[str, Any]) -> list[str]:
    terms = [_term_text(item) for item in _as_list(requirements.get("path_terms"))]
    return [term for term in terms if term]


def _first_vertex_path_term_index(path_terms: Sequence[str], vertex: VertexDefinition) -> int | None:
    vertex_terms = _vertex_terms(vertex)
    for index, term in enumerate(path_terms):
        normalized = _normalize(term)
        if any(vertex_term in normalized or normalized in vertex_term for vertex_term in vertex_terms):
            return index
    return None


def _path_segment_context(
    path_terms: Sequence[str],
    start: str,
    end: str,
    registry: GraphSemanticRegistry,
) -> str:
    try:
        start_index = _first_vertex_path_term_index(path_terms, registry.get_vertex(start))
        end_index = _first_vertex_path_term_index(path_terms, registry.get_vertex(end))
    except RegistryLookupError:
        return " ".join(path_terms)
    if start_index is None or end_index is None:
        return " ".join(path_terms)
    low, high = sorted((start_index, end_index))
    return " ".join(path_terms[low : high + 1])


def _path_tail_context(
    path_terms: Sequence[str],
    start: str,
    registry: GraphSemanticRegistry,
) -> str | None:
    try:
        start_index = _first_vertex_path_term_index(path_terms, registry.get_vertex(start))
    except RegistryLookupError:
        return None
    if start_index is None:
        return None
    return " ".join(path_terms[start_index:])


def _first_vertex_term_position(context: str, vertex: VertexDefinition) -> int | None:
    normalized_context = _normalize(context)
    positions = [
        position
        for term in _vertex_terms(vertex)
        for position in [_find_term(normalized_context, term)]
        if position is not None
    ]
    return min(positions) if positions else None


def _vertex_terms(vertex: VertexDefinition) -> set[str]:
    terms = {vertex.name, *_split_identifier(vertex.name)}
    value = vertex.ai_context.get("synonyms")
    if isinstance(value, str):
        terms.add(value)
    elif isinstance(value, Iterable):
        terms.update(str(item) for item in value if isinstance(item, str) and item)
    return {_normalize(term) for term in terms if _normalize(term)}


def _find_term(normalized_context: str, normalized_term: str) -> int | None:
    if not normalized_context or not normalized_term:
        return None
    index = normalized_context.find(normalized_term)
    return index if index >= 0 else None


def _split_identifier(identifier: str) -> set[str]:
    return {part for part in re.split(r"[_\W]+", identifier) if part}


def _unique_literal(literals: Sequence[Mapping[str, Any]], owner: str, property_name: str) -> Mapping[str, Any] | None:
    matches = [
        literal
        for literal in literals
        if _get(literal, "owner") == owner
        and (_get(literal, "property") or _get(literal, "name")) == property_name
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _literal_value(literal: Mapping[str, Any]) -> dict[str, Any]:
    raw = _get(literal, "raw")
    if raw is None:
        raw = _get(literal, "raw_literal")
    normalized = _get(literal, "normalized")
    if normalized is None:
        normalized = _get(literal, "normalized_value")
    if normalized is None:
        normalized = _get(literal, "value")
    if normalized is None:
        normalized = _get(literal, "resolved_value")
    return {
        "raw": raw,
        "normalized": normalized,
        "resolver_match_type": _get(literal, "resolver_match_type") or _get(literal, "match_type"),
    }


def _operator(item: Any) -> str | None:
    if isinstance(item, Mapping):
        operator = item.get("operator") or "eq"
    else:
        operator = "eq"
    return _OPERATOR_ALIASES.get(str(operator))


_OPERATOR_ALIASES = {
    "=": "eq",
    "==": "eq",
    "eq": "eq",
    "等于": "eq",
    "为": "eq",
    "是": "eq",
    "!=": "neq",
    "<>": "neq",
    "neq": "neq",
    "不等于": "neq",
    "不是": "neq",
    ">": "gt",
    "gt": "gt",
    "大于": "gt",
    "超过": "gt",
    "高于": "gt",
    "多于": "gt",
    "<": "lt",
    "lt": "lt",
    "小于": "lt",
    "低于": "lt",
    "少于": "lt",
    ">=": "gte",
    "gte": "gte",
    "大于等于": "gte",
    "不少于": "gte",
    "不小于": "gte",
    "至少": "gte",
    "不低于": "gte",
    "<=": "lte",
    "lte": "lte",
    "小于等于": "lte",
    "不超过": "lte",
    "不大于": "lte",
    "最多": "lte",
    "不高于": "lte",
}


_AMBIGUOUS_LIMIT = object()


def _limit_value(requirements: Mapping[str, Any]) -> int | object | None:
    values: list[int] = []
    raw_limit = requirements.get("limit")
    if isinstance(raw_limit, int) and raw_limit > 0:
        values.append(raw_limit)
    elif isinstance(raw_limit, list | tuple):
        values.extend(item for item in raw_limit if isinstance(item, int) and item > 0)
    limit_requirement = requirements.get("requires_limit")
    if isinstance(limit_requirement, Mapping):
        value = limit_requirement.get("value")
        if isinstance(value, int) and value > 0:
            values.append(value)
    unique_values = set(values)
    if len(unique_values) > 1:
        return _AMBIGUOUS_LIMIT
    if not unique_values:
        return None
    return next(iter(unique_values))


def _sort_source(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if not isinstance(item, Mapping):
        return None
    source = item.get("source") or item.get("by")
    return str(source) if source is not None else None


def _sort_direction(item: Any, requirements: Mapping[str, Any]) -> str | None:
    direction = item.get("direction") if isinstance(item, Mapping) else None
    direction = direction or requirements.get("order_direction")
    if direction in {"asc", "desc"}:
        return str(direction)
    return None


def _aggregate_function(item: Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("function") or "count")
    return "count"


def _candidate_type(candidate: Any) -> str | None:
    return _get(candidate, "semantic_type")


def _candidate_name(candidate: Any) -> str | None:
    return _get(candidate, "semantic_name") or _get(candidate, "semantic_id")


def _candidate_property(candidate: Any) -> tuple[str | None, str | None]:
    owner = _get(candidate, "owner")
    name = _get(candidate, "semantic_name")
    semantic_id = _get(candidate, "semantic_id")
    if owner is None and isinstance(semantic_id, str) and "." in semantic_id:
        owner, _, candidate_name = semantic_id.rpartition(".")
        name = name or candidate_name
    if isinstance(name, str) and "." in name:
        inferred_owner, _, inferred_name = name.rpartition(".")
        owner = owner or inferred_owner
        name = inferred_name
    return owner, name


def _property_name(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if not isinstance(item, Mapping):
        return None
    property_ref = item.get("property")
    if isinstance(property_ref, Mapping):
        name = property_ref.get("name") or property_ref.get("property_name")
    else:
        name = property_ref or item.get("name") or item.get("property_name")
    return str(name) if name is not None else None


def _owner_name(item: Any) -> str | None:
    if not isinstance(item, Mapping):
        return None
    property_ref = item.get("property")
    if isinstance(property_ref, Mapping):
        owner = property_ref.get("owner")
    else:
        owner = item.get("owner")
    return str(owner) if owner is not None else None


def _alias(item: Any) -> Any | None:
    if isinstance(item, Mapping):
        return item.get("alias")
    return None


def _projection_terms(item: Any) -> list[str]:
    if not isinstance(item, Mapping):
        return []
    raw_terms = item.get("projection_terms")
    if not isinstance(raw_terms, list | tuple):
        return []
    return [str(term).strip() for term in raw_terms if str(term).strip()]


def _term_text(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if not isinstance(item, Mapping):
        return None
    value = item.get("text")
    return str(value) if value is not None else None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _get(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())


def _snake_case(value: str) -> str:
    text = value.replace("-", "_")
    text = "".join(f"_{char.lower()}" if char.isupper() else char for char in text)
    return text.strip("_")
