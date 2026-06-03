from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from services.cypher_generator_agent.app.literals.models import LiteralResolverResult
from services.cypher_generator_agent.app.retrieval.models import (
    CandidateRetrievalResult,
    SemanticCandidate,
)
from services.cypher_generator_agent.app.semantic_model.registry import (
    GraphSemanticRegistry,
    RegistryLookupError,
)

from .models import (
    BindingPlan,
    CandidateBinding,
    EdgeBinding,
    FilterBinding,
    LiteralBinding,
    MetricBinding,
    PathPatternBinding,
    PropertyBinding,
    VertexBinding,
)


_QUERY_SHAPE_ALIASES = {
    "lookup": "vertex_lookup",
    "vertex": "vertex_lookup",
    "vertex_lookup": "vertex_lookup",
    "single_hop": "single_hop_traversal",
    "single_hop_traversal": "single_hop_traversal",
    "variable_path": "variable_path_traversal",
    "variable_path_traversal": "variable_path_traversal",
    "named_path": "named_path_pattern",
    "named_path_pattern": "named_path_pattern",
    "metric_aggregate": "metric_aggregate",
    "aggregate": "ad_hoc_aggregate",
    "ad_hoc_aggregate": "ad_hoc_aggregate",
    "top_n": "top_n",
    "two_step_aggregate": "two_step_aggregate",
}
_OPERATOR_ALIASES = {
    "=": "eq",
    "==": "eq",
    "eq": "eq",
    "!=": "neq",
    "<>": "neq",
    "neq": "neq",
    ">": "gt",
    "gt": "gt",
    ">=": "gte",
    "gte": "gte",
    "<": "lt",
    "lt": "lt",
    "<=": "lte",
    "lte": "lte",
    "in": "in",
    "contains": "contains",
}


class BindingValidationError(ValueError):
    """Raised when grounded understanding selects an unbindable semantic name."""


