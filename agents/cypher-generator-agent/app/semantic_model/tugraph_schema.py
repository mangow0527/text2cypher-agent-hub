from __future__ import annotations

from typing import Any, Mapping, Sequence


_TYPE_MAP = {
    "STRING": "string",
    "DOUBLE": "float",
    "FLOAT": "float",
    "INT": "int",
    "INT32": "int",
    "INT64": "int",
    "BOOL": "boolean",
    "BOOLEAN": "boolean",
    "DATE": "datetime",
    "DATETIME": "datetime",
}

_VERTEX_SYNONYMS = {
    "NetworkElement": ["设备", "网元", "网络设备", "device", "network element"],
    "Protocol": ["协议", "网络协议", "protocol"],
    "Tunnel": ["隧道", "tunnel", "MPLS 隧道", "IPsec 隧道"],
    "Service": ["服务", "业务", "service"],
    "Port": ["端口", "接口", "port", "interface"],
    "Fiber": ["光纤", "光缆", "fiber"],
    "Link": ["链路", "连接", "link"],
}

_PROPERTY_SYNONYMS = {
    "id": ["ID", "标识", "identifier"],
    "name": ["名称", "名字", "name"],
    "ip_address": ["IP 地址", "IPv4 地址", "ip address"],
    "location": ["位置", "机房", "站点", "location"],
    "model": ["型号", "model"],
    "software_version": ["软件版本", "版本", "software version"],
    "elem_type": ["类型", "元素类型", "设备类型", "服务类型", "type"],
    "vendor": ["厂商", "供应商", "vendor"],
    "ietf_category": ["IETF 分类", "协议分类", "category"],
    "standard": ["标准", "RFC", "standard"],
    "version": ["版本", "version"],
    "bandwidth": ["带宽", "bandwidth"],
    "latency": ["延迟", "时延", "latency"],
    "ietf_standard": ["IETF 标准", "RFC", "standard"],
    "speed": ["速率", "端口速率", "speed"],
    "mac_address": ["MAC 地址", "mac address"],
    "status": ["状态", "运行状态", "status"],
    "vlan_id": ["VLAN", "VLAN ID", "vlan"],
    "bandwidth_capacity": ["带宽容量", "容量", "capacity"],
    "length": ["长度", "光纤长度", "length"],
    "wavelength": ["波长", "wavelength"],
    "mtu": ["MTU", "最大传输单元"],
    "admin_status": ["管理状态", "admin status"],
    "protocol": ["协议", "protocol"],
    "hop_order": ["跳序", "第几跳", "hop", "path order"],
}

_VALUE_SYNONYMS = {
    "router": ["路由器"],
    "switch": ["交换机"],
    "firewall": ["防火墙", "FW"],
    "load_balancer": ["负载均衡器", "LB"],
    "wan_optimizer": ["广域网优化器", "WAN 优化器"],
    "MPLS-TE": ["MPLS TE", "MPLS 流量工程"],
    "GRE": ["GRE 隧道"],
    "IPsec": ["IPsec 隧道"],
    "L2TP": ["L2TP 隧道"],
    "VXLAN": ["VXLAN 隧道"],
    "MPLS-VPN": ["MPLS VPN", "MPLS VPN 业务"],
    "Gold": ["Gold", "金牌", "黄金级"],
    "Silver": ["Silver", "银牌", "白银级"],
    "Bronze": ["Bronze", "铜牌", "青铜级"],
    "Best_Effort": ["Best Effort", "尽力而为"],
    "QoS": ["服务质量"],
    "L3VPN": ["三层 VPN"],
    "Firewall_Service": ["防火墙服务"],
    "up": ["正常", "在线", "启用"],
    "down": ["故障", "离线", "中断"],
    "administratively_down": ["管理关闭", "人工关闭"],
    "physical": ["物理", "物理端口"],
    "logical": ["逻辑", "逻辑端口"],
    "virtual": ["虚拟", "虚拟端口"],
    "single-mode": ["单模"],
    "multi-mode": ["多模"],
    "testing": ["测试中"],
    "Routing": ["路由"],
    "Transport": ["传输"],
    "Applications": ["应用"],
    "Security": ["安全"],
    "Management": ["管理"],
}

