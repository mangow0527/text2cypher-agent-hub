from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from services.cypher_generator_agent.app.graph_semantic_view import (
    GraphSemanticViewConfigError,
    get_default_graph_semantic_view,
    load_graph_semantic_view,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "services/testing_agent/docs/reference/schema.json"


def test_default_graph_semantic_view_loads_design_modules() -> None:
    semantic_view = get_default_graph_semantic_view()

    assert semantic_view.view_id == "network_graph_semantic_view"
    assert semantic_view.entity("service").label == "Service"
    assert semantic_view.dimension("service.quality_of_service").property == "quality_of_service"
    assert semantic_view.fact("tunnel.latency").property == "latency"
    assert semantic_view.metric("avg_tunnel_latency").expression == "avg(t.latency)"
    assert semantic_view.relationship("service_uses_tunnel").edge == "SERVICE_USES_TUNNEL"
    assert semantic_view.path_semantic("service.uses_tunnel").relationships == ("service_uses_tunnel",)
    assert semantic_view.return_policy("path_entities_default").name_zh == "路径两端实体默认返回"
    assert semantic_view.disambiguation_rules[0].rule_id == "destination_ne_prefers_tunnel_dst"


def test_default_graph_semantic_view_uses_tugraph_elem_type_field_for_type_semantics() -> None:
    semantic_view = get_default_graph_semantic_view()

    assert semantic_view.dimension("service.elem_type").property == "elem_type"
    assert semantic_view.dimension("tunnel.elem_type").property == "elem_type"
    assert semantic_view.dimension("network_element.elem_type").property == "elem_type"
    assert semantic_view.dimension("port.elem_type").property == "elem_type"
    assert semantic_view.dimension("fiber.elem_type").property == "elem_type"
    assert semantic_view.dimension("link.elem_type").property == "elem_type"


def test_graph_semantic_view_covers_user_language_for_field_and_path_materials() -> None:
    semantic_view = get_default_graph_semantic_view()

    assert "服务ID" in semantic_view.dimension("service.id").synonyms
    assert "隧道ID" in semantic_view.dimension("tunnel.id").synonyms
    assert "网元ID" in semantic_view.dimension("network_element.id").synonyms
    assert "端口ID" in semantic_view.dimension("port.id").synonyms
    assert "IETF标准" in semantic_view.dimension("tunnel.ietf_standard").synonyms
    assert "端口MAC地址" in semantic_view.dimension("port.mac_address").synonyms
    assert "网元IP地址" in semantic_view.dimension("network_element.ip_address").synonyms
    assert "服务带宽值" in semantic_view.fact("service.bandwidth").synonyms
    assert "服务延迟" in semantic_view.fact("service.latency").synonyms
    assert "隧道延迟" in semantic_view.fact("tunnel.latency").synonyms

    assert "服务与隧道的对应关系" in semantic_view.path_semantic("service.uses_tunnel").trigger_phrases
    assert "网元端口MAC地址" in semantic_view.path_semantic("service.tunnel_path_ports").trigger_phrases
    assert "源网元IP地址" in semantic_view.path_semantic("service.tunnel_source").trigger_phrases
    assert "连接到各位置网元" in semantic_view.path_semantic("service.tunnel_destination").trigger_phrases


def test_loader_rejects_invalid_schema_references(tmp_path: Path) -> None:
    config_path = tmp_path / "network_graph_semantic_view.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "view_id": "network_graph_semantic_view",
                "name_zh": "网络图语义视图",
                "description": "测试视图",
                "entities": {
                    "ghost": {
                        "name_zh": "幽灵",
                        "label": "Ghost",
                        "alias": "g",
                        "description": "不存在实体",
                        "synonyms": ["幽灵"],
                        "primary_key": "id",
                        "display_fields": ["name"],
                    },
                    "service": {
                        "name_zh": "服务",
                        "label": "Service",
                        "alias": "s",
                        "description": "服务",
                        "synonyms": ["服务"],
                        "primary_key": "id",
                        "display_fields": ["name"],
                    },
                },
                "dimensions": {
                    "service.bad": {
                        "name_zh": "坏字段",
                        "owner": "service",
                        "property": "not_in_schema",
                        "description": "不存在字段",
                        "synonyms": ["不存在字段"],
                        "roles": ["return"],
                        "value_type": "string",
                    }
                },
                "facts": {},
                "metrics": {},
                "relationships": {
                    "bad_edge": {
                        "name_zh": "坏关系",
                        "from": "service",
                        "edge": "SERVICE_USES_TUNNEL",
                        "to": "ghost",
                        "direction": "out",
                        "description": "坏关系",
                        "synonyms": ["使用"],
                    }
                },
                "path_semantics": {},
                "return_policies": {},
                "disambiguation_rules": [],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(GraphSemanticViewConfigError) as exc_info:
        load_graph_semantic_view(config_path, schema_path=SCHEMA_PATH)

    message = str(exc_info.value)
    assert "Ghost" in message
    assert "not_in_schema" in message


def test_loader_rejects_synonym_collisions(tmp_path: Path) -> None:
    config_path = tmp_path / "network_graph_semantic_view.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "view_id": "network_graph_semantic_view",
                "name_zh": "网络图语义视图",
                "description": "测试视图",
                "entities": {
                    "service": {
                        "name_zh": "服务",
                        "label": "Service",
                        "alias": "s",
                        "description": "服务",
                        "synonyms": ["业务"],
                        "primary_key": "id",
                        "display_fields": ["name"],
                    },
                    "tunnel": {
                        "name_zh": "隧道",
                        "label": "Tunnel",
                        "alias": "t",
                        "description": "隧道",
                        "synonyms": ["业务"],
                        "primary_key": "id",
                        "display_fields": ["name"],
                    },
                },
                "dimensions": {},
                "facts": {},
                "metrics": {},
                "relationships": {},
                "path_semantics": {},
                "return_policies": {},
                "disambiguation_rules": [],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(GraphSemanticViewConfigError, match="业务"):
        load_graph_semantic_view(config_path, schema_path=SCHEMA_PATH)
