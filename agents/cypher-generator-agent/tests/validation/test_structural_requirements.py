from __future__ import annotations

from services.cypher_generator_agent.app.validation.structural_requirements import (
    StructuralRequirements,
    derive_structural_requirements,
    validate_dsl_structural_coverage,
)


def test_derives_structural_requirements_from_existing_decomposition_slots() -> None:
    requirements = derive_structural_requirements(
        {
            "schema_version": "question_decomposition_v1",
            "original_question": "统计服务使用的隧道源节点所在位置的网元数量，按数量降序排列，返回前3名。",
            "intent_type": "top_n",
            "output_shape": "grouped_rows",
            "substantive_terms": [
                {"text": "3", "slot": "limit"},
                {"text": "降序", "slot": "order_by"},
                {"text": "位置", "slot": "group_by", "attached_to": "网元"},
                {"text": "数量", "slot": "projection"},
                {"text": "隧道", "slot": "path"},
                {"text": "服务", "slot": "path"},
                {"text": "使用", "slot": "path"},
                {"text": "源节点", "slot": "path"},
            ],
        }
    )

    payload = requirements.model_dump(mode="json")
    assert payload["requires_aggregate"] is True
    assert payload["requires_group_by"] is True
    assert payload["requires_order_by"] is True
    assert payload["order_direction"] == "desc"
    assert payload["requires_limit"] == {"required": True, "value": 3}
    assert [term["text"] for term in payload["path_terms"]] == ["服务", "使用", "隧道", "源节点"]
    assert payload["path_order_confidence"] == "high"
    assert payload["min_path_hops"] == 2
    assert payload["projection_terms"] == ["数量"]


def test_top_n_with_group_order_limit_requires_aggregate_even_when_output_shape_is_rows() -> None:
    requirements = derive_structural_requirements(
        {
            "schema_version": "question_decomposition_v1",
            "original_question": "按隧道ID统计服务数量，按数量降序排列，返回前3名。",
            "intent_type": "top_n",
            "output_shape": "rows",
            "substantive_terms": [
                {"text": "隧道ID", "slot": "group_by", "attached_to": "隧道"},
                {"text": "服务数量", "slot": "projection", "attached_to": "服务"},
                {"text": "数量降序", "slot": "order_by"},
                {"text": "前3", "slot": "limit"},
            ],
        }
    )

    assert requirements.requires_aggregate is True


def test_high_to_low_order_phrase_derives_desc_direction() -> None:
    requirements = derive_structural_requirements(
        {
            "schema_version": "question_decomposition_v1",
            "original_question": "按名称分组统计出现次数，并按次数从高到低返回前10个。",
            "intent_type": "top_n",
            "output_shape": "grouped_rows",
            "substantive_terms": [
                {"text": "名称", "slot": "group_by"},
                {"text": "出现次数", "slot": "projection"},
                {"text": "次数", "slot": "order_by"},
                {"text": "从高到低", "slot": "order_by"},
                {"text": "前10", "slot": "limit"},
            ],
        }
    )

    assert requirements.order_direction == "desc"


def test_scalar_projection_value_does_not_imply_aggregate() -> None:
    requirements = derive_structural_requirements(
        {
            "original_question": "查询名称为 Service_002 的服务的名称。",
            "intent_type": "lookup",
            "output_shape": "scalar",
            "substantive_terms": [
                {"text": "名称", "slot": "filter", "attached_to": "服务"},
                {"text": "Service_002", "slot": "filter", "attached_to": "服务"},
                {"text": "服务", "slot": "path"},
                {"text": "名称", "slot": "projection", "attached_to": "服务"},
            ],
        }
    )

    assert requirements.requires_aggregate is False
    assert requirements.projection_terms == ["名称"]


