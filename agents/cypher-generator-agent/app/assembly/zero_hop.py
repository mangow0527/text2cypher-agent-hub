from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry, RegistryLookupError


@dataclass(frozen=True)
class ZeroHopAssemblyResult:
    success: bool
    dsl: dict[str, Any] | None = None
    fallback_reason: str | None = None


class ZeroHopAssembler:
    def __init__(self, registry: GraphSemanticRegistry) -> None:
        self.registry = registry

    def assemble(
        self,
        shape: str,
        candidates: Sequence[Any],
        structural_requirements: Mapping[str, Any],
        literals: Sequence[Mapping[str, Any]] | None = None,
    ) -> ZeroHopAssemblyResult:
        vertex_result = self._unique_vertex(candidates)
        if isinstance(vertex_result, ZeroHopAssemblyResult):
            return vertex_result
        vertex_name = vertex_result

        normalized_shape = _shape_family(shape)
        if normalized_shape == "F1":
            return self._assemble_f1(vertex_name, candidates, structural_requirements)
        if normalized_shape == "F2":
            return self._assemble_f2(vertex_name, candidates, structural_requirements, literals or [])
        if normalized_shape == "F3":
            return self._assemble_f3(vertex_name, candidates, structural_requirements, literals or [])
        return _fallback("unsupported_zero_hop_shape")

    def _assemble_f1(
        self,
        vertex_name: str,
        candidates: Sequence[Any],
        structural_requirements: Mapping[str, Any],
    ) -> ZeroHopAssemblyResult:
        projection = []
        for item in _projection_requirements(structural_requirements):
            vertex_full_name = _vertex_full_name(item)
            if vertex_full_name is not None:
                if vertex_full_name != vertex_name:
                    return _fallback("projection_owner_mismatch")
                projection.append(
                    _vertex_full_projection_item(
                        vertex_name,
                        item.get("alias") if isinstance(item, Mapping) else None,
                        _projection_terms(item),
                    )
                )
                continue
            property_name = _property_name(item)
            if property_name is None:
                return _fallback("missing_projection_property")
            property_ref = self._unique_property(candidates, property_name)
            if isinstance(property_ref, ZeroHopAssemblyResult):
                return property_ref
            owner, name = property_ref
            if owner != vertex_name:
                return _fallback("projection_owner_mismatch")
            projection.append(
                _projection_item(
                    owner,
                    name,
                    item.get("alias") if isinstance(item, Mapping) else None,
                    _projection_terms(item),
                )
            )

        if not projection:
            return _fallback("missing_projection_property")

        return _success(
            _vertex_lookup_dsl(
                vertex_name,
                projection=projection,
                limit=_limit_value(structural_requirements),
            )
        )

    def _assemble_f2(
        self,
        vertex_name: str,
        candidates: Sequence[Any],
        structural_requirements: Mapping[str, Any],
        literals: Sequence[Mapping[str, Any]],
    ) -> ZeroHopAssemblyResult:
        filter_items = _as_list(structural_requirements.get("filters"))
        if len(filter_items) != 1:
            return _fallback("missing_filter_property")

        filter_property_name = _property_name(filter_items[0])
        if filter_property_name is None:
            return _fallback("missing_filter_property")

        filter_property = self._unique_property(candidates, filter_property_name)
        if isinstance(filter_property, ZeroHopAssemblyResult):
            return _fallback("ambiguous_filter_property" if filter_property.fallback_reason == "ambiguous_projection_property" else filter_property.fallback_reason or "invalid_filter_property")
        filter_owner, filter_name = filter_property
        if filter_owner != vertex_name:
            return _fallback("filter_owner_mismatch")

        literal = _unique_literal(literals, filter_owner, filter_name)
        if literal is None:
            return _fallback("missing_filter_literal")
        operator = _operator(filter_items[0])
        if operator is None:
            return _fallback("unsupported_filter_operator")

        projection = []
        for item in _projection_requirements(structural_requirements):
            vertex_full_name = _vertex_full_name(item)
            if vertex_full_name is not None:
                if vertex_full_name != vertex_name:
                    return _fallback("projection_owner_mismatch")
                projection.append(
                    _vertex_full_projection_item(
                        vertex_name,
                        item.get("alias") if isinstance(item, Mapping) else None,
                        _projection_terms(item),
                    )
                )
                continue
            property_name = _property_name(item)
            if property_name is None:
                return _fallback("missing_projection_property")
            property_ref = self._unique_property(candidates, property_name)
            if isinstance(property_ref, ZeroHopAssemblyResult):
                return property_ref
            owner, name = property_ref
            if owner != vertex_name:
                return _fallback("projection_owner_mismatch")
            projection.append(
                _projection_item(
                    owner,
                    name,
                    item.get("alias") if isinstance(item, Mapping) else None,
                    _projection_terms(item),
                )
            )

        if not projection:
            return _fallback("missing_projection_property")

        filters = [
            {
                "target": "target",
                "property": {"owner": filter_owner, "name": filter_name},
                "operator": operator,
                "value": {
                    "raw": literal.get("raw"),
                    "normalized": literal.get("normalized", literal.get("value")),
                    "resolver_match_type": literal.get("resolver_match_type", literal.get("match_type")),
                },
            }
        ]
        return _success(
            _vertex_lookup_dsl(
                vertex_name,
                filters=filters,
                projection=projection,
                limit=_limit_value(structural_requirements),
            )
        )

    def _assemble_f3(
        self,
        vertex_name: str,
        candidates: Sequence[Any],
        structural_requirements: Mapping[str, Any],
        literals: Sequence[Mapping[str, Any]],
    ) -> ZeroHopAssemblyResult:
        if any(structural_requirements.get(key) for key in ("group_by", "order_by", "order", "sort", "limit")):
            return _fallback("unsupported_f3_modifier")

        aggregate = structural_requirements.get("aggregate") or structural_requirements.get("measure") or {}
        if not isinstance(aggregate, Mapping) or aggregate.get("function", "count") != "count":
            return _fallback("unsupported_f3_aggregate")

        measure_specs = _as_list(aggregate.get("measures")) or [aggregate]
        measures: list[dict[str, Any]] = []
        projection_items: list[dict[str, Any]] = []
        for measure_spec in measure_specs:
            if not isinstance(measure_spec, Mapping) or measure_spec.get("function", "count") != "count":
                return _fallback("unsupported_f3_aggregate")
            aggregate_property = _property_name(measure_spec)
            if aggregate_property is None:
                aggregate_property = self.registry.get_vertex(vertex_name).id_property
            property_ref = self._unique_property(
                candidates,
                aggregate_property,
                allow_registry_id=True,
                vertex_name=vertex_name,
            )
            if isinstance(property_ref, ZeroHopAssemblyResult):
                return property_ref
            owner, name = property_ref
            if owner != vertex_name:
                return _fallback("aggregate_owner_mismatch")
            alias = str(measure_spec.get("alias") or f"{_snake_case(vertex_name)}_count")
            measures.append(
                {
                    "alias": alias,
                    "function": "count",
                    "target": "target",
                    "property": {"owner": owner, "name": name},
                }
            )
            projection_terms = _projection_terms(measure_spec)
            projection_item = {"alias": alias, "source": f"measure.{alias}"}
            if projection_terms:
                projection_item["projection_terms"] = projection_terms
            projection_items.append(projection_item)

        filters = []
        for item in _as_list(structural_requirements.get("filters")):
            filter_property_name = _property_name(item)
            if filter_property_name is None:
                return _fallback("missing_filter_property")
            filter_property = self._unique_property(candidates, filter_property_name)
            if isinstance(filter_property, ZeroHopAssemblyResult):
                return _fallback("ambiguous_filter_property" if filter_property.fallback_reason == "ambiguous_projection_property" else filter_property.fallback_reason or "invalid_filter_property")
            filter_owner, filter_name = filter_property
            if filter_owner != vertex_name:
                return _fallback("filter_owner_mismatch")
            operator = _operator(item)
            if operator is None:
                return _fallback("unsupported_filter_operator")
            if operator == "is_not_null":
                filters.append(
                    {
                        "target": "target",
                        "property": {"owner": filter_owner, "name": filter_name},
                        "operator": operator,
                        "value": {
                            "raw": None,
                            "normalized": None,
                            "resolver_match_type": "deterministic_non_null",
                        },
                    }
                )
                continue
            literal = _unique_literal(literals, filter_owner, filter_name)
            if literal is None:
                return _fallback("missing_filter_literal")
            filters.append(
                {
                    "target": "target",
                    "property": {"owner": filter_owner, "name": filter_name},
                    "operator": operator,
                    "value": {
                        "raw": literal.get("raw"),
                        "normalized": literal.get("normalized", literal.get("value")),
                        "resolver_match_type": literal.get("resolver_match_type", literal.get("match_type")),
                    },
                }
            )

        dsl = {
            "schema_version": "restricted_query_dsl_v1",
            "query_id": "zero-hop-f3",
            "query_shape": "ad_hoc_aggregate",
            "source_question": "",
            "bindings": {"target": {"vertex_name": vertex_name}},
            "operations": [
                {
                    "op": "aggregate",
                    "group_by": [],
                    "measures": measures,
                }
            ],
            "filters": filters,
            "projection": {"items": projection_items},
        }
        return _success(dsl)

    def _unique_vertex(self, candidates: Sequence[Any]) -> str | ZeroHopAssemblyResult:
        vertices = [_candidate_name(candidate) for candidate in candidates if _candidate_type(candidate) == "vertex"]
        vertices = [name for name in vertices if name is not None]
        if len(set(vertices)) != 1:
            return _fallback("ambiguous_vertex_candidate")
        vertex_name = vertices[0]
        try:
            self.registry.get_vertex(vertex_name)
        except RegistryLookupError:
            return _fallback("unknown_vertex_candidate")
        return vertex_name

    def _unique_property(
        self,
        candidates: Sequence[Any],
        property_name: str,
        *,
        allow_registry_id: bool = False,
        vertex_name: str | None = None,
    ) -> tuple[str, str] | ZeroHopAssemblyResult:
        matches = [
            (owner, name)
            for candidate in candidates
            if _candidate_type(candidate) == "property"
            for owner, name in [_candidate_property(candidate)]
            if owner is not None and name == property_name
        ]
        unique_matches = sorted(set(matches))
        if not unique_matches and allow_registry_id and vertex_name is not None:
            try:
                self.registry.get_property(vertex_name, property_name)
            except RegistryLookupError:
                return _fallback("unknown_projection_property")
            return vertex_name, property_name
        if len(unique_matches) != 1:
            return _fallback("ambiguous_projection_property")
        owner, name = unique_matches[0]
        try:
            self.registry.get_property(owner, name)
        except RegistryLookupError:
            return _fallback("unknown_projection_property")
        return owner, name