_EDGE_CONTEXT = {
    "HAS_PORT": {
        "cardinality": "one_to_many",
        "synonyms": ["拥有端口", "包含接口", "has port"],
        "direction": "存储方向：NetworkElement -> Port。语义方向：设备拥有该端口。",
    },
    "FIBER_SRC": {
        "cardinality": "many_to_one",
        "synonyms": ["光纤源端", "光纤起点", "fiber source"],
        "direction": "存储方向：Fiber -> Port。语义方向：该 Port 是光纤源端。",
    },
    "FIBER_DST": {
        "cardinality": "many_to_one",
        "synonyms": ["光纤宿端", "光纤终点", "fiber destination"],
        "direction": "存储方向：Fiber -> Port。语义方向：该 Port 是光纤宿端。",
    },
    "LINK_SRC": {
        "cardinality": "many_to_one",
        "synonyms": ["链路源端", "链路起点", "link source"],
        "direction": "存储方向：Link -> Port。语义方向：该 Port 是链路源端。",
    },
    "LINK_DST": {
        "cardinality": "many_to_one",
        "synonyms": ["链路宿端", "链路终点", "link destination"],
        "direction": "存储方向：Link -> Port。语义方向：该 Port 是链路宿端。",
    },
    "TUNNEL_SRC": {
        "cardinality": "many_to_one",
        "synonyms": ["隧道源端", "入口设备", "source device"],
        "direction": "存储方向：Tunnel -> NetworkElement。语义方向：该 NetworkElement 是隧道源端设备。",
        "anti_patterns": [
            "不要只根据 TUNNEL_SRC 和 TUNNEL_DST 推断完整隧道路由路径。",
            "完整路径查询必须使用 PATH_THROUGH，并按 hop_order 排序。",
        ],
    },
    "TUNNEL_DST": {
        "cardinality": "many_to_one",
        "synonyms": ["隧道宿端", "出口设备", "destination device"],
        "direction": "存储方向：Tunnel -> NetworkElement。语义方向：该 NetworkElement 是隧道宿端设备。",
        "anti_patterns": [
            "不要只根据 TUNNEL_SRC 和 TUNNEL_DST 推断完整隧道路由路径。",
            "完整路径查询必须使用 PATH_THROUGH，并按 hop_order 排序。",
        ],
    },
    "TUNNEL_PROTO": {
        "cardinality": "many_to_one",
        "synonyms": ["隧道协议", "使用协议", "tunnel protocol"],
        "direction": "存储方向：Tunnel -> Protocol。语义方向：该隧道使用该协议。",
    },
    "PATH_THROUGH": {
        "cardinality": "many_to_many",
        "synonyms": ["经过", "路径", "走过", "traverses"],
        "direction": "存储方向：Tunnel -> NetworkElement。语义方向：隧道路由路径经过该设备。",
    },
    "SERVICE_USES_TUNNEL": {
        "cardinality": "many_to_many",
        "synonyms": ["使用隧道", "承载于", "uses tunnel"],
        "direction": "存储方向：Service -> Tunnel。语义方向：业务承载于一个或多个 Tunnel。",
    },
}


def build_graph_semantic_model_from_tugraph_schema(
    schema: Sequence[Mapping[str, Any]],
    *,
    model_name: str,
) -> dict[str, Any]:
    vertices = [_vertex(entry) for entry in schema if entry.get("type") == "VERTEX"]
    edges = [_edge(entry) for entry in schema if entry.get("type") == "EDGE"]

    return {
        "name": model_name,
        "description": "由 TuGraph 网络 schema 生成的图语义模型。",
        "ai_context": {
            "instructions": (
                "以这份 TuGraph schema 作为唯一事实来源。只生成只读 Cypher；"
                "隧道完整路径查询必须使用 PATH_THROUGH，并按 hop_order 排序。"
            ),
            "synonyms": ["TuGraph network schema", "网络拓扑", "图数据库 schema"],
            "examples": [
                "Gold 服务使用了哪些隧道",
                "隧道 tun-mpls-001 经过哪些设备",
                "全网有多少台防火墙",
            ],
        },
        "vertices": vertices,
        "edges": edges,
        "path_patterns": [_tunnel_full_path_pattern()],
        "metrics": _metrics(),
    }


def _vertex(entry: Mapping[str, Any]) -> dict[str, Any]:
    label = str(entry["label"])
    return {
        "name": label,
        "id_property": str(entry.get("primary") or "id"),
        "description": entry.get("description"),
        "ai_context": {"synonyms": _synonyms(_VERTEX_SYNONYMS.get(label, []))},
        "properties": [_property(prop) for prop in entry.get("properties", [])],
    }


def _edge(entry: Mapping[str, Any]) -> dict[str, Any]:
    name = str(entry["label"])
    endpoint = _single_endpoint(entry)
    context = _EDGE_CONTEXT.get(name, {})
    edge: dict[str, Any] = {
        "name": name,
        "from": endpoint[0],
        "to": endpoint[1],
        "cardinality": context.get("cardinality", "many_to_many"),
        "description": entry.get("description"),
        "direction_semantics": context.get(
            "direction",
            f"存储方向：{endpoint[0]} -> {endpoint[1]}。",
        ),
        "ai_context": {"synonyms": _synonyms(context.get("synonyms", []))},
        "properties": [_property(prop) for prop in entry.get("properties", [])],
    }
    if context.get("anti_patterns"):
        edge["anti_patterns"] = context["anti_patterns"]
    return edge


