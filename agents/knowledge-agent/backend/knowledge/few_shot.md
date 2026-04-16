## Reference Examples

[id: tunnel_protocol_version]
Question: 查询协议版本为v2.0的隧道
Cypher: MATCH (t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol) WHERE p.version = 'v2.0' RETURN t.id, t.name
Why: 展示协议版本过滤路径。

[id: networkelement_tunnel_protocol_path]
Question: 查询协议版本为v2.0的隧道所属网元
Cypher: MATCH (n:NetworkElement)-[:FIBER_SRC]->(t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol) WHERE p.version = 'v2.0' RETURN n.id
Why: 展示网元到隧道再到协议的标准路径。

## few_shot

[id: query_failure_case_example]
{'question': '查询失败样例', 'cypher': 'MATCH (n:NetworkElement) RETURN n.id AS id, n.name AS name LIMIT 20'}

## few_shot_examples

[id: example_generic_sample_list_query]
{'user_query': '查询失败样例', 'thought': "用户意图为查询'失败样例'。根据业务知识映射，'失败样例'对应通用的网元实例查询。遵循通用列表查询标准规范：1. 限制返回数量（默认LIMIT 20）以避免性能问题；2. 仅选择核心字段（id, name）以减少数据传输。", 'sql': "SELECT id, name FROM NetworkElement WHERE status = 'failed' LIMIT 20"}
