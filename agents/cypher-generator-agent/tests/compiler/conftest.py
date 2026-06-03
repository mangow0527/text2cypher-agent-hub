from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.cypher_generator_agent.app.dsl.parser import parse_restricted_query_dsl
from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry, load_graph_semantic_model


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "network_topology_graph_model.yaml"


@pytest.fixture(scope="module")
def registry() -> GraphSemanticRegistry:
    return load_graph_semantic_model(FIXTURE_PATH).registry


def parse_dsl(payload: dict[str, Any], registry: GraphSemanticRegistry):
    return parse_restricted_query_dsl(payload, registry)


def single_hop_dsl() -> dict[str, Any]:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-single-hop",
        "query_shape": "single_hop_traversal",
        "source_question": "Gold 服务使用了哪些隧道",
        "bindings": {
            "start": {"vertex_name": "Service"},
            "edge": {"edge_name": "SERVICE_USES_TUNNEL"},
            "end": {"vertex_name": "Tunnel"},
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
        "filters": [
            {
                "target": "start",
                "property": {"owner": "Service", "name": "quality_of_service"},
                "operator": "eq",
                "value": {
                    "raw": "Gold",
                    "normalized": "GOLD",
                    "resolver_match_type": "synonym",
                },
            }
        ],
        "projection": {
            "items": [
                {
                    "alias": "tunnel_id",
                    "target": "end",
                    "property": {"owner": "Tunnel", "name": "id"},
                }
            ]
        },
    }


def named_path_pattern_dsl() -> dict[str, Any]:
    return {
        "schema_version": "restricted_query_dsl_v1",
        "query_id": "q-path",
        "query_shape": "named_path_pattern",
        "source_question": "隧道 tun-mpls-001 经过哪些设备",
        "bindings": {"primary_vertex": {"vertex_name": "Tunnel"}},
        "operations": [
            {
                "op": "use_path_pattern",
                "path_pattern_name": "tunnel_full_path",
                "bind_as": "path",
                "parameters": {
                    "tunnel_id": {
                        "raw": "tun-mpls-001",
                        "normalized": "tun-mpls-001",
                        "resolver_match_type": "value_index_exact",
                    }
                },
            }
        ],
        "projection": {
            "items": [
                {"alias": "device", "source": "path.device"},
                {"alias": "hop", "source": "path.hop"},
            ]
        },
    }