class SemanticBinder:
    def __init__(
        self,
        registry: GraphSemanticRegistry,
        *,
        fuzzy_assumption_threshold: float = 0.95,
    ) -> None:
        self.registry = registry
        self.fuzzy_assumption_threshold = fuzzy_assumption_threshold

    def bind(
        self,
        grounded_understanding: Mapping[str, Any],
        *,
        candidates: CandidateRetrievalResult | Sequence[SemanticCandidate] | Mapping[str, Any],
    ) -> BindingPlan:
        candidate_index = _CandidateIndex(_coerce_candidates(candidates))
        literal_bindings = self._bind_literals(grounded_understanding)
        property_bindings = self._bind_properties(grounded_understanding, candidate_index)
        filter_bindings = self._bind_filters(
            grounded_understanding,
            candidate_index,
            literal_bindings,
            property_bindings,
        )
        if not filter_bindings:
            filter_bindings = _filters_from_selected_literals(literal_bindings, property_bindings)

        for filter_binding in filter_bindings:
            property_binding = self._bind_property(
                {"owner": filter_binding.owner, "name": filter_binding.property},
                candidate_index,
            )
            property_bindings = _append_unique_property(property_bindings, property_binding)

        assumptions = _coerce_assumptions(grounded_understanding.get("assumptions", []))
        assumptions.extend(self._fuzzy_literal_assumptions(literal_bindings))
        projection = self._bind_projection(grounded_understanding, candidate_index)
        sort = self._bind_sort(grounded_understanding, candidate_index)
        group_by = self._bind_group_by(grounded_understanding, candidate_index)
        measures = self._bind_measures(grounded_understanding, candidate_index)

        vertex_bindings = self._bind_vertices(grounded_understanding, candidate_index)
        edge_bindings = self._bind_edges(grounded_understanding, candidate_index)
        metric_bindings = self._bind_metrics(grounded_understanding, candidate_index)
        path_pattern_bindings = self._bind_path_patterns(grounded_understanding, candidate_index)
        query_shape = _normalize_query_shape(grounded_understanding.get("query_shape"))
        if (
            query_shape == "variable_path_traversal"
            and len(edge_bindings) > 1
            and len(vertex_bindings) != len(edge_bindings) + 1
        ):
            inferred_vertices = self._infer_vertex_chain_from_edges(edge_bindings, candidate_index)
            if inferred_vertices is not None and _selected_vertices_are_subset(vertex_bindings, inferred_vertices):
                vertex_bindings = inferred_vertices
                assumptions.append(
                    {
                        "type": "inferred_vertex_chain_from_edges",
                        "reason": "selected edge bindings uniquely determine the missing traversal vertices",
                        "vertices": [binding.name for binding in vertex_bindings],
                        "edges": [binding.name for binding in edge_bindings],
                    }
                )
        if not edge_bindings and query_shape in {"vertex_lookup", "single_hop_traversal"}:
            inferred_edge = self._infer_unambiguous_connecting_edge(vertex_bindings, candidate_index)
            if inferred_edge is not None:
                edge_bindings = _append_unique_edge(edge_bindings, inferred_edge)
                assumptions.append(
                    {
                        "type": "inferred_edge_binding",
                        "edge": inferred_edge.name,
                        "from_vertex": self.registry.get_edge(inferred_edge.name).from_vertex,
                        "to_vertex": self.registry.get_edge(inferred_edge.name).to_vertex,
                        "direction": inferred_edge.direction,
                    }
                )
        if query_shape == "vertex_lookup" and edge_bindings and len(vertex_bindings) >= 2:
            query_shape = "single_hop_traversal"
        if query_shape == "vertex_lookup" and not vertex_bindings:
            inferred_vertex = self._infer_vertex_from_unique_property_owner(
                property_bindings,
                filter_bindings,
                projection,
                literal_bindings,
                candidate_index,
            )
            if inferred_vertex is not None:
                vertex_bindings = [inferred_vertex]
                assumptions.append(
                    {
                        "type": "inferred_vertex_from_property_owner",
                        "reason": "vertex_lookup selected only properties/literals owned by one vertex",
                        "vertex": inferred_vertex.name,
                    }
                )
        if (
            query_shape == "variable_path_traversal"
            and len(edge_bindings) > 1
            and len(vertex_bindings) == len(edge_bindings) + 1
        ):
            assumptions.append(
                {
                    "type": "query_shape_normalized",
                    "from": "variable_path_traversal",
                    "to": "single_hop_traversal",
                    "reason": "multiple edge bindings form a traversal chain",
                    "edge_count": len(edge_bindings),
                    "vertex_count": len(vertex_bindings),
                }
            )
            query_shape = "single_hop_traversal"
        if query_shape == "metric_aggregate" and not metric_bindings and measures:
            query_shape = "ad_hoc_aggregate"

        return BindingPlan(
            query_shape=query_shape,
            vertex_bindings=vertex_bindings,
            edge_bindings=edge_bindings,
            property_bindings=property_bindings,
            literal_bindings=literal_bindings,
            metric_bindings=metric_bindings,
            path_pattern_bindings=path_pattern_bindings,
            filters=filter_bindings,
            group_by=group_by,
            measures=measures,
            projection=projection,
            sort=sort,
            limit=grounded_understanding.get("limit"),
            assumptions=assumptions,
        )

    def _bind_vertices(
        self,
        grounded: Mapping[str, Any],
        candidate_index: "_CandidateIndex",
    ) -> list[VertexBinding]:
        bindings: list[VertexBinding] = []
        for item in _selected_items(grounded, "selected_vertices", "vertices", "vertex_bindings"):
            name = _extract_name(item, "vertex")
            self._require_registry("vertex", name, self.registry.get_vertex)
            candidate = candidate_index.require("vertex", name)
            bindings = _append_unique_named(bindings, VertexBinding(name=name, candidate=candidate))
        return bindings

    def _bind_edges(
        self,
        grounded: Mapping[str, Any],
        candidate_index: "_CandidateIndex",
    ) -> list[EdgeBinding]:
        bindings: list[EdgeBinding] = []
        for item in _selected_items(grounded, "selected_edges", "edges", "edge_bindings"):
            name = _extract_name(item, "edge")
            self._require_registry("edge", name, self.registry.get_edge)
            candidate = candidate_index.require("edge", name)
            binding = EdgeBinding(
                name=name,
                candidate=candidate,
                direction=_extract_edge_direction(item),
            )
            bindings = _append_unique_edge(bindings, binding)
        return bindings

    def _bind_properties(
        self,
        grounded: Mapping[str, Any],
        candidate_index: "_CandidateIndex",
    ) -> list[PropertyBinding]:
        bindings: list[PropertyBinding] = []
        for item in _selected_items(grounded, "selected_properties", "properties", "property_bindings"):
            binding = self._bind_property(item, candidate_index)
            bindings = _append_unique_property(bindings, binding)
        return bindings

    def _bind_property(
        self,
        item: Any,
        candidate_index: "_CandidateIndex",
    ) -> PropertyBinding:
        owner, name = _extract_owner_name(item)
        self._require_registry("property", f"{owner}.{name}", lambda _: self.registry.get_property(owner, name))
        candidate = candidate_index.require("property", f"{owner}.{name}")
        return PropertyBinding(owner=owner, name=name, candidate=candidate)

    def _bind_metrics(
        self,
        grounded: Mapping[str, Any],
        candidate_index: "_CandidateIndex",
    ) -> list[MetricBinding]:
        bindings: list[MetricBinding] = []
        for item in _selected_items(grounded, "selected_metrics", "metrics", "metric_bindings"):
            name = _extract_name(item, "metric")
            self._require_registry("metric", name, self.registry.get_metric)
            candidate = candidate_index.require("metric", name)
            bindings = _append_unique_named(bindings, MetricBinding(name=name, candidate=candidate))
        return bindings

    def _bind_path_patterns(
        self,
        grounded: Mapping[str, Any],
        candidate_index: "_CandidateIndex",
    ) -> list[PathPatternBinding]:
        bindings: list[PathPatternBinding] = []
        for item in _selected_items(
            grounded,
            "selected_path_patterns",
            "path_patterns",
            "path_pattern_bindings",
        ):
            name = _extract_name(item, "path_pattern")
            self._require_registry("path_pattern", name, self.registry.get_path_pattern)
            candidate = candidate_index.require("path_pattern", name)
            bindings = _append_unique_named(bindings, PathPatternBinding(name=name, candidate=candidate))
        return bindings

    def _bind_literals(self, grounded: Mapping[str, Any]) -> list[LiteralBinding]:
        bindings: list[LiteralBinding] = []
        for item in _selected_items(
            grounded,
            "selected_literals",
            "literal_resolver_results",
            "resolved_literals",
            "literal_bindings",
        ):
            result = _coerce_literal_result(item)
            owner = result.expected_vertex or result.expected_edge
            if owner is not None:
                self._require_registry(
                    "property",
                    f"{owner}.{result.expected_property}",
                    lambda _: self.registry.get_property(owner, result.expected_property),
                )
            bindings.append(
                LiteralBinding(
                    raw_literal=result.raw_literal,
                    resolved=result.resolved,
                    value=_resolved_literal_value(result),
                    normalized_value=result.normalized_value,
                    match_type=result.match_type,
                    confidence=result.confidence,
                    owner=owner,
                    property=result.expected_property,
                    evidence=result.evidence,
                    alternatives=result.alternatives,
                    requires_user_choice=result.requires_user_choice,
                    value_index_miss=result.value_index_miss,
                    error_code=result.error_code,
                )
            )
        return bindings

    def _bind_filters(
        self,
        grounded: Mapping[str, Any],
        candidate_index: "_CandidateIndex",
        literal_bindings: list[LiteralBinding],
        property_bindings: list[PropertyBinding],
    ) -> list[FilterBinding]:
        filters: list[FilterBinding] = []
        for item in _selected_items(grounded, "filters", "selected_filters"):
            if not isinstance(item, Mapping):
                raise BindingValidationError(f"filter binding must be a mapping, got {item!r}")

            inline_literal = _literal_from_filter(item)
            filter_literals = literal_bindings if inline_literal is None else [*literal_bindings, inline_literal]
            owner, property_name = _extract_filter_owner_name(item, filter_literals, property_bindings)
            self._bind_property({"owner": owner, "name": property_name}, candidate_index)

            if inline_literal is not None:
                _validate_filter_literal_match(item, inline_literal, owner=owner, property_name=property_name)
                literal_bindings.append(inline_literal)
            raw_literal = _extract_raw_literal(item, inline_literal)
            literal = inline_literal or _find_literal_binding(literal_bindings, owner, property_name, raw_literal)
            if literal is None:
                raise BindingValidationError(
                    f"filter {owner}.{property_name} requires a matching literal resolver result"
                )
            filters.append(
                FilterBinding(
                    owner=owner,
                    property=property_name,
                    operator=_normalize_operator(item.get("operator") or item.get("op") or "="),
                    raw_literal=raw_literal,
                    value=literal.value if literal is not None else item.get("value"),
                    literal=literal,
                )
            )
        return filters

    def _bind_projection(
        self,
        grounded: Mapping[str, Any],
        candidate_index: "_CandidateIndex",
    ) -> list[dict[str, Any]]:
        projection = [
            _normalize_reference_item(item, field_name="projection")
            for item in _coerce_dict_list(grounded.get("projection", []), "projection")
        ]
        if not projection:
            projection = _projection_from_selected_bindings(grounded)
        for item in projection:
            self._validate_semantic_reference(item, candidate_index, field_name="projection")
        return projection


    def _bind_sort(
        self,
        grounded: Mapping[str, Any],
        candidate_index: "_CandidateIndex",
    ) -> list[dict[str, Any]]:
        sort = _coerce_dict_list(grounded.get("sort", []), "sort")
        for item in sort:
            self._validate_semantic_reference(item, candidate_index, field_name="sort")
        return sort

    def _bind_group_by(
        self,
        grounded: Mapping[str, Any],
        candidate_index: "_CandidateIndex",
    ) -> list[dict[str, Any]]:
        group_by = _coerce_dict_list(grounded.get("group_by", []), "group_by")
        for item in group_by:
            _validate_dimension_reference(item, field_name="group_by")
            self._validate_semantic_reference(
                {"semantic_type": "property", "property": item["property"]},
                candidate_index,
                field_name="group_by",
            )
        return group_by

    def _bind_measures(
        self,
        grounded: Mapping[str, Any],
        candidate_index: "_CandidateIndex",
    ) -> list[dict[str, Any]]:
        measures = [
            self._normalize_measure_item(item)
            for item in _coerce_dict_list(grounded.get("measures", []), "measures")
        ]
        for item in measures:
            _validate_measure_reference(item, field_name="measures")
            self._validate_semantic_reference(
                {"semantic_type": "property", "property": item["property"]},
                candidate_index,
                field_name="measures",
            )
        return measures

    def _normalize_measure_item(self, item: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(item)
        if isinstance(normalized.get("property"), Mapping):
            return normalized

        vertex_name = normalized.get("vertex") or normalized.get("owner")
        if vertex_name is None:
            return normalized

        vertex = self.registry.get_vertex(str(vertex_name))
        normalized["alias"] = normalized.get("alias") or f"{_snake_case(vertex.name)}_count"
        normalized["target"] = normalized.get("target") or _snake_case(vertex.name)
        normalized["property"] = {"owner": vertex.name, "name": vertex.id_property}
        return normalized

    def _validate_semantic_reference(
        self,
        item: Mapping[str, Any],
        candidate_index: "_CandidateIndex",
        *,
        field_name: str,
    ) -> None:
        semantic_type = item.get("semantic_type")
        if semantic_type is None:
            if _looks_like_property_reference(item):
                semantic_type = "property"
            elif "source" in item:
                _validate_source_reference(item, field_name=field_name)
                return
            else:
                raise BindingValidationError(
                    f"{field_name} item must declare semantic_type, property, or source: {item!r}"
                )

        try:
            if semantic_type == "property":
                owner, property_name = _extract_owner_name(item)
                self._require_registry(
                    "property",
                    f"{owner}.{property_name}",
                    lambda _: self.registry.get_property(owner, property_name),
                )
                candidate_index.require("property", f"{owner}.{property_name}")
                return
            name = _extract_name(item, str(semantic_type))
            candidate_type = str(semantic_type)
            if semantic_type == "vertex_full":
                candidate_type = "vertex"
            if semantic_type == "vertex":
                self._require_registry("vertex", name, self.registry.get_vertex)
            elif semantic_type == "vertex_full":
                self._require_registry("vertex", name, self.registry.get_vertex)
            elif semantic_type == "edge":
                self._require_registry("edge", name, self.registry.get_edge)
            elif semantic_type == "metric":
                self._require_registry("metric", name, self.registry.get_metric)
            elif semantic_type == "path_pattern":
                self._require_registry("path_pattern", name, self.registry.get_path_pattern)
            else:
                raise BindingValidationError(f"{field_name} has unsupported semantic_type {semantic_type}")
            candidate_index.require(candidate_type, name)
        except BindingValidationError as exc:
            raise BindingValidationError(f"{field_name} semantic reference rejected: {exc}") from exc

    def _fuzzy_literal_assumptions(self, literal_bindings: list[LiteralBinding]) -> list[dict[str, Any]]:
        assumptions: list[dict[str, Any]] = []
        for literal in literal_bindings:
            if (
                literal.resolved
                and literal.match_type == "fuzzy_text"
                and literal.confidence >= self.fuzzy_assumption_threshold
            ):
                assumptions.append(
                    {
                        "type": "literal_fuzzy_match",
                        "raw_literal": literal.raw_literal,
                        "owner": literal.owner,
                        "property": literal.property,
                        "value": literal.value,
                        "confidence": literal.confidence,
                    }
                )
        return assumptions

    def _infer_unambiguous_connecting_edge(
        self,
        vertex_bindings: list[VertexBinding],
        candidate_index: "_CandidateIndex",
    ) -> EdgeBinding | None:
        if len(vertex_bindings) < 2:
            return None

        vertex_names = [binding.name for binding in vertex_bindings]
        matches: list[EdgeBinding] = []
        for left_index, left in enumerate(vertex_names):
            for right in vertex_names[left_index + 1 :]:
                for edge in self.registry.model.edges:
                    direction: str | None = None
                    if edge.from_vertex == left and edge.to_vertex == right:
                        direction = "forward"
                    elif edge.from_vertex == right and edge.to_vertex == left:
                        direction = "backward"
                    if direction is None:
                        continue
                    candidate = candidate_index.get("edge", edge.name) or _inferred_edge_candidate(edge)
                    matches.append(
                        EdgeBinding(
                            name=edge.name,
                            candidate=candidate,
                            direction=direction,
                        )
                    )
        return matches[0] if len(matches) == 1 else None

    def _infer_vertex_chain_from_edges(
        self,
        edge_bindings: list[EdgeBinding],
        candidate_index: "_CandidateIndex",
    ) -> list[VertexBinding] | None:
        names: list[str] = []
        for edge_binding in edge_bindings:
            edge = self.registry.get_edge(edge_binding.name)
            start = edge.from_vertex if edge_binding.direction == "forward" else edge.to_vertex
            end = edge.to_vertex if edge_binding.direction == "forward" else edge.from_vertex
            if not names:
                names.extend([start, end])
                continue
            if names[-1] != start:
                return None
            names.append(end)
        if len(names) != len(edge_bindings) + 1:
            return None
        bindings: list[VertexBinding] = []
        for name in names:
            self._require_registry("vertex", name, self.registry.get_vertex)
            candidate = candidate_index.get("vertex", name) or _inferred_vertex_candidate(name)
            bindings = _append_unique_named(bindings, VertexBinding(name=name, candidate=candidate))
        return bindings

    def _infer_vertex_from_unique_property_owner(
        self,
        property_bindings: list[PropertyBinding],
        filter_bindings: list[FilterBinding],
        projection: list[dict[str, Any]],
        literal_bindings: list[LiteralBinding],
        candidate_index: "_CandidateIndex",
    ) -> VertexBinding | None:
        owners: set[str] = set()
        for binding in property_bindings:
            owners.add(binding.owner)
        for binding in filter_bindings:
            owners.add(binding.owner)
        for item in projection:
            owner = _projection_property_owner(item)
            if owner is not None:
                owners.add(owner)
        for binding in literal_bindings:
            if binding.owner is not None:
                owners.add(binding.owner)

        vertex_owners: set[str] = set()
        for owner in owners:
            try:
                self.registry.get_vertex(owner)
            except RegistryLookupError:
                continue
            vertex_owners.add(owner)
        if len(vertex_owners) != 1:
            return None
        vertex_name = next(iter(vertex_owners))
        candidate = candidate_index.get("vertex", vertex_name) or _inferred_vertex_candidate(vertex_name)
        return VertexBinding(name=vertex_name, candidate=candidate)

    def _require_registry(self, object_type: str, name: str, lookup: Any) -> None:
        try:
            lookup(name)
        except RegistryLookupError as exc:
            raise BindingValidationError(
                f"cannot bind {object_type} {name}: not found in semantic registry"
            ) from exc


def _filters_from_selected_literals(
    literal_bindings: list[LiteralBinding],
    property_bindings: list[PropertyBinding],
) -> list[FilterBinding]:
    selected_properties = {(binding.owner, binding.name) for binding in property_bindings}
    filters: list[FilterBinding] = []
    for literal in literal_bindings:
        if not literal.resolved:
            continue
        if selected_properties and (literal.owner, literal.property) not in selected_properties:
            continue
        filters.append(
            FilterBinding(
                owner=literal.owner,
                property=literal.property,
                operator="eq",
                raw_literal=literal.raw_literal,
                value=literal.value,
                literal=literal,
            )
        )
    return filters


def _projection_property_owner(item: Mapping[str, Any]) -> str | None:
    property_ref = item.get("property")
    if isinstance(property_ref, Mapping):
        owner = property_ref.get("owner")
        return str(owner) if owner else None
    owner = item.get("owner")
    return str(owner) if owner else None


def _normalize_reference_item(item: Mapping[str, Any], *, field_name: str) -> dict[str, Any]:
    shorthand = _single_semantic_reference(item)
    if shorthand is not None:
        semantic_type, semantic_id = shorthand
        if semantic_type == "vertex":
            return {"semantic_type": "vertex", "name": semantic_id}
        if semantic_type == "property" and "." in semantic_id:
            owner, name = semantic_id.split(".", 1)
            return {"semantic_type": "property", "owner": owner, "name": name}

    binding = item.get("binding")
    if isinstance(binding, Mapping):
        normalized = _normalize_reference_item(binding, field_name=field_name)
        if item.get("alias") is not None:
            normalized["alias"] = item["alias"]
        return normalized

    normalized = dict(item)
    property_ref = normalized.get("property")
    if isinstance(property_ref, str) and "." in property_ref:
        owner, name = property_ref.split(".", 1)
        normalized["property"] = {"owner": owner, "name": name}
    semantic_type = normalized.get("semantic_type")
    if semantic_type == "vertex":
        reference = {"semantic_type": "vertex", "name": _extract_name(normalized, "vertex")}
        if normalized.get("alias") is not None:
            reference["alias"] = normalized["alias"]
        return reference
    if semantic_type == "vertex_full":
        reference = {"semantic_type": "vertex_full", "name": _extract_name(normalized, "vertex")}
        if normalized.get("alias") is not None:
            reference["alias"] = normalized["alias"]
        return reference
    for shorthand_type in ("vertex", "edge", "metric", "path_pattern"):
        if shorthand_type in normalized:
            reference = {
                "semantic_type": shorthand_type,
                "name": _extract_name(normalized, shorthand_type),
            }
            if normalized.get("alias") is not None:
                reference["alias"] = normalized["alias"]
            return reference
    if semantic_type == "property":
        owner, name = _extract_owner_name(normalized)
        reference = {"semantic_type": "property", "owner": owner, "name": name}
        for key in ("alias", "projection_terms"):
            if normalized.get(key) is not None:
                reference[key] = normalized[key]
        return reference
    return normalized


def _single_semantic_reference(item: Mapping[str, Any]) -> tuple[str, str] | None:
    if len(item) != 1:
        return None
    key = next(iter(item))
    if not isinstance(key, str) or ":" not in key:
        return None
    semantic_type, semantic_id = key.split(":", 1)
    if semantic_type not in {"vertex", "property", "edge", "metric", "path_pattern"} or not semantic_id:
        return None
    return semantic_type, semantic_id


class _CandidateIndex:
    def __init__(self, candidates: Iterable[SemanticCandidate]) -> None:
        self._by_key: dict[tuple[str, str], CandidateBinding] = {}
        for candidate in candidates:
            binding = _candidate_binding(candidate)
            self._by_key[(candidate.semantic_type, candidate.semantic_id)] = binding

    def require(self, semantic_type: str, semantic_id: str) -> CandidateBinding:
        try:
            return self._by_key[(semantic_type, semantic_id)]
        except KeyError as exc:
            raise BindingValidationError(
                f"cannot bind {semantic_type} {semantic_id}: not present in candidate set"
            ) from exc

    def get(self, semantic_type: str, semantic_id: str) -> CandidateBinding | None:
        return self._by_key.get((semantic_type, semantic_id))


def _coerce_candidates(
    candidates: CandidateRetrievalResult | Sequence[SemanticCandidate] | Mapping[str, Any],
) -> list[SemanticCandidate]:
    if isinstance(candidates, CandidateRetrievalResult):
        return list(candidates.candidates)
    if isinstance(candidates, Mapping):
        return [SemanticCandidate.model_validate(candidate) for candidate in candidates.get("candidates", [])]
    return [
        candidate if isinstance(candidate, SemanticCandidate) else SemanticCandidate.model_validate(candidate)
        for candidate in candidates
    ]


def _candidate_binding(candidate: SemanticCandidate) -> CandidateBinding:
    return CandidateBinding(
        semantic_type=candidate.semantic_type,
        semantic_id=candidate.semantic_id,
        semantic_name=candidate.semantic_name,
        score=candidate.score,
        match_type=candidate.match_type,
        owner=candidate.owner,
        evidence=[evidence.model_dump() for evidence in candidate.evidence],
        metadata=candidate.metadata,
    )


def _inferred_edge_candidate(edge: Any) -> CandidateBinding:
    return CandidateBinding(
        semantic_type="edge",
        semantic_id=edge.name,
        semantic_name=edge.name,
        score=1.0,
        match_type="semantic_inference",
        owner=None,
        evidence=[
            {
                "term": f"{edge.from_vertex}->{edge.to_vertex}",
                "source": "semantic_model.edge_connects",
                "matched_text": edge.name,
            }
        ],
        metadata={
            "from_vertex": edge.from_vertex,
            "to_vertex": edge.to_vertex,
        },
    )


def _inferred_vertex_candidate(name: str) -> CandidateBinding:
    return CandidateBinding(
        semantic_type="vertex",
        semantic_id=name,
        semantic_name=name,
        score=1.0,
        match_type="semantic_inference",
        owner=None,
        evidence=[
            {
                "term": name,
                "source": "semantic_model.edge_endpoint",
                "matched_text": name,
            }
        ],
        metadata={},
    )


def _selected_vertices_are_subset(
    selected: list[VertexBinding],
    inferred: list[VertexBinding],
) -> bool:
    inferred_names = {binding.name for binding in inferred}
    return all(binding.name in inferred_names for binding in selected)


def _selected_items(grounded: Mapping[str, Any], *keys: str) -> list[Any]:
    items: list[Any] = []
    for key in keys:
        value = grounded.get(key, [])
        if value is None:
            continue
        if isinstance(value, list | tuple):
            items.extend(value)
        else:
            items.append(value)
    return items


def _projection_from_selected_bindings(grounded: Mapping[str, Any]) -> list[dict[str, Any]]:
    projection: list[dict[str, Any]] = []
    for item in _coerce_dict_list(grounded.get("selected_bindings", []), "selected_bindings"):
        role = str(item.get("role") or "").strip()
        if not any(token in role for token in ("projection", "return", "field")):
            continue
        semantic_type = str(item.get("semantic_type") or "").strip()
        if semantic_type == "vertex":
            projection.append(
                {
                    "semantic_type": "vertex_full",
                    "name": _extract_name(item, "vertex"),
                    "alias": item.get("alias") or _snake_case(_extract_name(item, "vertex")),
                }
            )
            continue
        if semantic_type == "vertex_full":
            projection.append(_normalize_reference_item(item, field_name="projection"))
            continue
        if semantic_type == "property":
            projection.append(_normalize_reference_item(item, field_name="projection"))
    return projection


def _extract_name(item: Any, object_type: str) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, SemanticCandidate):
        return item.semantic_id
    if isinstance(item, Mapping):
        for key in ("name", "semantic_id", "semantic_name", object_type):
            value = item.get(key)
            if value:
                return str(value)
    raise BindingValidationError(f"cannot extract {object_type} name from {item!r}")


