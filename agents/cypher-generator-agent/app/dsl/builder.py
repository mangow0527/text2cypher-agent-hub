from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from services.cypher_generator_agent.app.binding.models import (
    BindingPlan,
    FilterBinding,
    LiteralBinding,
)
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry


class RestrictedDslBuilder:
    def __init__(self, registry: GraphSemanticRegistry) -> None:
        self.registry = registry

    def build(
        self,
        plan: BindingPlan,
        *,
        source_question: str,
        query_id: str,
    ) -> dict[str, Any]:
        self._reject_uncompiled_sort_limit(plan)
        if plan.query_shape == "vertex_lookup":
            dsl = self._build_vertex_lookup(plan, source_question=source_question, query_id=query_id)
        elif plan.query_shape == "single_hop_traversal":
            dsl = self._build_single_hop(plan, source_question=source_question, query_id=query_id)
        elif plan.query_shape == "named_path_pattern":
            dsl = self._build_named_path_pattern(plan, source_question=source_question, query_id=query_id)
        elif plan.query_shape == "variable_path_traversal":
            dsl = self._build_variable_path(plan, source_question=source_question, query_id=query_id)
        elif plan.query_shape == "metric_aggregate":
            dsl = self._build_metric_aggregate(plan, source_question=source_question, query_id=query_id)
        elif plan.query_shape == "ad_hoc_aggregate":
            dsl = self._build_ad_hoc_aggregate(plan, source_question=source_question, query_id=query_id)
        elif plan.query_shape == "top_n":
            dsl = self._build_top_n(plan, source_question=source_question, query_id=query_id)
        elif plan.query_shape == "two_step_aggregate":
            dsl = self._build_two_step_aggregate(plan, source_question=source_question, query_id=query_id)
        else:
            raise ValueError(f"unsupported query_shape for restricted DSL builder: {plan.query_shape}")

        return dsl

    def _build_vertex_lookup(
        self,
        plan: BindingPlan,
        *,
        source_question: str,
        query_id: str,
    ) -> dict[str, Any]:
        if len(plan.vertex_bindings) != 1:
            raise ValueError("vertex_lookup requires exactly one target vertex")

        target = plan.vertex_bindings[0]
        role_by_owner = {target.name: "target"}

        dsl = self._base_payload(plan, source_question=source_question, query_id=query_id)
        dsl["bindings"] = {"target": {"vertex_name": target.name}}
        if not plan.projection:
            vertex = self.registry.get_vertex(target.name)
            plan.projection.append(
                {
                    "semantic_type": "property",
                    "owner": target.name,
                    "name": vertex.id_property,
                    "alias": f"{_snake_case(target.name)}_{vertex.id_property}",
                }
            )
        self._add_filters_projection_sort_limit(dsl, plan, role_by_owner=role_by_owner, include_filters=True)
        return dsl

    def _reject_uncompiled_sort_limit(self, plan: BindingPlan) -> None:
        compiled_sort_limit_shapes = {"metric_aggregate", "ad_hoc_aggregate", "top_n", "two_step_aggregate"}
        if plan.query_shape == "vertex_lookup" and not plan.sort:
            return
        if plan.query_shape not in compiled_sort_limit_shapes and (plan.sort or plan.limit is not None):
            raise ValueError(
                f"{plan.query_shape} sort/limit is not supported until the Cypher compiler consumes it"
            )

    def _build_single_hop(
        self,
        plan: BindingPlan,
        *,
        source_question: str,
        query_id: str,
    ) -> dict[str, Any]:
        if len(plan.vertex_bindings) < 2 or not plan.edge_bindings:
            raise ValueError("single_hop_traversal requires start/end vertices and one edge")
        if len(plan.edge_bindings) > 1:
            return self._build_traversal_chain(
                plan,
                source_question=source_question,
                query_id=query_id,
            )

        start = plan.vertex_bindings[0]
        end = plan.vertex_bindings[1]
        edge = plan.edge_bindings[0]
        role_by_owner = {start.name: "start", end.name: "end"}
        if not plan.projection:
            vertex = self.registry.get_vertex(end.name)
            plan.projection.append(
                {
                    "semantic_type": "property",
                    "owner": end.name,
                    "name": vertex.id_property,
                    "alias": f"{_snake_case(end.name)}_{vertex.id_property}",
                }
            )

        dsl = self._base_payload(plan, source_question=source_question, query_id=query_id)
        dsl["bindings"] = {
            "start": {"vertex_name": start.name},
            "edge": {"edge_name": edge.name},
            "end": {"vertex_name": end.name},
        }
        dsl["operations"] = [
            {
                "op": "traverse_edge",
                "from": "start",
                "edge": "edge",
                "to": "end",
                "direction": edge.direction,
            }
        ]
        self._add_filters_projection_sort_limit(dsl, plan, role_by_owner=role_by_owner, include_filters=True)
        return dsl

    def _build_traversal_chain(
        self,
        plan: BindingPlan,
        *,
        source_question: str,
        query_id: str,
    ) -> dict[str, Any]:
        if len(plan.vertex_bindings) != len(plan.edge_bindings) + 1:
            raise ValueError("single_hop_traversal chain requires one more vertex than edge")

        role_by_owner = {
            binding.name: f"v{index}"
            for index, binding in enumerate(plan.vertex_bindings)
        }
        dsl = self._base_payload(plan, source_question=source_question, query_id=query_id)
        dsl["bindings"] = {
            **{
                f"v{index}": {"vertex_name": binding.name}
                for index, binding in enumerate(plan.vertex_bindings)
            },
            **{
                f"e{index}": {"edge_name": binding.name}
                for index, binding in enumerate(plan.edge_bindings)
            },
        }
        dsl["operations"] = [
            {
                "op": "traverse_edge",
                "from": f"v{index}",
                "edge": f"e{index}",
                "to": f"v{index + 1}",
                "direction": edge.direction,
            }
            for index, edge in enumerate(plan.edge_bindings)
        ]
        if not plan.projection:
            vertex = self.registry.get_vertex(plan.vertex_bindings[-1].name)
            plan.projection.append(
                {
                    "semantic_type": "property",
                    "owner": plan.vertex_bindings[-1].name,
                    "name": vertex.id_property,
                    "alias": f"{_snake_case(plan.vertex_bindings[-1].name)}_{vertex.id_property}",
                }
            )
        self._add_filters_projection_sort_limit(dsl, plan, role_by_owner=role_by_owner, include_filters=True)
        return dsl

    def _build_named_path_pattern(
        self,
        plan: BindingPlan,
        *,
        source_question: str,
        query_id: str,
    ) -> dict[str, Any]:
        if not plan.path_pattern_bindings:
            raise ValueError("named_path_pattern requires one path pattern binding")

        primary_vertex = plan.vertex_bindings[0] if plan.vertex_bindings else None
        path_pattern_name = plan.path_pattern_bindings[0].name
        dsl = self._base_payload(plan, source_question=source_question, query_id=query_id)
        dsl["bindings"] = (
            {"primary_vertex": {"vertex_name": primary_vertex.name}}
            if primary_vertex is not None
            else {}
        )
        dsl["operations"] = [
            {
                "op": "use_path_pattern",
                "path_pattern_name": path_pattern_name,
                "bind_as": "path",
                "parameters": self._path_pattern_parameters(
                    plan,
                    path_pattern_name,
                    primary_vertex.name if primary_vertex else None,
                ),
            }
        ]
        self._add_filters_projection_sort_limit(dsl, plan, role_by_owner={}, include_filters=False)
        return dsl

    def _build_variable_path(
        self,
        plan: BindingPlan,
        *,
        source_question: str,
        query_id: str,
    ) -> dict[str, Any]:
        if len(plan.vertex_bindings) < 2:
            raise ValueError("variable_path_traversal requires start/through vertices")
        if len(plan.edge_bindings) != 1:
            raise ValueError("variable_path_traversal requires exactly one edge binding")

        start = plan.vertex_bindings[0]
        through = plan.vertex_bindings[1]
        edge = plan.edge_bindings[0]
        role_by_owner = {start.name: "start", through.name: "through"}
        through_filters = [item for item in plan.filters if item.owner == through.name]
        remaining_filters = [item for item in plan.filters if item.owner != through.name]

        dsl = self._base_payload(plan, source_question=source_question, query_id=query_id)
        dsl["bindings"] = {
            "start": {"vertex_name": start.name},
            "through": {"vertex_name": through.name},
        }
        dsl["operations"] = [
            {
                "op": "variable_path",
                "bind_as": "path",
                "start": "start",
                "through": {
                    "vertex_ref": "through",
                    "filters": [self._filter_item(item, role_by_owner) for item in through_filters],
                },
                "allowed_edges": [edge.name],
                "min_hops": 1,
                "max_hops": 8,
            }
        ]
        if remaining_filters:
            dsl["filters"] = [self._filter_item(item, role_by_owner) for item in remaining_filters]
        dsl["projection"] = {"items": self._projection_items(plan, role_by_owner)}
        return dsl

    def _build_metric_aggregate(
        self,
        plan: BindingPlan,
        *,
        source_question: str,
        query_id: str,
    ) -> dict[str, Any]:
        if len(plan.metric_bindings) != 1:
            raise ValueError("metric_aggregate requires exactly one metric binding")

        metric_name = plan.metric_bindings[0].name
        metric = self.registry.get_metric(metric_name)
        alias_by_owner = {
            owner: alias for alias, owner in _metric_aliases(metric.pattern or "").items()
        }

        dsl = self._base_payload(plan, source_question=source_question, query_id=query_id)
        dsl["bindings"] = {"metric": {"metric_name": metric_name}}
        dsl["operations"] = [
            {
                "op": "metric_aggregate",
                "metric_name": metric_name,
                "group_by": [self._dimension_item(item, alias_by_owner) for item in plan.group_by],
                "filters": [self._filter_item(item, alias_by_owner) for item in plan.filters],
            }
        ]
        dsl["projection"] = {"items": self._metric_projection_items(plan, metric_name)}
        self._append_sort_limit(dsl, plan)
        return dsl

    def _build_ad_hoc_aggregate(
        self,
        plan: BindingPlan,
        *,
        source_question: str,
        query_id: str,
    ) -> dict[str, Any]:
        if not plan.measures:
            raise ValueError("ad_hoc_aggregate requires at least one measure")

        role_by_owner = self._aggregate_role_by_owner(plan)
        selected_vertices = {binding.name for binding in plan.vertex_bindings}
        missing_vertices = sorted(owner for owner in role_by_owner if owner not in selected_vertices)
        if missing_vertices:
            raise ValueError(f"ad_hoc_aggregate missing vertex bindings for {missing_vertices}")

        dsl = self._base_payload(plan, source_question=source_question, query_id=query_id)
        dsl["bindings"] = {
            alias: {"vertex_name": owner}
            for owner, alias in sorted(role_by_owner.items(), key=lambda item: item[1])
        }
        dsl["operations"] = [
            {
                "op": "aggregate",
                "group_by": [self._dimension_item(item, role_by_owner) for item in plan.group_by],
                "measures": [self._measure_item(item, role_by_owner) for item in plan.measures],
            }
        ]
        if plan.filters:
            dsl["filters"] = [self._filter_item(item, role_by_owner) for item in plan.filters]
        dsl["projection"] = {"items": self._aggregate_projection_items(plan)}
        self._append_sort_limit(dsl, plan)
        return dsl

    def _build_top_n(
        self,
        plan: BindingPlan,
        *,
        source_question: str,
        query_id: str,
    ) -> dict[str, Any]:
        if not plan.sort or plan.limit is None:
            raise ValueError("top_n requires sort and limit")
        if plan.metric_bindings:
            return self._build_metric_aggregate(plan, source_question=source_question, query_id=query_id)
        return self._build_ad_hoc_aggregate(plan, source_question=source_question, query_id=query_id)

    def _build_two_step_aggregate(
        self,
        plan: BindingPlan,
        *,
        source_question: str,
        query_id: str,
    ) -> dict[str, Any]:
        if not plan.measures:
            raise ValueError("two_step_aggregate requires at least one measure")

        role_by_owner = self._aggregate_role_by_owner(plan)
        selected_vertices = {binding.name for binding in plan.vertex_bindings}
        missing_vertices = sorted(owner for owner in role_by_owner if owner not in selected_vertices)
        if missing_vertices:
            raise ValueError(f"two_step_aggregate missing vertex bindings for {missing_vertices}")

        bind_as = self._subquery_bind_as(plan)
        dsl = self._base_payload(plan, source_question=source_question, query_id=query_id)
        dsl["bindings"] = {
            alias: {"vertex_name": owner}
            for owner, alias in sorted(role_by_owner.items(), key=lambda item: item[1])
        }
        dsl["operations"] = [
            {
                "op": "subquery",
                "bind_as": bind_as,
                "query_shape": "ad_hoc_aggregate",
                "group_by": [self._dimension_item(item, role_by_owner) for item in plan.group_by],
                "measures": [self._measure_item(item, role_by_owner) for item in plan.measures],
            }
        ]
        dsl["projection"] = {"items": self._two_step_projection_items(plan, bind_as)}
        self._append_sort_limit(
            dsl,
            plan,
            source_namespace_overrides={"group": bind_as, "measure": bind_as},
        )
        return dsl

    def _base_payload(
        self,
        plan: BindingPlan,
        *,
        source_question: str,
        query_id: str,
    ) -> dict[str, Any]:
        dsl: dict[str, Any] = {
            "schema_version": "restricted_query_dsl_v1",
            "query_id": query_id,
            "query_shape": plan.query_shape,
            "source_question": source_question,
            "bindings": {},
            "operations": [],
        }
        if plan.assumptions:
            dsl["assumptions"] = plan.assumptions
        return dsl

    def _add_filters_projection_sort_limit(
        self,
        dsl: dict[str, Any],
        plan: BindingPlan,
        *,
        role_by_owner: dict[str, str],
        include_filters: bool,
    ) -> None:
        if include_filters:
            filters = [self._filter_item(item, role_by_owner) for item in plan.filters]
            if filters:
                dsl["filters"] = filters

        dsl["projection"] = {"items": self._projection_items(plan, role_by_owner)}

        if plan.sort:
            dsl["operations"].append({"op": "sort", "by": [self._sort_item(item) for item in plan.sort]})
        if plan.limit is not None:
            dsl["operations"].append({"op": "limit", "value": plan.limit})

    def _filter_item(
        self,
        item: FilterBinding,
        role_by_owner: dict[str, str],
    ) -> dict[str, Any]:
        filter_item: dict[str, Any] = {
            "property": {"owner": item.owner, "name": item.property},
            "operator": item.operator,
            "value": self._value_from_filter(item),
        }
        target = role_by_owner.get(item.owner)
        if target is not None:
            filter_item["target"] = target
        return filter_item

    def _projection_items(
        self,
        plan: BindingPlan,
        role_by_owner: dict[str, str],
    ) -> list[dict[str, Any]]:
        return [self._projection_item(item, role_by_owner) for item in plan.projection]

    def _projection_item(
        self,
        item: Mapping[str, Any],
        role_by_owner: dict[str, str],
    ) -> dict[str, Any]:
        if "source" in item:
            projection = {"source": item["source"]}
            if item.get("alias") is not None:
                projection["alias"] = item["alias"]
            _copy_projection_terms(item, projection)
            return projection

        property_ref = item.get("property")
        if isinstance(property_ref, Mapping):
            projection = {
                "target": self._projection_target(item, property_ref, role_by_owner),
                "property": {"owner": property_ref["owner"], "name": property_ref["name"]},
            }
            if item.get("alias") is not None:
                projection["alias"] = item["alias"]
            _copy_projection_terms(item, projection)
            return projection

        if item.get("semantic_type") == "vertex":
            raise ValueError(
                "ambiguous bare vertex projection is not allowed; use property projection or vertex_full"
            )

        if item.get("semantic_type") == "vertex_full":
            vertex_name = str(item["name"])
            target = role_by_owner.get(vertex_name)
            if target is None:
                raise ValueError(f"projection owner {vertex_name} is not bound in query roles")
            projection = {
                "alias": item.get("alias") or _snake_case(vertex_name),
                "target": target,
                "vertex_full": True,
            }
            _copy_projection_terms(item, projection)
            return projection

        if item.get("semantic_type") == "property":
            owner = str(item["owner"])
            name = str(item["name"])
            target = role_by_owner.get(owner)
            if target is None:
                raise ValueError(f"projection owner {owner} is not bound in query roles")
            projection = {
                "alias": item.get("alias") or name,
                "target": target,
                "property": {"owner": owner, "name": name},
            }
            _copy_projection_terms(item, projection)
            return projection

        raise ValueError(f"unsupported projection item for restricted DSL builder: {item!r}")

    def _projection_target(
        self,
        item: Mapping[str, Any],
        property_ref: Mapping[str, Any],
        role_by_owner: dict[str, str],
    ) -> str:
        target = item.get("target")
        if target in role_by_owner.values():
            return str(target)
        owner = str(property_ref["owner"])
        owner_target = role_by_owner.get(owner)
        if owner_target is None:
            raise ValueError(f"projection owner {owner} is not bound in query roles")
        return owner_target

    def _sort_item(
        self,
        item: Mapping[str, Any],
        *,
        source_namespace_overrides: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if "source" not in item:
            raise ValueError(f"restricted DSL sort item requires a source reference: {item!r}")
        return {
            "source": _normalize_source_namespace(str(item["source"]), source_namespace_overrides or {}),
            "direction": item.get("direction", "asc"),
        }

    def _dimension_item(
        self,
        item: Mapping[str, Any],
        role_by_owner: dict[str, str],
    ) -> dict[str, Any]:
        property_ref = _mapping_property(item)
        owner = str(property_ref["owner"])
        target = str(item.get("target") or role_by_owner[owner])
        return {
            "alias": str(item["alias"]),
            "target": target,
            "property": {"owner": owner, "name": str(property_ref["name"])},
        }

    def _measure_item(
        self,
        item: Mapping[str, Any],
        role_by_owner: dict[str, str],
    ) -> dict[str, Any]:
        property_ref = _mapping_property(item)
        owner = str(property_ref["owner"])
        target = str(item.get("target") or role_by_owner[owner])
        return {
            "alias": str(item["alias"]),
            "function": str(item["function"]),
            "target": target,
            "property": {"owner": owner, "name": str(property_ref["name"])},
        }

    def _aggregate_role_by_owner(self, plan: BindingPlan) -> dict[str, str]:
        role_by_owner: dict[str, str] = {}
        for item in [*plan.group_by, *plan.measures, *plan.projection]:
            if not isinstance(item, Mapping):
                continue
            property_ref = item.get("property")
            if not isinstance(property_ref, Mapping):
                continue
            owner = property_ref.get("owner")
            target = item.get("target")
            if owner and target:
                role_by_owner[str(owner)] = str(target)
        for binding in plan.vertex_bindings:
            role_by_owner.setdefault(binding.name, _snake_case(binding.name))
        return role_by_owner

    def _metric_projection_items(
        self,
        plan: BindingPlan,
        metric_name: str,
    ) -> list[dict[str, Any]]:
        if plan.projection:
            return self._projection_items(plan, {})
        items = [{"alias": str(item["alias"]), "source": f"group.{item['alias']}"} for item in plan.group_by]
        items.append({"alias": metric_name, "source": f"metric.{metric_name}"})
        return items

    def _aggregate_projection_items(self, plan: BindingPlan) -> list[dict[str, Any]]:
        if plan.projection:
            return self._aggregate_output_projection_items(plan, source_namespace_by_kind={})
        return [
            *({"alias": str(item["alias"]), "source": f"group.{item['alias']}"} for item in plan.group_by),
            *({"alias": str(item["alias"]), "source": f"measure.{item['alias']}"} for item in plan.measures),
        ]

    def _two_step_projection_items(self, plan: BindingPlan, bind_as: str) -> list[dict[str, Any]]:
        if plan.projection:
            return self._aggregate_output_projection_items(
                plan,
                source_namespace_by_kind={"group": bind_as, "measure": bind_as},
            )
        return [
            *({"alias": str(item["alias"]), "source": f"{bind_as}.{item['alias']}"} for item in plan.group_by),
            *({"alias": str(item["alias"]), "source": f"{bind_as}.{item['alias']}"} for item in plan.measures),
        ]

    def _aggregate_output_projection_items(
        self,
        plan: BindingPlan,
        *,
        source_namespace_by_kind: Mapping[str, str],
    ) -> list[dict[str, Any]]:
        outputs = _aggregate_outputs(plan)
        items: list[dict[str, Any]] = []
        for item in plan.projection:
            if "source" in item:
                projection = {"source": _normalize_source_namespace(str(item["source"]), source_namespace_by_kind)}
                if item.get("alias") is not None:
                    projection["alias"] = item["alias"]
                _copy_projection_terms(item, projection)
                items.append(projection)
                continue

            property_ref = _projection_property_ref(item)
            if property_ref is None:
                raise ValueError(f"aggregate projection must reference group/measure output: {item!r}")

            selected = _select_aggregate_output(property_ref, item, outputs)
            namespace = source_namespace_by_kind.get(selected["kind"], selected["kind"])
            projection = {
                "alias": str(item.get("alias") or selected["alias"]),
                "source": f"{namespace}.{selected['alias']}",
            }
            _copy_projection_terms(item, projection)
            items.append(projection)
        return items

    def _append_sort_limit(
        self,
        dsl: dict[str, Any],
        plan: BindingPlan,
        *,
        source_namespace_overrides: Mapping[str, str] | None = None,
    ) -> None:
        if plan.sort:
            dsl["operations"].append(
                {
                    "op": "sort",
                    "by": [
                        self._sort_item(item, source_namespace_overrides=source_namespace_overrides)
                        for item in plan.sort
                    ],
                }
            )
        if plan.limit is not None:
            dsl["operations"].append({"op": "limit", "value": plan.limit})

    def _subquery_bind_as(self, plan: BindingPlan) -> str:
        for item in [*plan.projection, *plan.sort]:
            source = item.get("source") if isinstance(item, Mapping) else None
            if not isinstance(source, str) or "." not in source:
                continue
            namespace = source.split(".", 1)[0]
            if namespace not in {"group", "measure", "metric"}:
                return namespace
        return "subquery"

    def _value_from_filter(self, item: FilterBinding) -> dict[str, Any]:
        if item.literal is not None:
            return self._value_from_literal(item.literal)
        return {
            "raw": item.raw_literal,
            "normalized": item.value,
            "resolver_match_type": None,
        }

    def _value_from_literal(self, literal: LiteralBinding) -> dict[str, Any]:
        return {
            "raw": literal.raw_literal,
            "normalized": literal.normalized_value if literal.normalized_value is not None else literal.value,
            "resolver_match_type": literal.match_type,
        }

    def _path_pattern_parameters(
        self,
        plan: BindingPlan,
        path_pattern_name: str,
        primary_vertex_name: str | None,
    ) -> dict[str, Any]:
        path_pattern = self.registry.get_path_pattern(path_pattern_name)
        parameters: dict[str, Any] = {}
        consumed_filters: set[int] = set()
        for parameter in path_pattern.parameters:
            literal = self._literal_for_parameter(plan, parameter.name, primary_vertex_name)
            if literal is not None:
                parameters[parameter.name] = self._value_from_literal(literal)
                consumed_filters.update(
                    self._filter_indices_for_parameter(plan, parameter.name, primary_vertex_name)
                )
                continue

            filter_index, filter_item = self._filter_for_parameter(plan, parameter.name, primary_vertex_name)
            if filter_item is not None:
                parameters[parameter.name] = self._value_from_filter(filter_item)
                if filter_index is not None:
                    consumed_filters.add(filter_index)
        self._raise_on_unrepresented_path_filters(plan, consumed_filters, path_pattern_name)
        return parameters

    def _literal_for_parameter(
        self,
        plan: BindingPlan,
        parameter_name: str,
        primary_vertex_name: str | None,
    ) -> LiteralBinding | None:
        for literal in plan.literal_bindings:
            if self._matches_parameter(parameter_name, literal.owner, literal.property, primary_vertex_name):
                return literal
        for filter_item in plan.filters:
            if filter_item.literal is not None and self._matches_parameter(
                parameter_name,
                filter_item.owner,
                filter_item.property,
                primary_vertex_name,
            ):
                return filter_item.literal
        return None

    def _filter_for_parameter(
        self,
        plan: BindingPlan,
        parameter_name: str,
        primary_vertex_name: str | None,
    ) -> tuple[int | None, FilterBinding | None]:
        for index, filter_item in enumerate(plan.filters):
            if self._filter_matches_parameter(filter_item, parameter_name, primary_vertex_name):
                return index, filter_item
        return None, None

    def _filter_indices_for_parameter(
        self,
        plan: BindingPlan,
        parameter_name: str,
        primary_vertex_name: str | None,
    ) -> set[int]:
        return {
            index
            for index, filter_item in enumerate(plan.filters)
            if self._filter_matches_parameter(filter_item, parameter_name, primary_vertex_name)
        }

    def _filter_matches_parameter(
        self,
        filter_item: FilterBinding,
        parameter_name: str,
        primary_vertex_name: str | None,
    ) -> bool:
        return filter_item.operator == "eq" and self._matches_parameter(
            parameter_name,
            filter_item.owner,
            filter_item.property,
            primary_vertex_name,
        )

    def _raise_on_unrepresented_path_filters(
        self,
        plan: BindingPlan,
        consumed_filters: set[int],
        path_pattern_name: str,
    ) -> None:
        unrepresented = [
            f"{filter_item.owner}.{filter_item.property}"
            for index, filter_item in enumerate(plan.filters)
            if index not in consumed_filters
        ]
        if unrepresented:
            raise ValueError(
                f"named_path_pattern {path_pattern_name} filters cannot be represented "
                f"by template parameters: {', '.join(unrepresented)}"
            )

    def _matches_parameter(
        self,
        parameter_name: str,
        owner: str | None,
        property_name: str,
        primary_vertex_name: str | None,
    ) -> bool:
        if owner is None:
            return parameter_name == property_name

        expected_names = {property_name, f"{_snake_case(owner)}_{property_name}"}
        if primary_vertex_name == owner:
            id_property = self.registry.get_vertex(owner).id_property
            if property_name == id_property:
                expected_names.add(f"{_snake_case(owner)}_id")
        return parameter_name in expected_names


def _copy_projection_terms(source: Mapping[str, Any], target: dict[str, Any]) -> None:
    raw_terms = source.get("projection_terms")
    if not isinstance(raw_terms, list | tuple):
        return
    terms = [str(term).strip() for term in raw_terms if str(term).strip()]
    if terms:
        target["projection_terms"] = terms


def _snake_case(value: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return value.replace("-", "_").lower()


def _metric_aliases(pattern: str) -> dict[str, str]:
    return dict(re.findall(r"\(([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\)", pattern))


def _mapping_property(item: Mapping[str, Any]) -> Mapping[str, Any]:
    property_ref = item.get("property")
    if not isinstance(property_ref, Mapping):
        raise ValueError(f"aggregate item requires property owner/name: {item!r}")
    owner = property_ref.get("owner")
    name = property_ref.get("name") or property_ref.get("property_name")
    if not owner or not name:
        raise ValueError(f"aggregate item requires property owner/name: {item!r}")
    return {"owner": owner, "name": name}


def _projection_property_ref(item: Mapping[str, Any]) -> Mapping[str, str] | None:
    property_ref = item.get("property")
    if isinstance(property_ref, Mapping):
        owner = property_ref.get("owner")
        name = property_ref.get("name") or property_ref.get("property_name")
        if owner and name:
            return {"owner": str(owner), "name": str(name)}
        raise ValueError(f"aggregate projection property requires owner/name: {item!r}")
    if item.get("semantic_type") == "property":
        owner = item.get("owner")
        name = item.get("name")
        if owner and name:
            return {"owner": str(owner), "name": str(name)}
        raise ValueError(f"aggregate projection property requires owner/name: {item!r}")
    return None


def _aggregate_outputs(plan: BindingPlan) -> list[dict[str, str]]:
    outputs: list[dict[str, str]] = []
    for kind, values in (("group", plan.group_by), ("measure", plan.measures)):
        for item in values:
            property_ref = _mapping_property(item)
            outputs.append(
                {
                    "kind": kind,
                    "alias": str(item["alias"]),
                    "owner": str(property_ref["owner"]),
                    "name": str(property_ref["name"]),
                }
            )
    return outputs


def _select_aggregate_output(
    property_ref: Mapping[str, str],
    projection: Mapping[str, Any],
    outputs: list[Mapping[str, str]],
) -> Mapping[str, str]:
    owner = str(property_ref["owner"])
    name = str(property_ref["name"])
    matches = [
        item
        for item in outputs
        if item.get("owner") == owner and item.get("name") == name
    ]
    alias = projection.get("alias")
    if alias is not None:
        alias_matches = [item for item in matches if item.get("alias") == str(alias)]
        if len(alias_matches) == 1:
            return alias_matches[0]
        if len(alias_matches) > 1:
            raise ValueError(f"ambiguous aggregate projection output for alias {alias!r}")
        if len(matches) == 1:
            return matches[0]
    elif len(matches) == 1:
        return matches[0]

    if not matches:
        raise ValueError(
            f"aggregate projection property {owner}.{name} does not reference group_by or measure output"
        )
    raise ValueError(f"ambiguous aggregate projection property {owner}.{name}")


def _normalize_source_namespace(source: str, overrides: Mapping[str, str]) -> str:
    if "." not in source:
        return source
    namespace, name = source.split(".", 1)
    return f"{overrides.get(namespace, namespace)}.{name}"
