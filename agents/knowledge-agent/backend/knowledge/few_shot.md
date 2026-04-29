## Reference Examples

[id: tunnel_protocol_version]
[types: WHERE_FILTER, PATH_TRAVERSAL]
Question: 查询协议版本为v2.0的隧道
Cypher: MATCH (t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol) WHERE p.version = 'v2.0' RETURN t.id, t.name
Why: 展示协议版本过滤路径。

[id: networkelement_tunnel_protocol_path]
[types: WHERE_FILTER, PATH_TRAVERSAL, MULTI_HOP]
Question: 查询协议版本为v2.0的隧道所属网元
Cypher: MATCH (n:NetworkElement)-[:FIBER_SRC]->(t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol) WHERE p.version = 'v2.0' RETURN n.id
Why: 展示网元到隧道再到协议的标准路径。

[id: aggregate_ports_by_networkelement]
[types: AGGREGATION, GROUP_AGGREGATION]
Question: 统计每个网元的端口数量
Cypher: MATCH (n:NetworkElement)-[:HAS_PORT]->(p:Port) RETURN n.id, count(p) AS port_count
Why: 展示按网元分组并对关联端口计数。

[id: top_latency_tunnels]
[types: ORDER_LIMIT]
Question: 查询延迟最高的5条隧道
Cypher: MATCH (t:Tunnel) RETURN t.id, t.name, t.latency ORDER BY t.latency DESC LIMIT 5
Why: 展示排序与 LIMIT 的 Top-K 查询。
