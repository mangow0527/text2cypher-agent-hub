from __future__ import annotations

from copy import deepcopy
from typing import Any

from services.cypher_generator_agent.app.binding.models import BindingPlan
from services.cypher_generator_agent.app.dsl.models import RestrictedQueryDslModel
from services.cypher_generator_agent.app.repair.fingerprint import (
    canonical_payload,
    canonicalize_state,
    from_binding_plan,
    from_dsl,
)


def test_confidence_reason_raw_text_and_candidate_order_do_not_change_fingerprint() -> None:
    state = {
        "query_shape": "single_hop_traversal",
        "vertices": [
            {"role": "start", "name": "Service", "confidence": 0.91},
            {"role": "end", "name": "Tunnel", "confidence": 0.88},
        ],
        "edges": [
            {
                "role": "uses",
                "name": "SERVICE_USES_TUNNEL",
                "direction": "forward",
                "reason": "selected by LLM",
            }
        ],
        "candidates": [
            {"id": "Tunnel", "name": "Tunnel"},
            {"id": "Path", "name": "Path"},
        ],
        "raw_llm_output": "free-form chain of thought should not matter",
        "duration_ms": 123,
        "token_usage": {"prompt": 50, "completion": 7},
        "stage_id": "semantic_validator:1",
    }
    changed_only_by_ignored_fields = deepcopy(state)
    changed_only_by_ignored_fields["vertices"][0]["confidence"] = 0.42
    changed_only_by_ignored_fields["edges"][0]["reason"] = "different explanation"
    changed_only_by_ignored_fields["candidates"] = list(reversed(state["candidates"]))
    changed_only_by_ignored_fields["raw_llm_output"] = "different raw text"
    changed_only_by_ignored_fields["duration_ms"] = 999
    changed_only_by_ignored_fields["token_usage"] = {"prompt": 1, "completion": 1}
    changed_only_by_ignored_fields["stage_id"] = "semantic_validator:2"

    assert canonicalize_state(state) == canonicalize_state(changed_only_by_ignored_fields)


def test_binding_plan_confidence_change_does_not_change_fingerprint() -> None:
    plan = _binding_plan()
    changed_confidence = deepcopy(plan)
    changed_confidence["vertex_bindings"][0]["candidate"]["score"] = 0.11
    changed_confidence["edge_bindings"][0]["candidate"]["score"] = 0.12

    assert from_binding_plan(plan) == from_binding_plan(changed_confidence)


def test_binding_plan_vertex_order_changes_fingerprint_when_roles_are_absent() -> None:
    plan = _binding_plan()
    swapped = deepcopy(plan)
    swapped["vertex_bindings"] = list(reversed(swapped["vertex_bindings"]))

    assert from_binding_plan(plan) != from_binding_plan(swapped)


def test_real_binding_plan_literal_binding_values_do_not_change_structural_fingerprint() -> None:
    plan = BindingPlan.model_validate(_binding_plan() | {"literal_bindings": [_literal_binding("ne-0001")]})
    changed = BindingPlan.model_validate(_binding_plan() | {"literal_bindings": [_literal_binding("ne-0002")]})

    assert from_binding_plan(plan) == from_binding_plan(changed)


def test_real_restricted_dsl_traverse_endpoints_change_but_projection_order_does_not_change_fingerprint() -> None:
    base = RestrictedQueryDslModel.model_validate(_single_hop_dsl())

    endpoint_changed_payload = _single_hop_dsl()
    endpoint_changed_payload["operations"][0]["from"] = "end"
    endpoint_changed_payload["operations"][0]["to"] = "start"
    endpoint_changed = RestrictedQueryDslModel.model_validate(endpoint_changed_payload)

    projection_changed_payload = _single_hop_dsl()
    projection_changed_payload["projection"]["items"] = list(
        reversed(projection_changed_payload["projection"]["items"])
    )
    projection_changed = RestrictedQueryDslModel.model_validate(projection_changed_payload)

    assert from_dsl(base) != from_dsl(endpoint_changed)
    assert from_dsl(base) == from_dsl(projection_changed)


def test_dsl_fingerprint_ignores_edge_direction_for_same_structural_skeleton() -> None:
    base = _single_hop_dsl()
    direction_changed = deepcopy(base)
    direction_changed["operations"][0]["direction"] = "backward"

    assert from_dsl(direction_changed) == from_dsl(base)


def test_dsl_fingerprint_ignores_group_by_property_and_sort_target_identity() -> None:
    base = _two_step_aggregate_dsl()

    group_property_changed = deepcopy(base)
    group_property_changed["operations"][0]["group_by"][0]["property"] = {
        "owner": "NetworkElement",
        "name": "location",
    }

    sort_target_changed = deepcopy(base)
    sort_target_changed["operations"][2]["by"][0]["source"] = "device_port_counts.other_count"

    assert from_dsl(group_property_changed) == from_dsl(base)
    assert from_dsl(sort_target_changed) == from_dsl(base)