def _success(dsl: dict[str, Any]) -> ZeroHopAssemblyResult:
    return ZeroHopAssemblyResult(success=True, dsl=dsl)


def _fallback(reason: str) -> ZeroHopAssemblyResult:
    return ZeroHopAssemblyResult(success=False, fallback_reason=reason)


def _vertex_lookup_dsl(
    vertex_name: str,
    *,
    filters: list[dict[str, Any]] | None = None,
    projection: list[dict[str, Any]],
    limit: int | None = None,
) -> dict[str, Any]:
    operations = []
    if limit is not None:
        operations.append({"op": "limit", "value": limit})
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "zero-hop",
        "query_shape": "vertex_lookup",
        "source_question": "",
        "bindings": {"target": {"vertex_name": vertex_name}},
        "operations": operations,
        "filters": filters or [],
        "projection": {"items": projection},
    }


def _limit_value(structural_requirements: Mapping[str, Any]) -> int | None:
    raw_limit = structural_requirements.get("limit")
    if isinstance(raw_limit, Mapping):
        raw_limit = raw_limit.get("value")
    try:
        parsed = int(raw_limit)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _projection_item(
    owner: str,
    property_name: str,
    alias: Any | None,
    projection_terms: Sequence[str] | None = None,
) -> dict[str, Any]:
    item = {
        "target": "target",
        "property": {"owner": owner, "name": property_name},
    }
    if alias is not None:
        item["alias"] = str(alias)
    terms = [str(term).strip() for term in projection_terms or [] if str(term).strip()]
    if terms:
        item["projection_terms"] = terms
    return item