def _extract_edge_direction(item: Any) -> str:
    if not isinstance(item, Mapping):
        return "forward"
    value = item.get("direction") or item.get("edge_direction") or item.get("traversal_direction") or "forward"
    direction = str(value)
    if direction == "reverse":
        direction = "backward"
    if direction not in {"forward", "backward"}:
        raise BindingValidationError(f"edge direction must be forward or backward, got {value!r}")
    return direction


def _extract_owner_name(item: Any) -> tuple[str, str]:
    if isinstance(item, str):
        if "." not in item:
            raise BindingValidationError(f"property binding must be qualified as owner.name: {item}")
        owner, name = item.split(".", 1)
        return owner, name
    if isinstance(item, SemanticCandidate):
        if item.owner is None:
            raise BindingValidationError(f"property candidate is missing owner: {item.semantic_id}")
        return item.owner, item.semantic_name
    if isinstance(item, Mapping):
        shorthand = _single_semantic_reference(item)
        if shorthand is not None:
            semantic_type, semantic_id = shorthand
            if semantic_type == "property" and "." in semantic_id:
                owner, name = semantic_id.split(".", 1)
                return owner, name
        binding = item.get("binding")
        if isinstance(binding, Mapping):
            return _extract_owner_name(binding)
        nested_property = item.get("property")
        if isinstance(nested_property, Mapping):
            owner = nested_property.get("owner")
            name = nested_property.get("name") or nested_property.get("property_name")
            if owner and name:
                return str(owner), str(name)
        owner = item.get("owner") or item.get("expected_vertex") or item.get("expected_edge")
        name = item.get("name") or item.get("property") or item.get("property_name")
        if isinstance(name, str) and "." in name and owner is None:
            owner, name = name.split(".", 1)
        semantic_id = item.get("semantic_id")
        if (owner is None or name is None) and isinstance(semantic_id, str) and "." in semantic_id:
            owner, name = semantic_id.split(".", 1)
        if owner and name:
            return str(owner), str(name)
    raise BindingValidationError(f"cannot extract property owner/name from {item!r}")