def test_two_step_aggregate_subquery_payload_keeps_nested_aggregate_fields_inside_subquery() -> None:
    payload = canonical_payload(_dsl_payload_for_test(_two_step_aggregate_dsl()))

    assert payload.get("groups") is None
    assert payload.get("measures") is None
    assert payload["subqueries"][0]["fingerprint_payload"]["groups"] == [
        {
            "alias": "device",
            "position": 0,
            "property": {"name": "id", "owner": "NetworkElement"},
            "target": "device",
        }
    ]
    assert payload["subqueries"][0]["fingerprint_payload"]["measures"] == [
        {
            "alias": "port_count",
            "function": "count",
            "position": 0,
            "property": {"name": "id", "owner": "Port"},
            "target": "port",
        }
    ]


def test_two_step_aggregate_value_changes_do_not_change_structural_fingerprint() -> None:
    base = _two_step_aggregate_dsl()

    measure_changed = deepcopy(base)
    measure_changed["operations"][0]["measures"][0]["function"] = "sum"

    filter_subquery_changed = deepcopy(base)
    filter_subquery_changed["operations"][1]["predicate"]["value"] = 20

    sort_changed = deepcopy(base)
    sort_changed["operations"][2]["by"][0]["direction"] = "asc"

    limit_changed = deepcopy(base)
    limit_changed["operations"][3]["value"] = 10

    base_fingerprint = from_dsl(base)

    assert from_dsl(measure_changed) == base_fingerprint
    assert from_dsl(filter_subquery_changed) == base_fingerprint
    assert from_dsl(sort_changed) == base_fingerprint
    assert from_dsl(limit_changed) == base_fingerprint


def test_two_step_aggregate_measure_identity_does_not_change_structural_fingerprint() -> None:
    base = _two_step_aggregate_dsl()
    service_count_changed = deepcopy(base)
    service_count_changed["operations"][0]["measures"][0]["alias"] = "service_count"
    service_count_changed["operations"][0]["measures"][0]["property"] = {
        "owner": "Service",
        "name": "id",
    }
    service_count_changed["operations"][0]["measures"][0]["target"] = "service"

    assert from_dsl(service_count_changed) == from_dsl(base)


def test_use_path_pattern_parameters_change_fingerprint() -> None:
    base = _path_pattern_dsl()
    changed = deepcopy(base)
    changed["operations"][0]["parameters"]["min_hops"] = 2

    assert from_dsl(base) != from_dsl(changed)


def test_variable_path_through_filters_do_not_change_structural_fingerprint() -> None:
    base = _variable_path_dsl()
    changed = deepcopy(base)
    changed["operations"][0]["through"]["filters"][0]["value"] = "ne-0002"

    assert from_dsl(base) == from_dsl(changed)


def test_real_restricted_dsl_variable_path_parameters_change_fingerprint() -> None:
    base = RestrictedQueryDslModel.model_validate(_variable_path_dsl())

    changed_payload = _variable_path_dsl()
    changed_payload["operations"][0]["bind_as"] = "alt_path"
    bind_changed = RestrictedQueryDslModel.model_validate(changed_payload)

    changed_payload = _variable_path_dsl()
    changed_payload["operations"][0]["start"] = "device"
    start_changed = RestrictedQueryDslModel.model_validate(changed_payload)

    changed_payload = _variable_path_dsl()
    changed_payload["operations"][0]["through"]["vertex_ref"] = "tunnel"
    through_changed = RestrictedQueryDslModel.model_validate(changed_payload)

    changed_payload = _variable_path_dsl()
    changed_payload["operations"][0]["min_hops"] = 2
    min_hops_changed = RestrictedQueryDslModel.model_validate(changed_payload)

    changed_payload = _variable_path_dsl()
    changed_payload["operations"][0]["max_hops"] = 7
    max_hops_changed = RestrictedQueryDslModel.model_validate(changed_payload)

    base_fingerprint = from_dsl(base)
    assert from_dsl(bind_changed) == base_fingerprint
    assert from_dsl(start_changed) != base_fingerprint
    assert from_dsl(through_changed) != base_fingerprint
    assert from_dsl(min_hops_changed) != base_fingerprint
    assert from_dsl(max_hops_changed) != base_fingerprint


def _dsl_payload_for_test(dsl: dict[str, Any]) -> dict[str, Any]:
    from services.cypher_generator_agent.app.repair.fingerprint import _dsl_payload

    return _dsl_payload(dsl)


