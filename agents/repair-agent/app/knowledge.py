from __future__ import annotations

"""Repair-only knowledge helpers.

The current Cypher Generation Service no longer imports this module in its main
request path. These helpers remain for repair-side counterfactual experiments
and for documentation of the built-in network schema package.
"""

from typing import List

from .models import KnowledgeContext, KnowledgePackage
from .schema_profile import NETWORK_SCHEMA_V10_CONTEXT, NETWORK_SCHEMA_V10_HINTS

DEFAULT_KNOWLEDGE_PACKAGE = KnowledgePackage(
    package_id="default-network-schema",
    version="v1",
    graph_name="network_schema_v10",
    summary="Default built-in knowledge package for network_schema_v10, covering schema facts, common business terms, and starter query patterns.",
    schema_facts={
        "context": NETWORK_SCHEMA_V10_CONTEXT,
        "vertex_labels": ["NetworkElement", "Protocol", "Tunnel", "Service", "Port", "Fiber", "Link"],
        "edge_labels": [
            "HAS_PORT",
            "FIBER_SRC",
            "FIBER_DST",
            "LINK_SRC",
            "LINK_DST",
            "TUNNEL_SRC",
            "TUNNEL_DST",
            "TUNNEL_PROTO",
            "PATH_THROUGH",
            "SERVICE_USES_TUNNEL",
        ],
    },
    business_terms={
        "network_element": ["网络设备", "设备", "node", "network element", "router"],
        "port": ["端口", "接口", "port"],
        "tunnel": ["隧道", "tunnel"],
        "service": ["业务", "服务", "service"],
        "protocol": ["协议", "protocol"],
        "fiber": ["光纤", "fiber"],
        "link": ["链路", "link"],
        "has_port": ["设备端口", "设备的端口", "has port"],
        "service_uses_tunnel": ["业务使用隧道", "服务使用隧道"],
    },
    query_patterns={
        "network_element_ports": "MATCH (ne:NetworkElement)-[:HAS_PORT]->(p:Port) RETURN ne.name, p.name LIMIT 20",
        "service_tunnels": "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel) RETURN s.name, t.name LIMIT 20",
        "tunnel_protocols": "MATCH (t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol) RETURN t.name, p.name LIMIT 20",
    },
    constraints={
        "forbidden_labels": ["Movie", "Film", "Person", "Actor", "Book"],
        "notes": [
            "Only use labels and edge labels that exist in network_schema_v10.",
            "Prefer concrete return fields over full node dumps.",
        ],
    },
    knowledge_tags=[
        "network_element",
        "port",
        "tunnel",
        "service",
        "protocol",
        "fiber",
        "link",
        "has_port",
        "service_uses_tunnel",
        "schema_core",
    ],
)


def select_knowledge_tags(question: str) -> List[str]:
    text = question.lower()
    selected: List[str] = ["schema_core"]

    for entity_key, entity in NETWORK_SCHEMA_V10_HINTS.items():
        if any(keyword in text for keyword in entity["keywords"]):
            selected.append(entity_key)

    if any(token in text for token in ["端口", "port", "接口"]) and any(
        token in text for token in ["设备", "network element", "router", "网络设备"]
    ):
        selected.append("has_port")

    if any(token in text for token in ["服务", "service", "业务"]) and any(token in text for token in ["隧道", "tunnel"]):
        selected.append("service_uses_tunnel")

    if len(selected) == 1:
        selected.append("network_element")

    return sorted(set(selected))


def build_schema_hint_from_tags(tags: List[str]) -> str:
    lines = [f"Knowledge tags: {', '.join(tags)}"]

    if "network_element" in tags:
        lines.append("Entity: NetworkElement(id, name, elem_type, ip_address, location)")
    if "port" in tags:
        lines.append("Entity: Port(id, name, speed, status, vlan_id)")
    if "service" in tags:
        lines.append("Entity: Service(id, name, bandwidth, quality_of_service)")
    if "tunnel" in tags:
        lines.append("Entity: Tunnel(id, name, bandwidth, latency)")
    if "protocol" in tags:
        lines.append("Entity: Protocol(id, name, standard, version)")
    if "link" in tags:
        lines.append("Entity: Link(id, name, bandwidth, status)")
    if "fiber" in tags:
        lines.append("Entity: Fiber(id, name, length, location)")
    if "has_port" in tags:
        lines.append("Relation: (NetworkElement)-[:HAS_PORT]->(Port)")
    if "service_uses_tunnel" in tags:
        lines.append("Relation: (Service)-[:SERVICE_USES_TUNNEL]->(Tunnel)")

    return "\n".join(lines)


def build_knowledge_context(tags: List[str]) -> KnowledgeContext:
    return KnowledgeContext(
        package_id=DEFAULT_KNOWLEDGE_PACKAGE.package_id,
        version=DEFAULT_KNOWLEDGE_PACKAGE.version,
        graph_name=DEFAULT_KNOWLEDGE_PACKAGE.graph_name,
        summary=DEFAULT_KNOWLEDGE_PACKAGE.summary,
        loaded_knowledge_tags=tags,
    )