def _extract_filter_owner_name(
    item: Mapping[str, Any],
    literal_bindings: list[LiteralBinding],
    property_bindings: list[PropertyBinding],
) -> tuple[str, str]:
    try:
        return _extract_owner_name(item)
    except BindingValidationError as exc:
        property_name = _extract_property_name_shorthand(item)
        if property_name is None:
            raise
        raw_literal = _extract_raw_literal(item, None)
        literal_owners = {
            literal.owner
            for literal in literal_bindings
            if literal.owner is not None
            and literal.property == property_name
            and _literal_matches_raw_value(literal, raw_literal)
        }
        if len(literal_owners) == 1:
            return next(iter(literal_owners)), property_name

        property_owners = {binding.owner for binding in property_bindings if binding.name == property_name}
        if len(property_owners) == 1:
            return next(iter(property_owners)), property_name
        raise exc


def _extract_property_name_shorthand(item: Mapping[str, Any]) -> str | None:
    property_value = item.get("property") or item.get("property_name") or item.get("name")
    if isinstance(property_value, str) and "." not in property_value:
        return property_value
    return None


def _coerce_literal_result(item: Any) -> LiteralResolverResult:
    if isinstance(item, LiteralResolverResult):
        return item
    return LiteralResolverResult.model_validate(item)


