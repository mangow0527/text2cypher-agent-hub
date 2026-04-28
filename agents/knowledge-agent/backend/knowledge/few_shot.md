## Reference Examples

[id: tunnel_protocol_version]
Question: 查询协议版本为v2.0的隧道
Cypher: MATCH (t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol) WHERE p.version = 'v2.0' RETURN t.id, t.name
Why: 展示协议版本过滤路径。

[id: networkelement_tunnel_protocol_path]
Question: 查询协议版本为v2.0的隧道所属网元
Cypher: MATCH (n:NetworkElement)-[:FIBER_SRC]->(t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol) WHERE p.version = 'v2.0' RETURN n.id
Why: 展示网元到隧道再到协议的标准路径。