def test_node_projection_is_required_even_when_limit_is_present() -> None:
    requirements = derive_structural_requirements(
        {
            "original_question": "查询名称为 Service_003 的服务节点，最多返回 3 条记录。",
            "intent_type": "list",
            "output_shape": "rows",
            "substantive_terms": [
                {"text": "名称", "slot": "filter", "attached_to": "服务"},
                {"text": "Service_003", "slot": "filter", "attached_to": "服务"},
                {"text": "服务", "slot": "path"},
                {"text": "节点", "slot": "projection"},
                {"text": "最多", "slot": "limit"},
                {"text": "3", "slot": "limit"},
            ],
        }
    )

    assert requirements.requires_limit.required is True
    assert requirements.requires_limit.value == 3
    assert requirements.projection_terms == ["节点"]


def test_path_term_positions_prefer_longer_non_overlapping_surface_matches() -> None:
    requirements = derive_structural_requirements(
        {
            "original_question": "查询服务质量等级为Gold的服务使用的隧道",
            "intent_type": "list",
            "output_shape": "rows",
            "substantive_terms": [
                {"text": "隧道", "slot": "path"},
                {"text": "服务", "slot": "path"},
                {"text": "服务质量等级", "slot": "filter", "attached_to": "服务"},
                {"text": "使用", "slot": "path"},
            ],
        }
    )

    payload = requirements.model_dump(mode="json")
    assert [term["text"] for term in payload["path_terms"]] == ["服务", "使用", "隧道"]
    service_position = payload["path_terms"][0]["position"]
    assert service_position is not None
    assert service_position > "查询服务质量等级为Gold的".find("服务质量等级")
    assert payload["path_order_confidence"] == "high"


def test_missing_path_positions_mark_low_confidence_and_gate_still_checks_hop_count() -> None:
    requirements = derive_structural_requirements(
        {
            "original_question": "查询服务使用的隧道",
            "intent_type": "list",
            "output_shape": "rows",
            "substantive_terms": [
                {"text": "服务", "slot": "path"},
                {"text": "穿过", "slot": "path"},
                {"text": "隧道", "slot": "path"},
                {"text": "不可见路径词", "slot": "path"},
            ],
        }
    )

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_id": "low-confidence-path",
            "query_shape": "vertex_lookup",
            "source_question": "查询服务使用的隧道",
            "bindings": {"target": {"vertex_name": "Service"}},
            "operations": [],
            "projection": {"items": [{"target": "target", "property": {"owner": "Service", "name": "id"}}]},
        },
    )

    assert requirements.path_order_confidence == "low"
    assert result.is_valid is False
    assert result.missing[0]["code"] == "path_hops_insufficient"
    assert result.missing[0]["details"]["order_checked"] is False


def test_relation_only_path_words_do_not_increase_min_hops() -> None:
    requirements = derive_structural_requirements(
        {
            "original_question": "查询服务与所用隧道之间的关联。",
            "intent_type": "list",
            "output_shape": "rows",
            "substantive_terms": [
                {"text": "服务", "slot": "path"},
                {"text": "所用", "slot": "path"},
                {"text": "隧道", "slot": "path"},
                {"text": "关联", "slot": "path"},
                {"text": "ID", "slot": "projection", "attached_to": "隧道"},
            ],
        }
    )

    assert requirements.min_path_hops == 1


def test_single_hop_with_generic_relation_words_passes_hop_gate() -> None:
    requirements = derive_structural_requirements(
        {
            "original_question": "查询服务与所用隧道之间的关联。",
            "intent_type": "list",
            "output_shape": "rows",
            "substantive_terms": [
                {"text": "服务", "slot": "path"},
                {"text": "所用", "slot": "path"},
                {"text": "隧道", "slot": "path"},
                {"text": "关联", "slot": "path"},
                {"text": "ID", "slot": "projection", "attached_to": "隧道"},
            ],
        }
    )

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_id": "single-hop-relation-words",
            "query_shape": "single_hop_traversal",
            "source_question": "查询服务与所用隧道之间的关联。",
            "bindings": {
                "svc": {"vertex_name": "Service"},
                "edge": {"edge_name": "SERVICE_USES_TUNNEL"},
                "tun": {"vertex_name": "Tunnel"},
            },
            "operations": [
                {
                    "op": "traverse_edge",
                    "from": "svc",
                    "edge": "edge",
                    "to": "tun",
                    "direction": "forward",
                }
            ],
            "projection": {
                "items": [
                    {
                        "alias": "tunnel_id",
                        "target": "tun",
                        "property": {"owner": "Tunnel", "name": "id"},
                        "projection_terms": ["ID"],
                    }
                ]
            },
        },
    )

    assert result.is_valid is True
    assert result.missing == []