def _binding_plan() -> dict[str, Any]:
    return {
        "schema_version": "binding_plan_v1",
        "query_shape": "single_hop_traversal",
        "vertex_bindings": [
            {
                "name": "Service",
                "candidate": {
                    "semantic_type": "vertex",
                    "semantic_id": "Service",
                    "semantic_name": "Service",
                    "score": 0.91,
                    "match_type": "exact",
                },
            },
            {
                "name": "Tunnel",
                "candidate": {
                    "semantic_type": "vertex",
                    "semantic_id": "Tunnel",
                    "semantic_name": "Tunnel",
                    "score": 0.88,
                    "match_type": "exact",
                },
            },
        ],
        "edge_bindings": [
            {
                "name": "SERVICE_USES_TUNNEL",
                "direction": "forward",
                "candidate": {
                    "semantic_type": "edge",
                    "semantic_id": "SERVICE_USES_TUNNEL",
                    "semantic_name": "SERVICE_USES_TUNNEL",
                    "score": 0.82,
                    "match_type": "exact",
                },
            }
        ],
    }


def _two_step_aggregate_dsl() -> dict[str, Any]:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-two-step",
        "query_shape": "two_step_aggregate",
        "source_question": "端口数量最多的设备",
        "bindings": {},
        "operations": [
            {
                "op": "subquery",
                "bind_as": "device_port_counts",
                "query_shape": "ad_hoc_aggregate",
                "bindings": {
                    "device": {"vertex_name": "NetworkElement"},
                    "port": {"vertex_name": "Port"},
                    "device_ports": {"edge_name": "HAS_PORT"},
                },
                "operations": [
                    {
                        "op": "traverse_edge",
                        "from": "device",
                        "edge": "device_ports",
                        "to": "port",
                        "direction": "forward",
                    }
                ],
                "group_by": [
                    {
                        "alias": "device",
                        "target": "device",
                        "property": {"owner": "NetworkElement", "name": "id"},
                    }
                ],
                "measures": [
                    {
                        "alias": "port_count",
                        "function": "count",
                        "target": "port",
                        "property": {"owner": "Port", "name": "id"},
                    }
                ],
            },
            {
                "op": "filter_subquery",
                "source": "device_port_counts",
                "predicate": {"property": "port_count", "operator": "gt", "value": 10},
            },
            {
                "op": "sort",
                "by": [{"source": "device_port_counts.port_count", "direction": "desc"}],
            },
            {"op": "limit", "value": 5},
        ],
        "projection": {
            "items": [
                {"alias": "device", "source": "device_port_counts.device"},
                {"alias": "port_count", "source": "device_port_counts.port_count"},
            ]
        },
    }


def _literal_binding(normalized_value: str) -> dict[str, Any]:
    return {
        "raw_literal": normalized_value,
        "resolved": True,
        "value": normalized_value,
        "normalized_value": normalized_value,
        "match_type": "exact",
        "confidence": 0.99,
        "owner": "NetworkElement",
        "property": "id",
        "evidence": [],
        "alternatives": [],
        "requires_user_choice": False,
        "value_index_miss": False,
    }


def _single_hop_dsl() -> dict[str, Any]:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-single-hop",
        "query_shape": "single_hop_traversal",
        "source_question": "Gold 级别的服务都用了哪些隧道",
        "bindings": {
            "start": {"vertex_name": "Service"},
            "end": {"vertex_name": "Tunnel"},
            "uses": {"edge_name": "SERVICE_USES_TUNNEL"},
        },
        "operations": [
            {
                "op": "traverse_edge",
                "from": "start",
                "edge": "uses",
                "to": "end",
                "direction": "forward",
            }
        ],
        "projection": {
            "items": [
                {
                    "alias": "service_name",
                    "target": "start",
                    "property": {"owner": "Service", "name": "name"},
                },
                {
                    "alias": "tunnel_name",
                    "target": "end",
                    "property": {"owner": "Tunnel", "name": "name"},
                },
            ]
        },
    }


def _path_pattern_dsl() -> dict[str, Any]:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-path-pattern",
        "query_shape": "named_path_pattern",
        "bindings": {},
        "operations": [
            {
                "op": "use_path_pattern",
                "path_pattern_name": "tunnel_full_path",
                "bind_as": "path",
                "parameters": {"min_hops": 1, "max_hops": 5},
            }
        ],
        "projection": {"items": [{"alias": "path", "source": "path"}]},
    }


def _variable_path_dsl() -> dict[str, Any]:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-variable-path",
        "query_shape": "variable_path_traversal",
        "source_question": "找出所有经过设备 ne-0001 的隧道",
        "bindings": {
            "tunnel": {"vertex_name": "Tunnel"},
            "device": {"vertex_name": "NetworkElement"},
        },
        "operations": [
            {
                "op": "variable_path",
                "bind_as": "path",
                "start": "tunnel",
                "allowed_edges": ["TUNNEL_TRAVERSES_DEVICE"],
                "through": {
                    "vertex_ref": "device",
                    "filters": [
                        {
                            "target": "device",
                            "property": {"owner": "NetworkElement", "name": "id"},
                            "operator": "eq",
                            "value": "ne-0001",
                        }
                    ],
                },
                "min_hops": 1,
                "max_hops": 5,
            }
        ],
        "projection": {"items": [{"alias": "path", "source": "path"}]},
    }