def _single_endpoint(entry: Mapping[str, Any]) -> tuple[str, str]:
    constraints = entry.get("constraints")
    if not isinstance(constraints, Sequence) or len(constraints) != 1:
        raise ValueError(f"{entry.get('label')} must declare exactly one edge constraint")
    endpoint = constraints[0]
    if not isinstance(endpoint, Sequence) or len(endpoint) != 2:
        raise ValueError(f"{entry.get('label')} edge constraint must be [from, to]")
    return str(endpoint[0]), str(endpoint[1])


def _property(entry: Mapping[str, Any]) -> dict[str, Any]:
    name = str(entry["name"])
    valid_values = _parse_valid_values(entry.get("description"))
    prop: dict[str, Any] = {
        "name": name,
        "type": _TYPE_MAP.get(str(entry["type"]).upper(), "string"),
        "required": not bool(entry.get("optional", True)),
        "description": entry.get("description"),
        "ai_context": {"synonyms": _synonyms(_PROPERTY_SYNONYMS.get(name, []))},
    }
    if valid_values:
        prop["valid_values"] = valid_values
        value_synonyms = {
            value: _synonyms(_VALUE_SYNONYMS[value])
            for value in valid_values
            if value in _VALUE_SYNONYMS
        }
        if value_synonyms:
            prop["value_synonyms"] = value_synonyms
    return prop


def _parse_valid_values(description: Any) -> list[str]:
    if not isinstance(description, str) or "|" not in description:
        return []
    if "pattern" in description.casefold():
        return []
    values = [part.strip() for part in description.split("|")]
    if len(values) < 2 or any(not value for value in values):
        return []
    return values


def _synonyms(values: Sequence[str]) -> list[str]:
    return list(values)


def _tunnel_full_path_pattern() -> dict[str, Any]:
    return {
        "name": "tunnel_full_path",
        "description": "Full ordered tunnel path using PATH_THROUGH.hop_order.",
        "parameters": [
            {
                "name": "tunnel_id",
                "type": "string",
                "description": "Target Tunnel.id.",
            }
        ],
        "cypher": (
            "MATCH (t:Tunnel {id: $tunnel_id})-[p:PATH_THROUGH]->(ne:NetworkElement)\n"
            "RETURN ne AS device, p.hop_order AS hop\n"
            "ORDER BY p.hop_order ASC"
        ),
        "ai_context": {
            "examples": [
                "隧道 tun-mpls-001 经过哪些设备",
                "tun-mpls-001 的完整路径",
            ]
        },
    }


def _metrics() -> list[dict[str, Any]]:
    return [
        {
            "name": "device_count",
            "description": "Count network devices.",
            "pattern": "(ne:NetworkElement)",
            "expression": "count(ne)",
            "valid_dimensions": ["ne.elem_type", "ne.vendor", "ne.location"],
            "ai_context": {"synonyms": ["设备数量", "网元数量", "device count"]},
        },
        {
            "name": "port_count",
            "description": "Count ports through the HAS_PORT edge.",
            "pattern": "(ne:NetworkElement)-[:HAS_PORT]->(port:Port)",
            "expression": "count(port)",
            "valid_dimensions": ["ne.id", "ne.elem_type", "port.status", "port.elem_type"],
            "ai_context": {"synonyms": ["端口数量", "接口数量", "port count"]},
        },
        {
            "name": "service_count",
            "description": "Count services.",
            "pattern": "(svc:Service)",
            "expression": "count(svc)",
            "valid_dimensions": ["svc.quality_of_service", "svc.elem_type"],
            "ai_context": {"synonyms": ["服务数量", "业务数量", "service count"]},
        },
        {
            "name": "tunnel_count",
            "description": "Count tunnels.",
            "pattern": "(t:Tunnel)",
            "expression": "count(t)",
            "valid_dimensions": ["t.elem_type", "t.ietf_standard"],
            "ai_context": {"synonyms": ["隧道数量", "tunnel count"]},
        },
        {
            "name": "link_count",
            "description": "Count links.",
            "pattern": "(link:Link)",
            "expression": "count(link)",
            "valid_dimensions": ["link.status", "link.protocol", "link.elem_type"],
            "ai_context": {"synonyms": ["链路数量", "link count"]},
        },
    ]