def test_relation_phrase_correspondence_does_not_increase_min_hops() -> None:
    requirements = derive_structural_requirements(
        {
            "schema_version": "question_decomposition_v1",
            "original_question": "查询服务和隧道的对应关系。",
            "intent_type": "list",
            "output_shape": "rows",
            "substantive_terms": [
                {"text": "服务", "slot": "path"},
                {"text": "隧道", "slot": "path"},
                {"text": "对应关系", "slot": "path"},
            ],
        }
    )

    assert requirements.min_path_hops == 1


def test_closed_relation_phrases_do_not_increase_min_hops() -> None:
    for relation_phrase in ("连接关系", "关联关系", "对应关系"):
        requirements = derive_structural_requirements(
            {
                "schema_version": "question_decomposition_v1",
                "original_question": f"查询服务与隧道之间的{relation_phrase}。",
                "intent_type": "list",
                "output_shape": "rows",
                "substantive_terms": [
                    {"text": "服务", "slot": "path"},
                    {"text": "隧道", "slot": "path"},
                    {"text": relation_phrase, "slot": "path"},
                ],
            }
        )

        assert requirements.min_path_hops == 1


def test_service_node_text_does_not_create_path_hop_when_it_modifies_service() -> None:
    requirements = derive_structural_requirements(
        {
            "schema_version": "question_decomposition_v1",
            "original_question": "查询所有服务节点的元素类型。",
            "intent_type": "list",
            "output_shape": "rows",
            "substantive_terms": [
                {"text": "服务", "slot": "path"},
                {"text": "节点", "slot": "path"},
                {"text": "元素类型", "slot": "projection"},
            ],
        }
    )

    assert requirements.min_path_hops == 0


def test_gate_keeps_detail_projection_required_even_when_concrete_terms_are_covered() -> None:
    requirements = StructuralRequirements(projection_terms=["名称", "带宽", "详细信息"])

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_shape": "single_hop_traversal",
            "operations": [{"op": "traverse_edge", "from": "svc", "edge": "edge", "to": "tun"}],
            "projection": {
                "items": [
                    {"target": "tun", "property": {"owner": "Tunnel", "name": "name"}},
                    {"target": "tun", "property": {"owner": "Tunnel", "name": "bandwidth"}},
                ]
            },
        },
    )

    assert result.is_valid is False
    assert result.missing[0]["details"]["uncovered"] == ["详细信息"]


def test_gate_keeps_generic_detail_as_required_when_it_is_the_only_projection_request() -> None:
    requirements = StructuralRequirements(projection_terms=["详细信息"])

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_shape": "vertex_lookup",
            "operations": [],
            "projection": {"items": [{"target": "target", "property": {"owner": "Service", "name": "id"}}]},
        },
    )

    assert result.is_valid is False
    assert result.missing[0]["details"]["uncovered"] == ["详细信息"]


def test_gate_does_not_treat_internal_id_as_property_id() -> None:
    requirements = StructuralRequirements(projection_terms=["内部ID", "名称"])

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_shape": "vertex_lookup",
            "operations": [],
            "projection": {
                "items": [
                    {"target": "target", "property": {"owner": "Service", "name": "id"}},
                    {"target": "target", "property": {"owner": "Service", "name": "name"}},
                ]
            },
        },
    )

    assert result.is_valid is False
    assert result.missing[0]["details"]["uncovered"] == ["内部ID"]


def test_gate_does_not_ignore_unlisted_projection_terms() -> None:
    requirements = StructuralRequirements(projection_terms=["端口号"])

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_shape": "vertex_lookup",
            "operations": [],
            "projection": {"items": [{"target": "target", "property": {"owner": "Port", "name": "id"}}]},
        },
    )

    assert result.is_valid is False
    assert result.missing[0]["details"]["uncovered"] == ["端口号"]