def _resolved_literal_value(result: LiteralResolverResult) -> Any | None:
    if not result.resolved:
        return None
    if result.normalized_value is not None:
        return result.normalized_value
    return result.resolved_value


def _literal_from_filter(item: Mapping[str, Any]) -> LiteralBinding | None:
    literal_payload = item.get("literal") or item.get("literal_result")
    if literal_payload is None:
        return None
    if not isinstance(literal_payload, Mapping) or "expected_property" not in literal_payload:
        return None
    result = _coerce_literal_result(literal_payload)
    owner = result.expected_vertex or result.expected_edge
    return LiteralBinding(
        raw_literal=result.raw_literal,
        resolved=result.resolved,
        value=_resolved_literal_value(result),
        normalized_value=result.normalized_value,
        match_type=result.match_type,
        confidence=result.confidence,
        owner=owner,
        property=result.expected_property,
        evidence=result.evidence,
        alternatives=result.alternatives,
        requires_user_choice=result.requires_user_choice,
        value_index_miss=result.value_index_miss,
        error_code=result.error_code,
    )


def _validate_filter_literal_match(
    item: Mapping[str, Any],
    literal: LiteralBinding,
    *,
    owner: str,
    property_name: str,
) -> None:
    raw_literal = _extract_raw_literal(item, literal)
    if literal.owner != owner or literal.property != property_name:
        raise BindingValidationError(
            f"literal result {literal.owner}.{literal.property} does not match filter {owner}.{property_name}"
        )
    if raw_literal is not None and literal.raw_literal != raw_literal:
        raise BindingValidationError(
            f"literal result raw_literal {literal.raw_literal!r} does not match filter raw_literal {raw_literal!r}"
        )