def _vertex_full_projection_item(
    vertex_name: str,
    alias: Any | None,
    projection_terms: Sequence[str] | None = None,
) -> dict[str, Any]:
    item = {
        "target": "target",
        "vertex_full": True,
        "alias": str(alias) if alias is not None else _snake_case(vertex_name),
    }
    terms = [str(term).strip() for term in projection_terms or [] if str(term).strip()]
    if terms:
        item["projection_terms"] = terms
    return item


def _unique_literal(literals: Sequence[Mapping[str, Any]], owner: str, property_name: str) -> Mapping[str, Any] | None:
    matches = [
        literal
        for literal in literals
        if literal.get("owner") == owner and (literal.get("property") or literal.get("name")) == property_name
    ]
    if len(matches) != 1:
        return None
    return matches[0]


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


def _vertex_full_name(item: Any) -> str | None:
    if not isinstance(item, Mapping) or item.get("semantic_type") != "vertex_full":
        return None
    name = item.get("name") or item.get("semantic_id") or item.get("vertex")
    return str(name) if name is not None else None


def _projection_terms(item: Any) -> list[str]:
    if not isinstance(item, Mapping):
        return []
    raw_terms = item.get("projection_terms")
    if not isinstance(raw_terms, list | tuple):
        return []
    return [str(term).strip() for term in raw_terms if str(term).strip()]


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
    "is_not_null": "is_not_null",
    "非空": "is_not_null",
    "不为空": "is_not_null",
    "不为空值": "is_not_null",
}


def _projection_requirements(structural_requirements: Mapping[str, Any]) -> list[Any]:
    projection = _as_list(structural_requirements.get("projection"))
    if projection:
        return projection
    return _as_list(structural_requirements.get("projection_terms"))


def _shape_family(shape: str) -> str:
    normalized = str(shape).strip().upper()
    if normalized.startswith("F1"):
        return "F1"
    if normalized.startswith("F2"):
        return "F2"
    if normalized.startswith("F3"):
        return "F3"
    return normalized


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _get(candidate: Any, key: str) -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(key)
    return getattr(candidate, key, None)


def _snake_case(value: str) -> str:
    chars: list[str] = []
    for index, char in enumerate(value):
        if char.isupper() and index > 0:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)