def test_gate_reports_missing_aggregate_sort_limit_and_path_hops_without_checking_aliases() -> None:
    requirements = derive_structural_requirements(
        {
            "original_question": "统计服务使用的隧道源节点所在位置的网元数量，按数量降序排列，返回前3名。",
            "intent_type": "top_n",
            "output_shape": "grouped_rows",
            "substantive_terms": [
                {"text": "服务", "slot": "path"},
                {"text": "使用", "slot": "path"},
                {"text": "隧道", "slot": "path"},
                {"text": "源节点", "slot": "path"},
                {"text": "位置", "slot": "group_by", "attached_to": "网元"},
                {"text": "数量", "slot": "projection"},
                {"text": "降序", "slot": "order_by"},
                {"text": "3", "slot": "limit"},
            ],
        }
    )

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_id": "qa_c3e83dd7ad32",
            "query_shape": "single_hop_traversal",
            "source_question": "统计服务使用的隧道源节点所在位置的网元数量，按数量降序排列，返回前3名。",
            "bindings": {
                "start": {"vertex_name": "Tunnel"},
                "edge": {"edge_name": "TUNNEL_SRC"},
                "end": {"vertex_name": "NetworkElement"},
            },
            "operations": [
                {
                    "op": "traverse_edge",
                    "from": "start",
                    "edge": "edge",
                    "to": "end",
                    "direction": "forward",
                }
            ],
            "projection": {
                "items": [
                    {
                        "alias": "network_element_id",
                        "target": "end",
                        "property": {"owner": "NetworkElement", "name": "id"},
                    }
                ]
            },
        },
    )

    assert result.is_valid is False
    assert [item["code"] for item in result.missing] == [
        "aggregate_required",
        "group_by_required",
        "order_by_required",
        "limit_required",
        "path_hops_insufficient",
        "projection_terms_uncovered",
    ]


def test_gate_reports_uncovered_projection_terms_when_projection_is_partial() -> None:
    requirements = derive_structural_requirements(
        {
            "original_question": "查询所有业务使用的隧道的ID、名称和带宽。",
            "intent_type": "list",
            "output_shape": "rows",
            "substantive_terms": [
                {"text": "业务", "slot": "path"},
                {"text": "使用", "slot": "path"},
                {"text": "隧道", "slot": "path"},
                {"text": "ID", "slot": "projection"},
                {"text": "名称", "slot": "projection"},
                {"text": "带宽", "slot": "projection"},
            ],
        }
    )

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_id": "qa_65f6a2d6ec7a",
            "query_shape": "single_hop_traversal",
            "source_question": "查询所有业务使用的隧道的ID、名称和带宽。",
            "bindings": {
                "v0": {"vertex_name": "Service"},
                "edge_0": {"edge_name": "SERVICE_USES_TUNNEL"},
                "v1": {"vertex_name": "Tunnel"},
            },
            "operations": [
                {"op": "traverse_edge", "from": "v0", "edge": "edge_0", "to": "v1", "direction": "forward"}
            ],
            "projection": {
                "items": [
                    {
                        "alias": "tunnel_id",
                        "target": "v1",
                        "property": {"owner": "Tunnel", "name": "id"},
                        "projection_terms": ["ID"],
                    }
                ]
            },
        },
    )

    assert result.is_valid is False
    projection_missing = [item for item in result.missing if item["code"] == "projection_terms_uncovered"]
    assert projection_missing
    assert projection_missing[0]["details"]["uncovered"] == ["名称", "带宽"]