def _extract_raw_literal(item: Mapping[str, Any], inline_literal: LiteralBinding | None) -> str | None:
    raw_literal = item.get("raw_literal") or item.get("literal_text")
    if raw_literal is not None:
        return str(raw_literal)
    if inline_literal is not None:
        return inline_literal.raw_literal
    literal_payload = item.get("literal")
    if isinstance(literal_payload, Mapping) and literal_payload.get("raw_literal") is not None:
        return str(literal_payload["raw_literal"])
    shorthand = _single_semantic_reference(item)
    if shorthand is not None:
        value = item[next(iter(item))]
        if _is_scalar_literal(value):
            return str(value)
    value = item.get("value") or item.get("resolved_value") or item.get("normalized_value")
    if _is_scalar_literal(value):
        return str(value)
    return None


def _is_scalar_literal(value: Any) -> bool:
    return isinstance(value, str | int | float | bool)


def _literal_matches_raw_value(literal: LiteralBinding, raw_literal: str | None) -> bool:
    if raw_literal is None:
        return True
    literal_values = {
        literal.raw_literal,
        None if literal.value is None else str(literal.value),
        None if literal.normalized_value is None else str(literal.normalized_value),
    }
    return raw_literal in literal_values


def _find_literal_binding(
    literal_bindings: list[LiteralBinding],
    owner: str,
    property_name: str,
    raw_literal: str | None,
) -> LiteralBinding | None:
    for literal in literal_bindings:
        if literal.owner == owner and literal.property == property_name:
            if _literal_matches_raw_value(literal, raw_literal):
                return literal
    return None


