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

### 当前 agent 边界
- testing-agent 使用 schema 画像作为评测和排查参考。
- cypher-generator-agent 的正式知识上下文来自 knowledge-agent，不在本地组装 `KnowledgeContext`。
- repair-agent 消费 testing-agent 的 `IssueTicket`，不从本参考文档派生修复请求。

## Schema 关键词参考表

下表仅用于人工理解 schema 中常见中文问题词与图谱元素的对应关系。

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