def test_gate_covers_projection_terms_from_alias_or_property_surface_without_projection_terms() -> None:
    requirements = derive_structural_requirements(
        {
            "original_question": "返回隧道的IETF标准、源网元的IP地址、服务时延、网元软件版本和端口节点。",
            "intent_type": "list",
            "output_shape": "rows",
            "substantive_terms": [
                {"text": "IETF标准", "slot": "projection", "attached_to": "隧道"},
                {"text": "IP地址", "slot": "projection", "attached_to": "网元"},
                {"text": "时延", "slot": "projection", "attached_to": "服务"},
                {"text": "软件版本", "slot": "projection", "attached_to": "网元"},
                {"text": "节点", "slot": "projection", "attached_to": "端口"},
            ],
        }
    )

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_id": "projection-surface-fallback",
            "query_shape": "single_hop_traversal",
            "source_question": "返回隧道的IETF标准、源网元的IP地址、服务时延、网元软件版本和端口节点。",
            "bindings": {},
            "operations": [{"op": "traverse_edge", "from": "svc", "edge": "edge", "to": "tun"}],
            "projection": {
                "items": [
                    {
                        "alias": "IETF标准",
                        "property": {"owner": "Tunnel", "name": "ietf_standard"},
                    },
                    {
                        "alias": "IP地址",
                        "property": {"owner": "NetworkElement", "name": "ip_address"},
                    },
                    {
                        "alias": "service_latency",
                        "property": {"owner": "Service", "name": "latency"},
                    },
                    {
                        "alias": "network_element_software_version",
                        "property": {"owner": "NetworkElement", "name": "software_version"},
                    },
                    {
                        "alias": "port",
                        "target": {"vertex_name": "Port"},
                        "vertex_full": True,
                    },
                ]
            },
        },
    )

    assert result.is_valid is True


def test_gate_covers_service_projection_synonyms_without_explicit_projection_terms() -> None:
    requirements = StructuralRequirements(
        projection_terms=["编号", "服务ID", "服务名称", "等级值", "服务质量等级"],
    )

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_shape": "vertex_lookup",
            "bindings": {"target": {"vertex_name": "Service"}},
            "operations": [],
            "projection": {
                "items": [
                    {"target": "target", "property": {"owner": "Service", "name": "id"}},
                    {"target": "target", "property": {"owner": "Service", "name": "name"}},
                    {
                        "target": "target",
                        "property": {"owner": "Service", "name": "quality_of_service"},
                    },
                ]
            },
        },
    )

    assert result.is_valid is True


def test_gate_covers_elem_type_with_network_element_surface() -> None:
    requirements = StructuralRequirements(projection_terms=["网元类型"])

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_shape": "vertex_lookup",
            "bindings": {"target": {"vertex_name": "Service"}},
            "operations": [],
            "projection": {
                "items": [
                    {
                        "target": "target",
                        "property": {"owner": "Service", "name": "elem_type"},
                    }
                ]
            },
        },
    )

    assert result.is_valid is True


def test_gate_covers_vendor_info_projection_surface() -> None:
    requirements = StructuralRequirements(projection_terms=["隧道ID", "网元类型", "厂商信息"])

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_shape": "single_hop_traversal",
            "operations": [{"op": "traverse_edge", "from": "tun", "edge": "edge", "to": "ne"}],
            "projection": {
                "items": [
                    {"target": "tun", "property": {"owner": "Tunnel", "name": "id"}},
                    {"target": "ne", "property": {"owner": "NetworkElement", "name": "elem_type"}},
                    {"target": "ne", "property": {"owner": "NetworkElement", "name": "vendor"}},
                ]
            },
        },
    )

    assert result.is_valid is True


def test_gate_covers_generic_info_when_vendor_projection_is_present() -> None:
    requirements = StructuralRequirements(projection_terms=["时延", "信息"])

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_shape": "path_query",
            "operations": [
                {"op": "traverse_edge", "from": "svc", "edge": "uses", "to": "tun"},
                {"op": "traverse_edge", "from": "tun", "edge": "dst", "to": "ne"},
            ],
            "projection": {
                "items": [
                    {"target": "tun", "property": {"owner": "Tunnel", "name": "latency"}},
                    {"target": "ne", "property": {"owner": "NetworkElement", "name": "vendor"}},
                ]
            },
        },
    )

    assert result.is_valid is True


def test_gate_covers_node_text_for_service_count_measure() -> None:
    requirements = StructuralRequirements(projection_terms=["节点", "总数量"])

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_shape": "ad_hoc_aggregate",
            "operations": [
                {
                    "op": "aggregate",
                    "measures": [
                        {
                            "alias": "service_count",
                            "function": "count",
                            "target": "svc",
                            "property": {"owner": "Service", "name": "id"},
                        }
                    ],
                }
            ],
            "projection": {"items": [{"alias": "service_count", "source": "measure.service_count"}]},
        },
    )

    assert result.is_valid is True