def _looks_like_property_reference(item: Mapping[str, Any]) -> bool:
    return any(key in item for key in ("owner", "property", "property_name")) or (
        isinstance(item.get("semantic_id"), str) and "." in item["semantic_id"]
    )


def _validate_source_reference(item: Mapping[str, Any], *, field_name: str) -> None:
    value = item.get("source")
    if not isinstance(value, str) or "." not in value or value.startswith(".") or value.endswith("."):
        raise BindingValidationError(f"{field_name} source must be a namespaced reference, got {value!r}")
    disallowed = {"name", "semantic_name", "semantic_id", "semantic_type", "owner", "property", "property_name"}
    extras = sorted(disallowed.intersection(item))
    if extras:
        raise BindingValidationError(f"{field_name} source reference must not include {extras}")


def _validate_dimension_reference(item: Mapping[str, Any], *, field_name: str) -> None:
    alias = item.get("alias")
    target = item.get("target")
    property_ref = item.get("property")
    if not isinstance(alias, str) or not alias:
        raise BindingValidationError(f"{field_name} dimension must include alias")
    if not isinstance(target, str) or not target:
        raise BindingValidationError(f"{field_name} dimension must include target")
    if not isinstance(property_ref, Mapping):
        raise BindingValidationError(f"{field_name} dimension must include property owner/name")
    owner = property_ref.get("owner")
    name = property_ref.get("name") or property_ref.get("property_name")
    if not owner or not name:
        raise BindingValidationError(f"{field_name} dimension must include property owner/name")


