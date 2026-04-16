NETWORK_SCHEMA_V10_CONTEXT = """
Graph: network_schema_v10

=== VERTEX LABELS ===

(NetworkElement)
  Description: Network element (router/switch/firewall) - RFC 1812, RFC 2401
  Primary key: id
  Properties:
    - id: STRING, required, unique, indexed. Pattern: ^ne-[0-9]{4}$
    - name: STRING, required
    - type: STRING, required. Values: router | switch | firewall | load_balancer | wan_optimizer
    - ip_address: STRING, required
    - location: STRING, required
    - model: STRING, required
    - software_version: STRING, required
    - vendor: STRING, required

(Protocol)
  Description: Network protocol definition - RFC 791 (IP), RFC 4271 (BGP)
  Primary key: id
  Properties:
    - id: STRING, required, unique, indexed. Pattern: ^proto-[a-z0-9-]+$
    - name: STRING, required
    - ietf_category: STRING, required. Values: Routing | Transport | Applications | Security | Management
    - standard: STRING, required. Pattern: RFC [0-9]+
    - version: STRING, required

(Tunnel)
  Description: Network tunnel instance - RFC 3209 (MPLS-TE), RFC 4301 (IPsec)
  Primary key: id
  Properties:
    - id: STRING, required, unique, indexed. Pattern: ^tun-[a-z-]+-[0-9]+$
    - name: STRING, required
    - type: STRING, required. Values: MPLS-TE | GRE | IPsec | L2TP | VXLAN | MPLS-VPN
    - bandwidth: DOUBLE, required
    - latency: DOUBLE, required
    - ietf_standard: STRING, required. Pattern: RFC [0-9]+
  Note: Path defined EXCLUSIVELY via PATH_THROUGH edges.

(Service)
  Description: Business/service layer - RFC 4364 (MPLS-VPN), RFC 2475 (DiffServ)
  Primary key: id
  Properties:
    - id: STRING, required, unique, indexed. Pattern: ^svc-[a-z-]+-[0-9]+$
    - name: STRING, required
    - type: STRING, required. Values: MPLS-VPN | QoS | L3VPN | MPLS-TE | Firewall_Service
    - bandwidth: DOUBLE, required
    - latency: DOUBLE, required
    - quality_of_service: STRING, required. Values: Gold | Silver | Bronze | Best_Effort

(Port)
  Description: Physical/logical interface - RFC 2863 (Interfaces MIB), IEEE 802.3
  Primary key: id
  Properties:
    - id: STRING, required, unique, indexed. Pattern: ^port-[a-z0-9-]+$
    - name: STRING, required
    - type: STRING, required. Values: physical | logical | virtual
    - speed: DOUBLE, required
    - mac_address: STRING, required
    - status: STRING, required. Values: up | down | administratively_down
    - vlan_id: STRING, required

(Fiber)
  Description: Physical fiber optic cable - ITU-T G.652/G.657, RFC 2615
  Primary key: id
  Properties:
    - id: STRING, required, unique, indexed. Pattern: ^fiber-[0-9]{4}$
    - name: STRING, required
    - type: STRING, required. Values: single-mode | multi-mode
    - bandwidth_capacity: DOUBLE, required
    - length: DOUBLE, required
    - location: STRING, required
    - wavelength: STRING, required. Pattern: ^[0-9]+nm$

(Link)
  Description: Logical/physical link between ports - RFC 2863 (ifTable), RFC 1213 (MIB-II)
  Primary key: id
  Properties:
    - id: STRING, required, unique, indexed. Pattern: ^link-[0-9]{4}$
    - name: STRING, required
    - type: STRING, required
    - bandwidth: DOUBLE, required
    - latency: DOUBLE, required
    - mtu: INT32, required
    - admin_status: STRING, required. Values: up | down | testing
    - protocol: STRING, required
    - status: STRING, required
    - vlan_id: STRING, required

=== EDGE LABELS ===

[:HAS_PORT]  (NetworkElement) -> (Port)
  Description: NetworkElement owns Port (RFC 2863 Interfaces MIB)
  Properties:
    - admin_status: STRING, required. Administrative status per RFC 2863

[:FIBER_SRC]  (Fiber) -> (Port)
  Description: Fiber source termination: Fiber -> Port (physical layer)

[:FIBER_DST]  (Fiber) -> (Port)
  Description: Fiber destination termination: Fiber -> Port (physical layer)

[:LINK_SRC]  (Link) -> (Port)
  Description: Link source termination: Link -> Port (logical layer)

[:LINK_DST]  (Link) -> (Port)
  Description: Link destination termination: Link -> Port (logical layer)

[:TUNNEL_SRC]  (Tunnel) -> (NetworkElement)
  Description: Tunnel source endpoint: Tunnel -> NetworkElement

[:TUNNEL_DST]  (Tunnel) -> (NetworkElement)
  Description: Tunnel destination endpoint: Tunnel -> NetworkElement

[:TUNNEL_PROTO]  (Tunnel) -> (Protocol)
  Description: Tunnel protocol binding: Tunnel -> Protocol (RFC 3209)

[:PATH_THROUGH]  (Tunnel) -> (NetworkElement)
  Description: Tunnel path sequence: Tunnel -> NetworkElement (RFC 3209 ERO)
  Properties:
    - hop_order: INT32, required. Path sequence index (0=ingress LSR, n=egress LSR)

[:SERVICE_USES_TUNNEL]  (Service) -> (Tunnel)
  Description: Service leverages Tunnel: Service -> Tunnel (RFC 4364 MPLS-VPN architecture)
""".strip()


NETWORK_SCHEMA_V10_HINTS = {
    "network_element": {
        "label": "NetworkElement",
        "keywords": ["networkelement", "network element", "网络设备", "设备", "router", "节点"],
        "return_fields": ["n.id AS id", "n.name AS name", "n.ip_address AS ip_address", "n.location AS location"],
    },
    "port": {
        "label": "Port",
        "keywords": ["port", "端口", "接口"],
        "return_fields": ["p.id AS id", "p.name AS name", "p.status AS status", "p.vlan_id AS vlan_id"],
    },
    "tunnel": {
        "label": "Tunnel",
        "keywords": ["tunnel", "隧道"],
        "return_fields": ["t.id AS id", "t.name AS name", "t.bandwidth AS bandwidth", "t.latency AS latency"],
    },
    "service": {
        "label": "Service",
        "keywords": ["service", "业务", "服务"],
        "return_fields": ["s.id AS id", "s.name AS name", "s.bandwidth AS bandwidth", "s.quality_of_service AS quality_of_service"],
    },
    "protocol": {
        "label": "Protocol",
        "keywords": ["protocol", "协议"],
        "return_fields": ["p.id AS id", "p.name AS name", "p.standard AS standard", "p.version AS version"],
    },
    "fiber": {
        "label": "Fiber",
        "keywords": ["fiber", "光纤"],
        "return_fields": ["f.id AS id", "f.name AS name", "f.length AS length", "f.location AS location"],
    },
    "link": {
        "label": "Link",
        "keywords": ["link", "链路"],
        "return_fields": ["l.id AS id", "l.name AS name", "l.bandwidth AS bandwidth", "l.status AS status"],
    },
}