def test_gate_covers_node_text_for_metric_count_projection() -> None:
    requirements = StructuralRequirements(projection_terms=["节点", "总数量"])

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_shape": "metric_aggregate",
            "bindings": {"metric": {"metric_name": "service_count"}},
            "operations": [
                {
                    "op": "metric_aggregate",
                    "metric_name": "service_count",
                    "group_by": [],
                    "filters": [],
                }
            ],
            "projection": {"items": [{"alias": "service_count", "source": "metric.service_count"}]},
        },
    )

    assert result.is_valid is True


def test_derives_and_covers_vertex_full_detail_projection() -> None:
    requirements = derive_structural_requirements(
        {
            "original_question": "查询服务质量等级为金牌的所有服务的详细信息。",
            "intent_type": "list",
            "output_shape": "rows",
            "substantive_terms": [
                {"text": "服务质量等级", "slot": "filter", "attached_to": "服务"},
                {"text": "金牌", "slot": "filter", "attached_to": "服务质量等级"},
                {"text": "服务", "slot": "projection"},
                {"text": "详细信息", "slot": "projection", "attached_to": "服务"},
            ],
        }
    )

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_shape": "vertex_lookup",
            "bindings": {"target": {"vertex_name": "Service"}},
            "operations": [],
            "projection": {
                "items": [
                    {
                        "target": "target",
                        "vertex_full": True,
                        "alias": "service",
                    }
                ]
            },
        },
    )

    assert requirements.projection_terms == ["详细信息"]
    assert result.is_valid is True


def test_gate_accepts_matching_structure_without_requiring_specific_edge_or_alias() -> None:
    requirements = derive_structural_requirements(
        {
            "original_question": "统计服务使用的隧道源节点所在位置的网元数量，按数量降序排列，返回前3名。",
            "intent_type": "top_n",
            "output_shape": "grouped_rows",
            "substantive_terms": [
                {"text": "服务", "slot": "path"},
                {"text": "使用", "slot": "path"},
                {"text": "隧道", "slot": "path"},
                {"text": "源节点", "slot": "path"},
                {"text": "位置", "slot": "group_by", "attached_to": "网元"},
                {"text": "数量", "slot": "projection"},
                {"text": "降序", "slot": "order_by"},
                {"text": "3", "slot": "limit"},
            ],
        }
    )

    result = validate_dsl_structural_coverage(
        requirements,
        {
            "schema_version": "restricted_query_dsl_v1",
            "query_id": "qa_c3e83dd7ad32",
            "query_shape": "ad_hoc_aggregate",
            "source_question": "统计服务使用的隧道源节点所在位置的网元数量，按数量降序排列，返回前3名。",
            "bindings": {
                "svc": {"vertex_name": "Service"},
                "tun": {"vertex_name": "Tunnel"},
                "ne": {"vertex_name": "NetworkElement"},
            },
            "operations": [
                {
                    "op": "aggregate",
                    "group_by": [
                        {
                            "alias": "location",
                            "target": "ne",
                            "property": {"owner": "NetworkElement", "name": "location"},
                        }
                    ],
                    "measures": [
                        {
                            "alias": "cnt",
                            "function": "count",
                            "target": "ne",
                            "property": {"owner": "NetworkElement", "name": "id"},
                        }
                    ],
                },
                {"op": "sort", "by": [{"source": "measure.cnt", "direction": "desc"}]},
                {"op": "limit", "value": 3},
                {
                    "op": "variable_path",
                    "bind_as": "path",
                    "start": "svc",
                    "through": {"vertex_ref": "ne", "filters": []},
                    "allowed_edges": ["SERVICE_USES_TUNNEL", "TUNNEL_SRC"],
                    "min_hops": 2,
                    "max_hops": 8,
                },
            ],
            "projection": {
                "items": [
                    {"alias": "whatever_alias", "source": "group.location"},
                        {"alias": "cnt", "source": "measure.cnt", "projection_terms": ["数量"]},
                    ]
                },
            },
        )

    assert result.is_valid is True
    assert result.missing == []