def _validate_measure_reference(item: Mapping[str, Any], *, field_name: str) -> None:
    alias = item.get("alias")
    function = item.get("function")
    target = item.get("target")
    property_ref = item.get("property")
    if not isinstance(alias, str) or not alias:
        raise BindingValidationError(f"{field_name} measure must include alias")
    if function not in {"count", "sum", "avg", "min", "max"}:
        raise BindingValidationError(f"{field_name} measure has unsupported function {function!r}")
    if not isinstance(target, str) or not target:
        raise BindingValidationError(f"{field_name} measure must include target")
    if not isinstance(property_ref, Mapping):
        raise BindingValidationError(f"{field_name} measure must include property owner/name")
    owner = property_ref.get("owner")
    name = property_ref.get("name") or property_ref.get("property_name")
    if not owner or not name:
        raise BindingValidationError(f"{field_name} measure must include property owner/name")


def _coerce_dict_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        raise BindingValidationError(f"{field_name} must be a list")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise BindingValidationError(f"{field_name} item must be a mapping, got {item!r}")
        result.append(dict(item))
    return result


def _coerce_assumptions(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        value = [value]
    assumptions: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            assumptions.append(dict(item))
        else:
            assumptions.append({"type": "upstream_assumption", "message": str(item)})
    return assumptions


def _normalize_query_shape(value: Any) -> str:
    raw = "vertex_lookup" if value is None else str(value)
    return _QUERY_SHAPE_ALIASES.get(raw, raw)


def _normalize_operator(value: Any) -> str:
    raw = str(value)
    normalized = _OPERATOR_ALIASES.get(raw)
    if normalized is None:
        raise BindingValidationError(f"unsupported filter operator {raw}")
    return normalized


def _snake_case(value: str) -> str:
    text = value.replace("-", "_")
    text = "".join(f"_{char.lower()}" if char.isupper() else char for char in text)
    return text.strip("_")


def _append_unique_named(bindings: list[Any], binding: Any) -> list[Any]:
    if any(existing.name == binding.name for existing in bindings):
        return bindings
    return [*bindings, binding]


def _append_unique_edge(
    bindings: list[EdgeBinding],
    binding: EdgeBinding,
) -> list[EdgeBinding]:
    if any(existing.name == binding.name and existing.direction == binding.direction for existing in bindings):
        return bindings
    return [*bindings, binding]


def _append_unique_property(
    bindings: list[PropertyBinding],
    binding: PropertyBinding,
) -> list[PropertyBinding]:
    if any(existing.owner == binding.owner and existing.name == binding.name for existing in bindings):
        return bindings
    return [*bindings, binding]
