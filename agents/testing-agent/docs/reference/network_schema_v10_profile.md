# network_schema_v10 Schema Profile

这份画像来自真实 TuGraph `network_schema_v10` 的在线探测结果。

## Vertex Labels

- `NetworkElement`
  - 主键：`id`
  - 属性：`elem_type`, `id`, `ip_address`, `location`, `model`, `name`, `software_version`, `vendor`
- `Protocol`
  - 主键：`id`
  - 属性：`id`, `ietf_category`, `name`, `standard`, `version`
- `Tunnel`
  - 主键：`id`
  - 属性：`bandwidth`, `latency`, `elem_type`, `id`, `ietf_standard`, `name`
- `Service`
  - 主键：`id`
  - 属性：`bandwidth`, `latency`, `elem_type`, `id`, `name`, `quality_of_service`
- `Port`
  - 主键：`id`
  - 属性：`speed`, `elem_type`, `id`, `mac_address`, `name`, `status`, `vlan_id`
- `Fiber`
  - 主键：`id`
  - 属性：`bandwidth_capacity`, `length`, `elem_type`, `id`, `location`, `name`, `wavelength`
- `Link`
  - 主键：`id`
  - 属性：`bandwidth`, `latency`, `mtu`, `admin_status`, `elem_type`, `id`, `name`, `protocol`, `status`, `vlan_id`

## Edge Labels

- `(:NetworkElement)-[:HAS_PORT]->(:Port)`
- `(:Fiber)-[:FIBER_SRC]->(:Port)`
- `(:Fiber)-[:FIBER_DST]->(:Port)`
- `(:Link)-[:LINK_SRC]->(:Port)`
- `(:Link)-[:LINK_DST]->(:Port)`
- `(:Tunnel)-[:TUNNEL_SRC]->(:NetworkElement)`
- `(:Tunnel)-[:TUNNEL_DST]->(:NetworkElement)`
- `(:Tunnel)-[:TUNNEL_PROTO]->(:Protocol)`
- `(:Tunnel)-[:PATH_THROUGH {hop_order}]->(:NetworkElement)`
- `(:Service)-[:SERVICE_USES_TUNNEL]->(:Tunnel)`

## Data Sample Notes

验证查询：

```cypher
MATCH (n) RETURN n LIMIT 1
```

返回样本节点：
- label: `NetworkElement`
- name: `NetworkElement_001`
- ip: `10.0.0.1`

## 在系统中的使用方式

Schema 画像被多个模块引用：

### services/testing_agent/app/schema_profile.py
定义 `NETWORK_SCHEMA_V10_CONTEXT`（完整 schema 字符串）和 `NETWORK_SCHEMA_V10_HINTS`（实体关键词映射）。

### services/repair_agent/app/knowledge.py
该模块当前主要保留给修复服务的对照实验使用，不再属于 Cypher 生成服务主链路。  
它基于 schema 画像构建默认知识包 `DEFAULT_KNOWLEDGE_PACKAGE`：
- 包含标签（tags）如 `network_element`, `port`, `tunnel`, `service`, `protocol`, `fiber`, `link`
- 每个标签关联业务术语、查询模式、约束
- `select_knowledge_tags(question)` 根据问题关键词选择相关标签
- `build_schema_hint_from_tags(tags)` 从标签构建 schema hint
- `build_knowledge_context(tags)` 生成完整的 `KnowledgeContext`

### services/testing_agent/app/evaluation.py
评测引擎使用 schema 中的合法 label 和 edge 集合：
- `VALID_LABELS` = `{NetworkElement, Protocol, Tunnel, Service, Port, Fiber, Link}`
- `VALID_RELATIONS` = `{HAS_PORT, FIBER_SRC, FIBER_DST, LINK_SRC, LINK_DST, TUNNEL_SRC, TUNNEL_DST, TUNNEL_PROTO, PATH_THROUGH, SERVICE_USES_TUNNEL}`
- `schema_alignment` 维度检查生成的 Cypher 是否使用了合法的 label 和 edge

### 查询语句生成服务
- 当前 CGS 已不再提供启发式生成器；`NETWORK_SCHEMA_V10_HINTS` 仅作为图谱结构参考资料保留，不再作为生成服务的回退实现来源
- LLM 生成器在 prompt 中注入 `NETWORK_SCHEMA_V10_CONTEXT`
- 主链路不再在服务内选择知识标签，也不再在服务内组装 `KnowledgeContext`

## 启发式映射表

查询语句生成服务的启发式规则映射：

| 关键词 | 映射目标 |
|---|---|
| 设备 / 网络设备 / router | `NetworkElement` |
| 端口 / 接口 | `Port` |
| 隧道 | `Tunnel` |
| 服务 / 业务 | `Service` |
| 协议 | `Protocol` |
| 光纤 | `Fiber` |
| 链路 | `Link` |
| 设备及其端口 | `HAS_PORT` |
| 服务使用哪些隧道 | `SERVICE_USES_TUNNEL` |
| 隧道使用什么协议 | `TUNNEL_PROTO` |
| 隧道经过哪些设备 | `PATH_THROUGH` |
