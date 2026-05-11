# CGA 图语义视图建设说明

## 1. 文档目标

本文档说明 `cypher-generator-agent` 应如何建立自己的 **图语义视图 Graph Semantic View**。

图语义视图的目标是把 TuGraph 中的物理 schema 转换成业务查询可理解、可约束、可验证的语义资产。它不是提示词，不是简单词典，也不是某个样本集合，而是 CGA 后续生成 `LogicalQueryPlan` 和 Cypher 的核心依据。

本文档重点回答：

```text
1. 图语义视图应该包含哪些对象？
2. 每类对象有哪些参数？
3. 每个参数对应什么业务内容？
4. 当前网络图第一版应该如何建立？
5. 自然语言问题如何匹配到语义视图对象？
6. 后续如何用这份语义视图生成查询计划？
```

## 2. 图语义视图的整体结构

建议第一版语义视图使用一个 YAML 文件承载：

```text
services/cypher_generator_agent/resources/semantic_views/network_graph_semantic_view.yaml
```

顶层结构建议如下：

```yaml
version: 1
view_id: network_graph_semantic_view
name_zh: 网络图语义视图
description: 面向 NL2Cypher 的网络资源图业务语义定义。

entities: {}
dimensions: {}
facts: {}
metrics: {}
relationships: {}
path_semantics: {}
return_policies: {}
disambiguation_rules: {}
```

第一版顶层模块职责：

| 模块 | 作用 |
|---|---|
| `entities` | 定义业务实体与 TuGraph label 的映射 |
| `dimensions` | 定义可筛选、可返回、可分组、可排序的描述字段 |
| `facts` | 定义可返回、可聚合的数值事实字段 |
| `metrics` | 定义业务指标和聚合口径 |
| `relationships` | 定义单跳图关系 |
| `path_semantics` | 定义业务短语对应的多跳或单跳图路径 |
| `return_policies` | 定义不同语义匹配场景下的默认返回字段 |
| `disambiguation_rules` | 定义易混表达的消歧规则 |

`verified_examples` 是后续可选模块，用于沉淀已验证自然语言问题与目标查询语义；第一版不放入顶层结构。

## 3. `entities`：业务实体

### 3.1 作用

`entities` 定义自然语言中的业务对象与 TuGraph 节点 label 的映射。

例如：

```text
服务 -> Service
隧道 -> Tunnel
网元 -> NetworkElement
端口 -> Port
```

### 3.2 参数规范

```yaml
entities:
  <entity_id>:
    name_zh: <中文名称>
    label: <TuGraph 节点 label>
    alias: <Cypher 默认变量名>
    description: <业务解释>
    synonyms: [<自然语言同义词>]
    primary_key: <主键字段>
    display_fields: [<默认展示字段>]
    default_order_by: <可选，默认排序字段>
```

参数说明：

| 参数 | 必填 | 说明 |
|---|---|---|
| `entity_id` | 是 | 语义层内部使用的实体 ID，使用小写下划线 |
| `name_zh` | 是 | 中文业务名称 |
| `label` | 是 | TuGraph 中真实节点 label |
| `alias` | 是 | 渲染 Cypher 时默认变量名 |
| `description` | 是 | 说明该实体代表什么业务对象 |
| `synonyms` | 是 | 自然语言中可能出现的叫法 |
| `primary_key` | 是 | 主键字段，通常是 `id` |
| `display_fields` | 是 | 关系查询默认展示字段，通常包含 `name`、`id` |
| `default_order_by` | 否 | 默认排序字段 |

### 3.3 当前网络图第一版实体

```yaml
entities:
  service:
    name_zh: 服务
    label: Service
    alias: s
    description: 业务服务或网络服务实例。
    synonyms: [服务, 业务, 业务服务, 网络服务]
    primary_key: id
    display_fields: [name, id]

  tunnel:
    name_zh: 隧道
    label: Tunnel
    alias: t
    description: 网络隧道实例，例如 MPLS-TE、GRE、IPsec。
    synonyms: [隧道, tunnel]
    primary_key: id
    display_fields: [name, id]

  network_element:
    name_zh: 网元
    label: NetworkElement
    alias: ne
    description: 网络设备，包括路由器、交换机、防火墙等。
    synonyms: [网元, 网络设备, 设备, 路由器, 交换机, 防火墙]
    primary_key: id
    display_fields: [name, id]

  port:
    name_zh: 端口
    label: Port
    alias: p
    description: 物理或逻辑接口。
    synonyms: [端口, 接口]
    primary_key: id
    display_fields: [name, id]

  link:
    name_zh: 链路
    label: Link
    alias: l
    description: 逻辑或物理链路资源。
    synonyms: [链路, 连接]
    primary_key: id
    display_fields: [name, id]

  fiber:
    name_zh: 光纤
    label: Fiber
    alias: f
    description: 光纤资源。
    synonyms: [光纤, 纤芯]
    primary_key: id
    display_fields: [name, id]

  protocol:
    name_zh: 协议
    label: Protocol
    alias: proto
    description: 网络协议定义。
    synonyms: [协议, 网络协议]
    primary_key: id
    display_fields: [name, id]
```

## 4. `dimensions`：维度

### 4.1 作用

`dimensions` 定义描述实体的属性。维度通常用于：

```text
过滤 WHERE
返回 RETURN
分组 GROUP BY / WITH
排序 ORDER BY
```

维度一般是名称、类型、状态、位置、厂商、等级等离散或描述性字段。

### 4.2 参数规范

```yaml
dimensions:
  <dimension_id>:
    name_zh: <中文名称>
    owner: <entity_id>
    property: <TuGraph 属性名>
    description: <业务解释>
    synonyms: [<自然语言同义词>]
    roles: [filter, return, group_by, order_by]
    value_type: string
    enum_values: [<可选枚举值>]
    value_aliases:
      <标准枚举值>: [<自然语言别名>]
```

参数说明：

| 参数 | 必填 | 说明 |
|---|---|---|
| `dimension_id` | 是 | 建议格式：`entity.property` |
| `name_zh` | 是 | 中文业务名称 |
| `owner` | 是 | 所属实体 ID |
| `property` | 是 | TuGraph 真实属性名 |
| `description` | 是 | 业务解释 |
| `synonyms` | 是 | 自然语言同义词 |
| `roles` | 是 | 该维度允许承担的语义角色 |
| `value_type` | 是 | 值类型，如 `string`、`number`、`boolean` |
| `enum_values` | 否 | 枚举值，用于过滤值识别和校验 |
| `value_aliases` | 否 | 枚举值的自然语言别名映射，用于把“金牌”等表达归一化为 `Gold` |

### 4.3 当前网络图第一版维度

```yaml
dimensions:
  service.name:
    name_zh: 服务名称
    owner: service
    property: name
    description: 服务的展示名称。
    synonyms: [服务名称, 业务名称, 名称]
    roles: [filter, return, group_by, order_by]
    value_type: string

  service.quality_of_service:
    name_zh: 服务质量等级
    owner: service
    property: quality_of_service
    description: 服务质量等级，例如 Gold、Silver、Bronze、Best_Effort。
    synonyms: [服务质量, 服务质量等级, 服务等级, QoS]
    roles: [filter, return, group_by, order_by]
    value_type: string
    enum_values: [Gold, Silver, Bronze, Best_Effort]
    value_aliases:
      Gold: [金牌, 黄金级, 高等级]
      Silver: [银牌]
      Bronze: [铜牌]
      Best_Effort: [普通级, 尽力而为]

  service.elem_type:
    name_zh: 服务类型
    owner: service
    property: elem_type
    description: 服务类型。
    synonyms: [服务类型, 业务类型]
    roles: [filter, return, group_by]
    value_type: string

  tunnel.name:
    name_zh: 隧道名称
    owner: tunnel
    property: name
    description: 隧道展示名称。
    synonyms: [隧道名称, 名称]
    roles: [filter, return, group_by, order_by]
    value_type: string

  tunnel.elem_type:
    name_zh: 隧道类型
    owner: tunnel
    property: elem_type
    description: 隧道类型。
    synonyms: [隧道类型]
    roles: [filter, return, group_by]
    value_type: string

  network_element.name:
    name_zh: 网元名称
    owner: network_element
    property: name
    description: 网元展示名称。
    synonyms: [网元名称, 设备名称, 名称]
    roles: [filter, return, group_by, order_by]
    value_type: string

  network_element.vendor:
    name_zh: 厂商
    owner: network_element
    property: vendor
    description: 网元厂商。
    synonyms: [厂商, 设备厂商]
    roles: [filter, return, group_by, order_by]
    value_type: string

  network_element.location:
    name_zh: 网元位置
    owner: network_element
    property: location
    description: 网元所在位置。
    synonyms: [位置, 地点, 网元位置, 设备位置]
    roles: [filter, return, group_by, order_by]
    value_type: string

  port.mac_address:
    name_zh: 端口 MAC 地址
    owner: port
    property: mac_address
    description: 端口 MAC 地址。
    synonyms: [MAC地址, 端口MAC, 端口MAC地址]
    roles: [filter, return]
    value_type: string

  port.status:
    name_zh: 端口状态
    owner: port
    property: status
    description: 端口运行状态。
    synonyms: [端口状态, 接口状态, 状态]
    roles: [filter, return, group_by]
    value_type: string
```

完整资源文件中应继续补齐 `id`、`model`、`software_version`、`link.status`、`fiber.location`、`protocol.standard` 等维度。

## 5. `facts`：事实字段

### 5.1 作用

`facts` 定义实体上的数值事实。事实字段可以直接返回，也可以被指标聚合。

例如：

```text
服务带宽
隧道时延
端口速率
光纤长度
链路 MTU
```

### 5.2 参数规范

```yaml
facts:
  <fact_id>:
    name_zh: <中文名称>
    owner: <entity_id>
    property: <TuGraph 属性名>
    description: <业务解释>
    synonyms: [<自然语言同义词>]
    roles: [return, aggregate, order_by, filter]
    value_type: number
    unit: <可选单位>
    default_aggregations: [sum, avg, max, min]
```

参数说明：

| 参数 | 必填 | 说明 |
|---|---|---|
| `fact_id` | 是 | 建议格式：`entity.property` |
| `name_zh` | 是 | 中文业务名称 |
| `owner` | 是 | 所属实体 |
| `property` | 是 | TuGraph 真实属性名 |
| `description` | 是 | 业务解释 |
| `synonyms` | 是 | 自然语言同义词 |
| `roles` | 是 | 事实字段允许承担的语义角色 |
| `value_type` | 是 | 通常是 `number` |
| `unit` | 否 | 单位 |
| `default_aggregations` | 是 | 可用聚合函数 |

### 5.3 当前网络图第一版事实字段

```yaml
facts:
  service.bandwidth:
    name_zh: 服务带宽
    owner: service
    property: bandwidth
    description: 服务带宽。
    synonyms: [服务带宽, 业务带宽, 带宽]
    roles: [return, aggregate, order_by, filter]
    value_type: number
    default_aggregations: [sum, avg, max, min]

  service.latency:
    name_zh: 服务时延
    owner: service
    property: latency
    description: 服务时延。
    synonyms: [服务时延, 业务时延, 时延]
    roles: [return, aggregate, order_by, filter]
    value_type: number
    default_aggregations: [avg, max, min]

  tunnel.bandwidth:
    name_zh: 隧道带宽
    owner: tunnel
    property: bandwidth
    description: 隧道带宽。
    synonyms: [隧道带宽, 带宽]
    roles: [return, aggregate, order_by, filter]
    value_type: number
    default_aggregations: [sum, avg, max, min]

  tunnel.latency:
    name_zh: 隧道时延
    owner: tunnel
    property: latency
    description: 隧道时延。
    synonyms: [隧道时延, 时延]
    roles: [return, aggregate, order_by, filter]
    value_type: number
    default_aggregations: [avg, max, min]

  port.speed:
    name_zh: 端口速率
    owner: port
    property: speed
    description: 端口速率。
    synonyms: [端口速率, 接口速率, 速率]
    roles: [return, aggregate, order_by, filter]
    value_type: number
    default_aggregations: [avg, max, min]

  fiber.length:
    name_zh: 光纤长度
    owner: fiber
    property: length
    description: 光纤长度。
    synonyms: [光纤长度, 长度]
    roles: [return, aggregate, order_by, filter]
    value_type: number
    default_aggregations: [sum, avg, max, min]
```

## 6. `metrics`：指标

### 6.1 作用

`metrics` 定义业务指标和聚合口径。用户说“数量、总数、平均、最大、最多、最少”时，应优先链接到指标。

指标可以基于：

```text
实体计数
事实字段聚合
路径上下文中的目标实体计数
路径上下文中的事实字段聚合
```

### 6.2 参数规范

```yaml
metrics:
  <metric_id>:
    name_zh: <中文名称>
    description: <业务解释>
    aggregation: <count|count_distinct|sum|avg|max|min>
    target:
      entity: <entity_id>
      field: <可选 dimension/fact id>
    path_semantic: <可选 path_semantic_id>
    synonyms: [<自然语言同义词>]
    default_alias: <Cypher 返回别名>
```

参数说明：

| 参数 | 必填 | 说明 |
|---|---|---|
| `metric_id` | 是 | 指标 ID |
| `name_zh` | 是 | 中文指标名 |
| `description` | 是 | 业务解释 |
| `aggregation` | 是 | 聚合函数 |
| `target` | 是 | 聚合对象 |
| `path_semantic` | 否 | 如果指标依赖路径，引用路径语义 |
| `synonyms` | 是 | 指标自然语言表达 |
| `default_alias` | 是 | 默认返回别名 |

### 6.3 当前网络图第一版指标

```yaml
metrics:
  service.count:
    name_zh: 服务数量
    description: 服务实例数量。
    aggregation: count
    target:
      entity: service
    synonyms: [服务数量, 服务总数, 业务数量, 业务总数]
    default_alias: service_count

  tunnel.count:
    name_zh: 隧道数量
    description: 隧道实例数量。
    aggregation: count
    target:
      entity: tunnel
    synonyms: [隧道数量, 隧道总数]
    default_alias: tunnel_count

  network_element.count:
    name_zh: 网元数量
    description: 网元数量。
    aggregation: count
    target:
      entity: network_element
    synonyms: [网元数量, 设备数量, 网络设备数量]
    default_alias: network_element_count

  service_tunnel_path.network_element_count:
    name_zh: 服务隧道所经网元数量
    description: 服务使用的隧道路径经过的网元数量。
    aggregation: count
    target:
      entity: network_element
    path_semantic: service.tunnel_path
    synonyms: [所经网元数量, 穿过的网元数量, 路径网元数量]
    default_alias: network_element_count

  service_tunnel_destination.network_element_count:
    name_zh: 服务隧道目的网元数量
    description: 服务使用的隧道目的网元数量。
    aggregation: count
    target:
      entity: network_element
    path_semantic: service.tunnel_destination
    synonyms: [目的网元数量, 终点网元数量]
    default_alias: destination_network_element_count
```

## 7. `relationships`：单跳关系

### 7.1 作用

`relationships` 定义图中一条边的业务语义。它是路径语义的基础材料。

### 7.2 参数规范

```yaml
relationships:
  <relationship_id>:
    name_zh: <中文名称>
    from: <source entity_id>
    to: <target entity_id>
    edge: <TuGraph edge label>
    direction: out
    description: <业务解释>
    synonyms: [<自然语言同义词>]
    negative_phrases: [<容易混淆但不应命中的表达>]
```

参数说明：

| 参数 | 必填 | 说明 |
|---|---|---|
| `relationship_id` | 是 | 关系 ID |
| `name_zh` | 是 | 中文名称 |
| `from` | 是 | 起点实体 |
| `to` | 是 | 终点实体 |
| `edge` | 是 | TuGraph 真实边 label |
| `direction` | 是 | 当前第一版统一使用 `out` |
| `description` | 是 | 业务解释 |
| `synonyms` | 是 | 正向触发表达 |
| `negative_phrases` | 否 | 消歧反例 |

### 7.3 当前网络图第一版关系

```yaml
relationships:
  service_uses_tunnel:
    name_zh: 服务使用隧道
    from: service
    to: tunnel
    edge: SERVICE_USES_TUNNEL
    direction: out
    description: 服务使用或承载于某个隧道。
    synonyms: [使用隧道, 承载于隧道, 服务的隧道, 业务使用隧道]

  tunnel_src:
    name_zh: 隧道源网元
    from: tunnel
    to: network_element
    edge: TUNNEL_SRC
    direction: out
    description: 隧道的源端或起点网元。
    synonyms: [源网元, 起点网元, 隧道源端, 源端]
    negative_phrases: [目的网元, 终点网元, 所经网元, 穿过网元]

  tunnel_dst:
    name_zh: 隧道目的网元
    from: tunnel
    to: network_element
    edge: TUNNEL_DST
    direction: out
    description: 隧道的目的端或终点网元。
    synonyms: [目的网元, 终点网元, 目标网元, 隧道目的端, 目的端]
    negative_phrases: [源网元, 起点网元, 所经网元, 穿过网元]

  path_through:
    name_zh: 隧道经过网元
    from: tunnel
    to: network_element
    edge: PATH_THROUGH
    direction: out
    description: 隧道路径经过的网元。
    synonyms: [经过, 路径经过, 所经网元, 穿过网元, 穿过]
    negative_phrases: [源网元, 目的网元, 终点网元]

  has_port:
    name_zh: 网元拥有端口
    from: network_element
    to: port
    edge: HAS_PORT
    direction: out
    description: 网元拥有或包含端口。
    synonyms: [拥有端口, 包含端口, 网元端口, 设备接口]
    negative_phrases: [包含属性, 包含服务质量属性]
```

完整资源文件中应继续补齐 `tunnel_proto`、`link_src`、`link_dst`、`fiber_src`、`fiber_dst`。

## 8. `path_semantics`：路径语义

### 8.1 作用

`path_semantics` 是图语义视图最关键的部分。它定义一个业务短语或业务场景应该走哪条图路径。

例如：

```text
服务隧道目的网元
= Service -[:SERVICE_USES_TUNNEL]-> Tunnel -[:TUNNEL_DST]-> NetworkElement
```

路径语义解决的是：

```text
多个实体和关系词组合起来，应该形成哪条业务路径？
```

### 8.2 参数规范

```yaml
path_semantics:
  <path_semantic_id>:
    name_zh: <中文名称>
    description: <业务解释>
    source_entity: <起点 entity_id>
    target_entity: <终点 entity_id>
    intermediate_entities: [<中间 entity_id>]
    path:
      - relationship: <relationship_id>
    trigger_phrases: [<正向触发表达>]
    negative_phrases: [<反向消歧表达>]
    default_return_fields: [<dimension/fact id>]
```

参数说明：

| 参数 | 必填 | 说明 |
|---|---|---|
| `path_semantic_id` | 是 | 路径语义 ID |
| `name_zh` | 是 | 中文名称 |
| `description` | 是 | 业务解释 |
| `source_entity` | 是 | 路径起点实体 |
| `target_entity` | 是 | 路径终点实体 |
| `intermediate_entities` | 否 | 中间实体 |
| `path` | 是 | 由 relationship 组成的路径 |
| `trigger_phrases` | 是 | 命中该路径的自然语言表达 |
| `negative_phrases` | 否 | 不应命中的表达 |
| `default_return_fields` | 是 | 该路径在明细查询下的默认返回字段 |

### 8.3 当前网络图第一版路径语义

```yaml
path_semantics:
  service.tunnel_binding:
    name_zh: 服务使用隧道
    description: 查询服务使用或承载的隧道。
    source_entity: service
    target_entity: tunnel
    path:
      - relationship: service_uses_tunnel
    trigger_phrases: [服务使用的隧道, 服务承载的隧道, 业务使用隧道]
    default_return_fields: [service.name, tunnel.name]

  service.tunnel_source:
    name_zh: 服务隧道源网元
    description: 查询服务使用的隧道的源网元。
    source_entity: service
    target_entity: network_element
    intermediate_entities: [tunnel]
    path:
      - relationship: service_uses_tunnel
      - relationship: tunnel_src
    trigger_phrases: [服务隧道源网元, 服务使用的隧道源网元, 源网元, 起点网元]
    negative_phrases: [目的网元, 终点网元, 所经网元, 穿过网元]
    default_return_fields: [service.name, tunnel.name, network_element.name]

  service.tunnel_destination:
    name_zh: 服务隧道目的网元
    description: 查询服务使用的隧道的目的网元。
    source_entity: service
    target_entity: network_element
    intermediate_entities: [tunnel]
    path:
      - relationship: service_uses_tunnel
      - relationship: tunnel_dst
    trigger_phrases: [服务隧道目的网元, 服务使用的隧道目的网元, 目的网元, 终点网元, 目标网元]
    negative_phrases: [源网元, 起点网元, 所经网元, 穿过网元]
    default_return_fields: [service.name, tunnel.name, network_element.name]

  service.tunnel_path:
    name_zh: 服务隧道所经网元
    description: 查询服务使用的隧道路径经过的网元。
    source_entity: service
    target_entity: network_element
    intermediate_entities: [tunnel]
    path:
      - relationship: service_uses_tunnel
      - relationship: path_through
    trigger_phrases: [服务隧道所经网元, 所经网元, 穿过网元, 路径经过, 经过的网元]
    negative_phrases: [源网元, 目的网元, 终点网元]
    default_return_fields: [service.name, tunnel.name, network_element.name]

  network_element.port:
    name_zh: 网元端口
    description: 查询网元拥有的端口。
    source_entity: network_element
    target_entity: port
    path:
      - relationship: has_port
    trigger_phrases: [网元端口, 设备接口, 包含端口, 拥有端口]
    negative_phrases: [包含属性, 包含服务质量属性]
    default_return_fields: [network_element.name, port.name]
```

## 9. `return_policies`：默认返回策略

### 9.1 作用

`return_policies` 定义当自然语言没有完全明确列出返回字段时，系统应该返回哪些字段。

它解决的问题包括：

```text
服务及其隧道名称 -> 返回 service.name 和 tunnel.name
服务使用的隧道及其目的网元 -> 返回 service.name、tunnel.name、network_element.name
查询目的网元厂商 -> 返回 network_element.vendor
```

### 9.2 参数规范

```yaml
return_policies:
  <policy_id>:
    name_zh: <中文名称>
    applies_to:
      path_semantics: [<path_semantic_id>]
      entities: [<entity_id>]
    rules:
      - when: <触发条件>
        return_fields: [<dimension/fact id>]
```

### 9.3 第一版默认策略

```yaml
return_policies:
  path_entities_default:
    name_zh: 路径两端实体默认返回
    applies_to:
      path_semantics: [service.tunnel_binding, service.tunnel_destination, service.tunnel_path]
    rules:
      - when: question_mentions_source_and_target_entities
        return_fields: [source.display_fields, target.display_fields]
      - when: question_mentions_specific_target_property
        return_fields: [mentioned_properties]

  mentioned_fields_default:
    name_zh: 显式提及字段默认返回
    applies_to:
      entities: [service, tunnel, network_element, port, link, fiber, protocol]
    rules:
      - when: question_has_parallel_attributes
        return_fields: [all_mentioned_returnable_dimensions_and_facts]

  filter_field_can_also_return:
    name_zh: 过滤字段可同时返回
    applies_to:
      entities: [service, tunnel, network_element, port, link, fiber, protocol]
    rules:
      - when: same_field_appears_in_filter_and_return_phrase
        return_fields: [that_field]
```

## 10. `disambiguation_rules`：消歧规则

### 10.1 作用

`disambiguation_rules` 定义容易混淆的自然语言表达应该如何选择语义对象。

### 10.2 参数规范

```yaml
disambiguation_rules:
  - rule_id: <规则 ID>
    name_zh: <中文名称>
    positive_patterns: [<应该命中的表达>]
    negative_patterns: [<不应命中的表达>]
    prefer: <semantic object id>
    reject: [<semantic object id>]
    explanation: <解释>
```

### 10.3 第一版消歧规则

```yaml
disambiguation_rules:
  - rule_id: destination_ne_prefers_tunnel_dst
    name_zh: 目的网元优先 TUNNEL_DST
    positive_patterns: [目的网元, 终点网元, 目标网元, 目的端]
    prefer: service.tunnel_destination
    reject: [service.tunnel_path, tunnel.path_through]
    explanation: 目的或终点表达的是隧道端点，不是路径经过网元。

  - rule_id: path_ne_prefers_path_through
    name_zh: 所经网元优先 PATH_THROUGH
    positive_patterns: [所经网元, 穿过网元, 路径经过, 经过的网元]
    prefer: service.tunnel_path
    reject: [service.tunnel_destination, service.tunnel_source]
    explanation: 所经或穿过表达的是路径经过节点。

  - rule_id: property_contains_is_not_has_port
    name_zh: 包含属性不是 HAS_PORT
    positive_patterns: [包含服务质量属性, 包含.*属性]
    prefer: property_existence_semantics
    reject: [has_port, network_element.port]
    explanation: 包含属性描述字段存在或字段统计，不是网元到端口的图关系。

  - rule_id: contains_port_can_use_has_port
    name_zh: 包含端口可使用 HAS_PORT
    positive_patterns: [包含端口, 拥有端口, 设备接口, 网元端口]
    prefer: network_element.port
    explanation: 端口是实体，包含端口表达网元到端口关系。
```

## 11. `verified_examples`：已验证示例

第一版先不建立 `verified_examples`。

原因是第一阶段的重点是把语义视图本身建清楚，并通过实验观察实体、字段、路径语义、返回策略和消歧规则是否足够稳定。如果过早沉淀已验证示例，容易把尚未验证稳定的样本、旧 schema 残留或临时修补逻辑写进语义视图，反而干扰后续判断。

第一版 YAML 不应包含 `verified_examples` 顶层字段。

后续在完成若干轮运行中心实验后，再从以下来源筛选高质量样本建立该模块：

```text
1. qa-agent 生成且 golden Cypher 与当前真实 schema 对齐的样本
2. 运行中心中已确认语义正确、执行通过的样本
3. 曾经失败但通过通用语义规则修复后的代表性样本
4. 人工补充的核心业务路径样本
```

进入 `verified_examples` 的样本必须满足：

```text
1. 自然语言问题与目标语义一致
2. 引用的 entity、dimension、fact、metric、relationship、path_semantic 都来自当前语义视图
3. 目标 Cypher 能在当前真实 TuGraph schema 上执行
4. 该样本覆盖的是通用能力，不是针对单条问题的过拟合补丁
```

## 12. 语义视图匹配流程

### 12.1 目标和边界

语义视图匹配模块的目标，是把自然语言问题中的业务表达链接到图语义视图中的正式语义对象。

它输入自然语言问题、`network_graph_semantic_view.yaml` 和真实 TuGraph schema 快照，输出 `SemanticMatchResult`，供后续 planner 生成 `LogicalQueryPlan`。

它负责：

```text
1. 找到问题中提到的实体、字段、指标、关系和路径语义
2. 识别过滤值、比较操作、排序和 limit 等查询约束
3. 在多条合法候选之间做可解释消歧
4. 给 planner 输出结构化、可校验的业务语义匹配结果
```

它不负责：

```text
1. 生成 Cypher
2. 发明语义视图中不存在的实体、字段、关系或路径
3. 绕过真实 TuGraph schema 校验
4. 在业务信息不足时强行猜测
```

### 12.2 输入和输出

输入包括：

```text
1. 用户自然语言问题
2. network_graph_semantic_view.yaml
3. 当前真实 TuGraph schema 快照
```

输出是 `SemanticMatchResult`：

```jsonc
{
  // accepted 表示语义视图匹配是否成功。true 表示已经得到可交给 planner 的结构化匹配结果。
  "accepted": true,

  // entities 表示问题中识别出的业务实体 ID，必须来自语义视图 entities。
  "entities": ["service", "tunnel"],

  // filters 表示自然语言中识别出的过滤条件。
  // field 必须来自 dimensions 或 facts；operator 是标准比较操作符；value 是归一化后的值。
  "filters": [
    {
      "field": "service.quality_of_service",
      "operator": "=",
      "value": "Gold",
      "evidence": "金牌"
    }
  ],

  // paths 表示问题需要使用的业务路径语义。
  // path_semantic 必须来自 path_semantics；relationships 是该路径展开后的单跳关系链。
  "paths": [
    {
      "path_semantic": "service.uses_tunnel",
      "relationships": ["service_uses_tunnel"],
      "evidence": "使用的隧道"
    }
  ],

  // returns 表示候选返回字段，必须来自 dimensions、facts、metrics 或 return_policies 推导结果。
  "returns": [
    {
      "field": "tunnel.name",
      "evidence": "隧道名称"
    }
  ],

  // needs_clarification 表示是否需要向用户澄清。false 表示当前匹配结果足够明确。
  "needs_clarification": false,

  // clarification_type 表示澄清原因类型。
  // semantic_ambiguity 表示业务实体、字段或路径不明确；
  // return_ambiguity 表示返回字段不明确；path_ambiguity 表示多条路径语义无法区分。
  "clarification_type": null,

  // clarification_question 只有在 needs_clarification=true 时出现，用中文说明需要用户补充什么。
  "clarification_question": null,

  // clarification_options 表示可提供给用户选择的澄清选项。
  // value 必须映射到语义视图对象或语义匹配内部选项，不能是自由文本。
  "clarification_options": [],

  // trace 记录关键匹配证据，用于运行中心展示和失败分析。
  "trace": [
    "服务 -> entity service",
    "金牌 -> service.quality_of_service = Gold",
    "使用的隧道 -> path_semantics service.uses_tunnel",
    "隧道名称 -> return tunnel.name"
  ]
}
```

### 12.3 流程总览

语义视图匹配模块按以下流程执行：

本节所有输入输出示例都在 YAML 注释中直接解释字段含义。同名字段在不同阶段含义保持一致，只有语义发生变化时才再次说明。

**步骤一：候选生成**

具体工作：

1. 读取本次请求的原始问题、语义视图和真实 TuGraph schema 快照，确认后续匹配使用的是同一份视图和 schema。
2. 从语义视图派生运行时匹配索引，索引来源包括实体、字段、指标、关系、路径语义、返回策略、消歧规则和字段取值别名。
3. 对原始问题做轻量文本归一化，识别稳定表达，例如“金牌”“服务”“使用的隧道”“隧道名称”，并记录原文片段、字符位置和来源引用。
4. 按最长短语优先、精确表达优先的规则召回初始候选，候选可以是实体、字段、指标、关系、路径语义、字段取值或返回策略。
5. 为每个候选保留证据，包括命中的原文、命中方式、语义视图来源和候选 ID。
6. 本阶段只做宽召回，不判断最终路径是否唯一，不生成过滤条件闭环，也不决定最终返回字段。

输入：用户自然语言问题；`network_graph_semantic_view.yaml`；真实 TuGraph schema 快照。

输入示例：

```yaml
# 候选生成阶段的输入根对象。
candidate_generation_input:
  # 用户输入的原始自然语言问题，不做改写。
  question: 查询金牌服务使用的隧道名称

  # 本次匹配使用的语义视图。
  semantic_view:
    # 语义视图 ID。
    view_id: network_graph_semantic_view
    # 语义视图 YAML 的来源文件。
    source: network_graph_semantic_view.yaml

  # 当前真实 TuGraph schema 快照。
  schema_snapshot:
    # schema 快照或版本标识。
    graph: network_schema_v10
    # schema 快照来源，通常是 schema 文件或 schema 服务。
    source: schema.json
```

输出：`SemanticCandidateSet` 初始候选集合。

输出示例：

```yaml
# 候选生成阶段的输出根对象。
semantic_candidate_set:
  # 与输入一致的原始问题，用于 trace 和运行中心展示。
  question: 查询金牌服务使用的隧道名称

  # 从问题中识别出的文本命中片段及其归一化结果。
  normalized_mentions:
    # 本次请求内的临时文本命中 ID，不来自语义视图 YAML。
    - mention_id: m_value_gold
      # 原始问题中被命中的文本。
      text: 金牌
      # 该文本归一化后的标准字段取值。
      normalized: Gold
      # 该命中对应的语义视图配置位置。
      source_ref: dimensions.service.quality_of_service.value_aliases.Gold
      # 原始问题中的字符位置，使用 0-based Unicode 字符偏移，格式为 [start, end)。
      span: [2, 4]
    - mention_id: m_entity_service
      text: 服务
      # 该文本归一化后的实体 ID。
      normalized: service
      span: [4, 6]
    - mention_id: m_path_service_tunnel
      text: 使用的隧道
      # 该文本归一化后的业务路径语义 ID。
      normalized: service.uses_tunnel
      span: [6, 11]
    - mention_id: m_field_tunnel_name
      text: 隧道名称
      # 该文本归一化后的字段 ID。
      normalized: tunnel.name
      span: [9, 13]

  # 基于文本命中召回出的初始候选对象。
  candidates:
    # 本次请求内的临时候选 ID，不来自语义视图 YAML。
    - candidate_id: c_entity_service
      # 候选在语义视图匹配流程中的分类，不直接来自 TuGraph schema。
      candidate_type: entity
      # 候选指向的语义视图对象 ID 或字段路径。
      target_id: service
      # 触发该候选的原始文本。
      matched_text: 服务
      # 命中方式，例如中文名、字段取值别名、触发短语、复合短语。
      match_method: name_zh
      # 候选产生的可解释证据，供运行中心展示和问题排查。
      evidence: [命中 entities.service.name_zh]
    - candidate_id: c_value_gold
      # 字段取值候选，来源是维度字段的 enum_values / value_aliases。
      candidate_type: field_value
      # 候选指向的字段取值，格式为 <field_id>.<standard_value>。
      target_id: service.quality_of_service.Gold
      # 该取值属于哪个语义视图字段。
      field_id: service.quality_of_service
      # 归一化后的标准取值。
      value: Gold
      matched_text: 金牌
      match_method: value_alias
      evidence: [命中 dimensions.service.quality_of_service.value_aliases.Gold]
    - candidate_id: c_path_service_tunnel
      candidate_type: path_semantic
      target_id: service.uses_tunnel
      matched_text: 使用的隧道
      match_method: trigger_phrase
      evidence: [命中 path_semantics.service.uses_tunnel.trigger_phrases]
    - candidate_id: c_field_tunnel_name
      candidate_type: dimension
      target_id: tunnel.name
      matched_text: 隧道名称
      match_method: compound_phrase
      evidence: [命中 entity tunnel + dimension name]
```

功能：宽召回可能相关的语义视图对象，不做最终选择。

**步骤二：语义补全**

具体工作：

1. 根据步骤一输出的候选 ID 回查语义视图对象定义，确认每个候选对应的实体、字段、路径语义或字段取值。
2. 将字段取值候选补全为过滤条件，例如把“金牌”补全为 `service.quality_of_service = Gold`。
3. 将路径语义候选展开为关系链，例如把 `service.uses_tunnel` 展开为对应的单跳或多跳关系。
4. 根据实体候选、字段候选和路径候选推导本阶段需要校验的 label、edge 和 property，形成 `derived_schema_constraints`。
5. 使用真实 TuGraph schema 校验这些 label、edge 和 property 是否存在，删除无法落到真实 schema 的候选。
6. 根据显式字段表达和 `return_policies` 生成返回字段候选，例如把“隧道名称”补全为 `tunnel.name`。
7. 保留被拒绝候选及原因，避免后续只看到“没有结果”却不知道是 schema 不存在、字段归属冲突还是路径无法连通。

输入：初始候选集合；归一化片段；语义视图对象定义；真实 schema 快照。

输入示例：

```yaml
# 语义补全阶段的输入根对象。
semantic_completion_input:
  # 原始自然语言问题。
  question: 查询金牌服务使用的隧道名称
  # 引用上一步生成的候选集合。
  candidate_set_ref: semantic_candidate_set
  # 引用本次匹配使用的语义视图，用于回查候选对象定义。
  semantic_view_ref: network_graph_semantic_view
  # 引用当前真实 TuGraph schema 快照，用于校验 label、edge、property 是否存在。
  schema_snapshot_ref: network_schema_v10
  # 进入语义补全的候选 ID 列表。
  candidate_ids:
    - c_entity_service
    - c_value_gold
    - c_path_service_tunnel
    - c_field_tunnel_name
```

输出：经过补全和硬过滤的合法候选集合。

输出示例：

```yaml
# 语义补全阶段的输出根对象。
semantic_completion_result:
  # 本阶段根据候选、语义视图和真实 schema 推导出的硬校验约束。
  derived_schema_constraints:
    # 规划该问题时预计必须存在的 TuGraph 点标签。
    required_labels: [Service, Tunnel]
    # 规划该问题时预计必须存在的 TuGraph 边类型。
    required_edges: [SERVICE_USES_TUNNEL]
    # 规划该问题时预计必须存在的属性，按 label 分组。
    required_properties:
      Service: [quality_of_service]
      Tunnel: [name]

  # 通过语义视图和真实 schema 硬约束校验的候选集合。
  accepted_candidates:
    # 已确认参与查询的业务实体 ID。
    entities:
      - service
      - tunnel
    # 已补全的过滤条件。
    filters:
      # 过滤字段，必须来自语义视图并能映射到真实 schema。
      - field: service.quality_of_service
        # 标准比较操作符。
        operator: "="
        # 归一化后的过滤值。
        value: Gold
        # 该条件来自原始问题的哪段表达。
        evidence: 金牌
    # 通过补全得到的合法路径语义候选。
    path_candidates:
      # 语义视图中定义的业务路径 ID。
      - path_semantic: service.uses_tunnel
        # 该业务路径展开后的单跳关系链。
        relationships: [service_uses_tunnel]
        evidence: 使用的隧道
    # 候选返回字段集合。
    return_candidates:
      # 返回字段，必须来自语义视图并能映射到真实 schema。
      - field: tunnel.name
        evidence: 隧道名称
  # 被 schema 或语义规则淘汰的候选，用于诊断。
  rejected_candidates: []
```

功能：把零散候选补成可规划的业务语义片段，并删除不可能落到 schema 的候选。

**步骤三：过滤与排序**

具体工作：

1. 检查步骤二输出的合法候选是否足够形成查询语义；如果没有合法实体、字段或路径候选，应提前返回无法匹配。
2. 对候选执行硬规则过滤，例如 schema 不存在、字段 owner 冲突、路径关系链不连通、负向规则命中。
3. 对剩余候选执行可解释排序，优先级包括精确触发词、完整短语、路径语义命中、实体上下文一致、字段角色一致和消歧规则命中。
4. 判断是否存在强规则唯一命中；如果存在，输出 `decision: accept` 和被接受的实体、过滤条件、路径、返回字段。
5. 如果多个候选都合法且分数接近，输出 `decision: disambiguate`，并只保留少量可解释候选进入下一步。
6. 如果候选都被拒绝，输出拒绝原因，不进入 LLM 消歧。
7. 本阶段不调用 LLM，只产出可解释决策和 trace。

输入：合法候选集合；`disambiguation_rules`；字段角色和路径上下文。

输入示例：

```yaml
# 过滤与排序阶段的输入根对象。
candidate_ranking_input:
  # 上一步保留下来的合法候选集合。
  accepted_candidates:
    # 已确认或高置信的业务实体候选。
    entities: [service, tunnel]
    # 参与排序判断的过滤条件候选。
    filters:
      - field: service.quality_of_service
        operator: "="
        value: Gold
    # 参与排序判断的路径语义候选。
    path_candidates:
      - path_semantic: service.uses_tunnel
        evidence: 使用的隧道
    # 参与排序判断的返回字段候选。
    return_candidates:
      - field: tunnel.name
        evidence: 隧道名称
  # 本次排序可使用的消歧规则 ID 列表。
  disambiguation_rules:
    - path_ne_prefers_path_through
    - destination_ne_prefers_tunnel_dst
```

输出：已接受候选，或待消歧候选集合，或 `accepted=false`。

输出示例：

```yaml
# 过滤与排序阶段的输出根对象。
candidate_ranking_result:
  # 排序决策，accept 表示已经可以接受当前候选。
  decision: accept
  # 产生该决策的中文原因。
  reason: 强路径触发词命中且无竞争候选
  # 被接受并准备进入最终输出的结构化候选。
  accepted:
    # 被接受的业务实体 ID。
    entities: [service, tunnel]
    # 被接受的过滤条件。
    filters:
      - field: service.quality_of_service
        operator: "="
        value: Gold
    # 被接受的主业务路径语义 ID。
    path: service.uses_tunnel
    # 被接受的返回字段列表。
    returns:
      - tunnel.name
  # 关键证据链，用于解释为什么接受该候选。
  trace:
    - 使用的隧道 -> service.uses_tunnel
    - 隧道名称 -> tunnel.name
```

需要消歧时的输出示例：

```yaml
# 过滤与排序阶段的输出根对象。
candidate_ranking_result:
  # 排序决策，disambiguate 表示需要进入消歧。
  decision: disambiguate
  # 进入消歧的原因。
  reason: 多条路径语义候选接近，缺少强消歧词
  # 供工程规则或受控 LLM 继续选择的少量候选。
  candidates:
    # 消歧选项编号，只在本次消歧内有效。
    - option_id: A
      # 该选项指向的语义视图对象 ID。
      target_id: service.tunnel_source
      # 展示给用户或模型看的中文选项名。
      label: 源网元
    - option_id: B
      target_id: service.tunnel_destination
      label: 目的网元
    - option_id: C
      target_id: service.tunnel_path
      label: 路径经过的网元
```

功能：在不使用 LLM 的情况下尽量确定候选；无法匹配或语义视图未覆盖时提前结束。

**步骤四：消歧与输出**

具体工作：

1. 如果步骤三已经接受唯一候选，将其转换为 `SemanticMatchResult`，并补齐实体、过滤条件、路径、返回字段和 trace。
2. 如果步骤三输出待消歧候选，先用工程规则判断是否必须澄清，例如“对应网元”无法区分源网元、目的网元和路径经过网元。
3. 如果候选数量少、候选都合法、且问题表达仍有可判断空间，可以进入受控 LLM 消歧。
4. 受控 LLM 只能在给定候选编号中选择，或返回“需要澄清”；它不能创造实体、字段、关系、路径，也不能生成 Cypher。
5. 对 LLM 返回做强校验，确认选择值在允许选项内，并再次通过语义视图和真实 schema 校验。
6. 如果无法可靠选择，输出 `needs_clarification=true`，同时给出中文澄清问题和候选选项。
7. 如果候选不合法或语义视图未覆盖，输出 `accepted=false`，并保留拒绝原因供运行中心展示。
8. 最终输出只负责形成可交给 planner 的结构化语义结果，不直接生成 Cypher。

输入：待消歧候选集合；候选中文解释；允许输出选项；消歧规则。

输入示例：

```yaml
# 消歧阶段的输入根对象。
semantic_disambiguation_input:
  # 原始自然语言问题。
  question: 查询服务对应的网元
  # 经过过滤排序后仍然无法唯一确定的少量候选。
  candidates:
    # 消歧选项编号，只在本次消歧中有效。
    - option_id: A
      # 候选指向的语义视图对象 ID。
      target_id: service.tunnel_source
      # 候选的中文短标签，用于展示和模型选择。
      label: 源网元
      # 候选的中文业务解释，用于帮助模型或用户理解差异。
      description: 服务使用的隧道的源网元
    - option_id: B
      target_id: service.tunnel_destination
      label: 目的网元
      description: 服务使用的隧道的目的网元
    - option_id: C
      target_id: service.tunnel_path
      label: 路径经过的网元
      description: 服务使用的隧道路径经过的网元
  # 允许返回的选项集合；clarify 表示需要澄清。
  allowed_outputs: [A, B, C, clarify]
```

输出：`SemanticMatchResult`，可能是 `accepted=true`、`needs_clarification=true` 或 `accepted=false`。

输出示例：

```yaml
# 语义视图匹配模块的最终输出根对象。
semantic_match_result:
  # 是否已经形成可交给 planner 的确定匹配结果。
  accepted: false
  # 是否需要向用户发起澄清反问。
  needs_clarification: true
  # 澄清原因类型，例如语义对象不明确、路径不明确、返回字段不明确。
  clarification_type: semantic_ambiguity
  # 展示给用户的中文澄清问题。
  clarification_question: 你说的“对应网元”是指源网元、目的网元，还是路径经过的网元？
  # 用户可选择的澄清选项。
  clarification_options:
    # 澄清选项的中文展示名。
    - label: 源网元
      # 澄清选项对应的语义视图对象 ID 或内部候选 ID。
      value: service.tunnel_source
    - label: 目的网元
      value: service.tunnel_destination
    - label: 路径经过的网元
      value: service.tunnel_path
```

匹配成功时的输出示例：

```yaml
# 语义视图匹配模块的最终输出根对象。
semantic_match_result:
  # true 表示匹配成功，可以进入 planner。
  accepted: true
  # false 表示不需要澄清。
  needs_clarification: false
  # 最终确认的业务实体 ID 列表。
  entities: [service, tunnel]
  # 最终确认的过滤条件列表。
  filters:
    # 过滤字段，必须来自语义视图并映射到真实 schema。
    - field: service.quality_of_service
      # 过滤条件使用的比较操作符。
      operator: "="
      # 归一化后的过滤值。
      value: Gold
      # 该条件对应的原始问题证据。
      evidence: 金牌
  # 最终确认的业务路径语义列表。
  paths:
    # 语义视图中定义的业务路径 ID。
    - path_semantic: service.uses_tunnel
      # 该路径展开到真实图 schema 后需要使用的关系链。
      relationships: [service_uses_tunnel]
      evidence: 使用的隧道
  # 最终确认的返回字段列表。
  returns:
    # 返回字段。
    - field: tunnel.name
      evidence: 隧道名称
  # 从原始问题到最终语义对象的关键匹配证据链。
  trace:
    - 服务 -> entity service
    - 金牌 -> service.quality_of_service = Gold
    - 使用的隧道 -> path_semantics service.uses_tunnel
    - 隧道名称 -> return tunnel.name
```

功能：在少量候选之间做最终消歧，并形成可交给 planner 的结构化输出。

### 12.4 后续小节的职责

12.3 描述完整流程、输入输出和数据结构示例。12.4 到 12.11 聚焦说明每个内部机制如何落地。

可以按下面的关系理解：

| 小节 | 回答的问题 | 对应 12.3 阶段 |
|---|---|---|
| 12.5 匹配索引 | 从语义视图里派生哪些检索结构 | 候选生成 |
| 12.6 文本归一化 | 自然语言片段如何变成标准信号 | 候选生成、语义补全 |
| 12.7 候选集合 | 候选里应该包含哪些对象和字段 | 候选生成、语义补全 |
| 12.8 过滤排序 | 如何淘汰非法候选并选择更可信候选 | 过滤与排序 |
| 12.9 受控 LLM 消歧 | 什么时候允许模型参与，以及模型只能做什么 | 消歧与输出 |
| 12.10 澄清反问 | 哪些情况需要反问用户 | 消歧与输出 |
| 12.11 输出契约 | 最终结果如何交给 planner 和运行中心 | 消歧与输出 |

### 12.5 从语义视图派生匹配索引

匹配索引不是新的权威数据源，而是从 `network_graph_semantic_view.yaml` 派生出的运行时检索结构。语义视图仍然是唯一权威配置；索引只负责加速候选召回、减少重复扫描，并保留来源引用。

| 语义视图模块 | 派生索引 | 主要用途 |
|---|---|---|
| `entities` | 实体词索引 | 识别服务、隧道、网元、端口等业务对象 |
| `dimensions` | 描述字段索引 | 识别名称、服务质量、厂商、状态、位置等字段 |
| `facts` | 数值字段索引 | 识别带宽、时延、长度、速率等可比较字段 |
| `metrics` | 指标索引 | 识别数量、平均值、最大值、最小值等统计表达 |
| `relationships` | 单跳关系索引 | 识别使用、包含、源端、目的端等基础图关系 |
| `path_semantics` | 业务路径索引 | 识别服务使用隧道、隧道到目的网元、隧道经过网元等路径语义 |
| `return_policies` | 返回策略索引 | 识别“相关信息”“及其”等返回补齐表达 |
| `disambiguation_rules` | 消歧信号索引 | 识别目的、源、经过、穿过、对应等区分性表达 |
| `dimensions` / `facts` 中的 `enum_values` 和 `value_aliases` | 字段取值索引 | 识别金牌到 Gold、银牌到 Silver 等值标准化 |

派生索引记录建议包含以下字段：

```yaml
# 从语义视图派生出的运行时索引记录。
derived_match_index:
  # 可被自然语言问题命中的文本表达。
  phrase: 目的网元
  # 命中后指向的语义对象类型。
  target_type: path_semantic
  # 命中后指向的语义视图对象 ID。
  target_id: service.tunnel_destination
  # 该索引项来自语义视图中的哪个模块。
  source_module: path_semantics
  # 该索引项来自模块中的哪个字段。
  source_field: trigger_phrases
  # 该索引项的命中优先级或命中类型。
  priority: exact_phrase
```

索引可以在服务启动时构建，也可以在语义视图变更后重建。第一版不要求单独维护一份人工索引文件，避免出现语义视图和索引内容不一致的问题。

### 12.6 文本归一化规则

文本归一化不是自由改写，也不是让模型重写用户问题。它只把问题中的稳定表达转换成标准信号，并保留原始证据。

归一化规则来源包括：

```text
1. entities、dimensions、facts、metrics 中的 name_zh 和 synonyms
2. path_semantics 中的 trigger_phrases
3. disambiguation_rules 中的 positive_patterns 和 negative_patterns
4. dimensions / facts 中的 enum_values 和 value_aliases
5. 系统通用操作符、排序和 limit 规则
```

归一化结果只作为候选生成和排序的辅助信号，不直接等同于最终匹配结果。

```yaml
# 归一化后的文本片段集合。
normalized_mentions:
  # 原始问题中的命中文本。
  - text: 金牌
    # 归一化后的标准值、操作符或语义对象引用。
    normalized_value: Gold
    # 归一化信号类型；field_value 表示某个语义字段的标准取值。
    signal_type: field_value
    # 归一化规则来源，可以来自语义视图或系统规则。
    source: dimensions.service.quality_of_service.value_aliases.Gold
  - text: 前五个
    normalized_value: 5
    signal_type: limit
    source: system.limit_rules
  - text: 大于
    normalized_value: ">"
    signal_type: operator
    source: system.operator_rules
```

第一版不使用 LLM 做归一化；后续即使引入模型，也只能生成候选信号，不能直接改写最终结构。

### 12.7 候选集合结构

候选集合是候选生成阶段的统一中间结果。它不区分“先识别字段还是先识别路径”，而是把所有可能参与查询规划的语义对象放到同一个集合里，供后续补全、过滤和排序。

候选集合应至少包含以下类型：

```text
1. entity_candidate：实体候选，例如 service、tunnel、network_element
2. field_candidate：字段候选，例如 service.name、service.quality_of_service、tunnel.bandwidth
3. metric_candidate：指标候选，例如 service_count、avg_tunnel_latency
4. relationship_candidate：单跳关系候选，例如 service_uses_tunnel、tunnel_dst
5. path_candidate：业务路径候选，例如 service.uses_tunnel、service.tunnel_destination
6. field_value_candidate：字段取值候选，例如 service.quality_of_service.Gold、link.status.down
7. return_candidate：返回候选，例如 tunnel.name、service.name
8. policy_candidate：返回策略候选，例如 related_record_default
```

每个候选都应带上来源和证据，便于解释和回放。

```yaml
# 单个语义候选对象。
candidate:
  # 本次请求内的临时候选 ID。
  candidate_id: c_path_service_tunnel
  # 候选类型，例如实体、字段、路径、指标。
  candidate_type: path_candidate
  # 候选指向的语义视图对象 ID。
  target_id: service.uses_tunnel
  # 触发该候选的原始问题证据。
  evidence: 使用的隧道
  # 候选来自语义视图中的哪个模块。
  source_module: path_semantics
  # 候选来自模块中的哪个字段。
  source_field: trigger_phrases
  # 候选的命中方式。
  matched_by: exact_phrase
  # 候选可能使用到的真实 schema 对象引用。
  schema_refs:
    # 候选涉及的 TuGraph 点标签。
    labels: [Service, Tunnel]
    # 候选涉及的 TuGraph 边类型。
    edges: [SERVICE_USES_TUNNEL]
```

字段取值与过滤条件也作为候选的一部分处理。比如“金牌服务”不是单独流程的最终结论，而是先形成 `field_value_candidate`，再结合上下文补全为 `service.quality_of_service = Gold`。

```yaml
# 候选过滤条件。
filter_candidate:
  # 过滤字段，必须来自语义视图并映射到真实 schema。
  field: service.quality_of_service
  # 标准比较操作符。
  operator: "="
  # 归一化后的过滤值。
  value: Gold
  # 过滤条件来自原始问题的哪段文本。
  evidence: 金牌
  # 过滤条件的来源信息。
  source:
    # 字段取值别名来源。
    value_aliases: dimensions.service.quality_of_service.value_aliases.Gold
    # 字段所属的业务实体。
    field_owner: service
```

路径候选优先来自 `path_semantics`。只有没有显式业务路径命中时，才允许根据已识别实体在关系图中做短路径搜索；短路径搜索得到的是低优先级候选，不能覆盖强路径语义。

### 12.8 候选过滤、排序和 trace

过滤排序只处理候选质量，不重新解释完整问题。它的目标是把候选集合缩小为三类结果：可接受、需要消歧、无法匹配。

硬约束用于淘汰非法候选：

```text
1. 语义对象不在语义视图中，淘汰
2. label、edge、property 不在真实 TuGraph schema 中，淘汰
3. path_semantics 展开的关系链不连通，淘汰
4. 字段 owner 与候选实体完全冲突，降级或淘汰
```

排序规则用于选择更合理的候选：

```text
1. 精确 trigger 命中优先于普通同义词命中
2. 完整短语命中优先于单个泛词命中
3. path_semantics 命中优先于自动短路径搜索
4. field_value 命中优先形成过滤条件
5. 与已识别实体上下文一致的字段优先
6. 与字段角色和路径上下文一致的候选优先
7. disambiguation_rules 明确 prefer 的候选优先
8. negative_patterns 命中的候选降级或拒绝
```

第一版不使用训练模型分数，可以使用等级分作为工程启发式。分数不是业界固定标准，必须通过运行中心实验样本逐步校准。

```text
精确路径触发词命中：100
完整同义短语命中：90
实体上下文一致：+10
路径可连通：+10
消歧规则强命中：+20
泛词命中：40
字段归属冲突：-30
负向规则命中：拒绝或大幅降级
```

每个被接受、降级、拒绝或送入消歧的候选都必须保留 trace。

```yaml
# 单个候选的解释记录。
candidate_trace:
  # 被解释的候选 ID。
  candidate_id: c_path_destination
  # 候选指向的语义视图对象 ID。
  target_id: service.tunnel_destination
  # 对该候选的处理结果，例如接受、降级、拒绝、送入消歧。
  decision: accepted
  # 产生该处理结果的中文原因列表。
  reasons:
    - 命中短语“目的网元”
    - service 与 network_element 可通过 service -> tunnel -> TUNNEL_DST 连通
    - 消歧规则中“目的”优先 service.tunnel_destination
```

### 12.9 受控 LLM 消歧

LLM 只在候选已经由工程规则筛选出来、但无法稳定选择唯一结果时使用。它不是自由生成器，只能在给定候选中选择，或者返回“需要澄清”。

触发受控 LLM 消歧的条件：

```text
1. 存在 2 到 5 个合法候选，且排序结果接近
2. 同一自然语言短语可以解释为多个路径语义
3. 字段可同时作为过滤字段和返回字段，规则无法判断
4. disambiguation_rules 显式标记需要模型辅助选择
5. 用户表达较泛，但仍有少量业务上合理的候选
```

不触发 LLM 的情况：

```text
1. 没有任何合法候选
2. 最高候选已经由强规则确定
3. 候选引用了不存在的 schema 对象
4. 用户缺少必要业务信息，只能澄清
5. 需要生成 Cypher 或补造路径
```

提示词必须是小型选择题，不能把全量语义视图灌入提示词。

```text
你是 cypher-generator-agent 的语义视图候选消歧器。

任务：
从下面少量候选中选择最符合用户问题的语义对象。
你只能选择候选编号，不能创造新实体、新字段、新关系、新路径，不能生成 Cypher。
如果问题表达不足以区分候选，请选择“需要澄清”。

候选字段含义：
- 候选编号：允许你选择的选项。
- 业务含义：这个候选在业务上代表什么。
- 适用表达：哪些自然语言表达通常指向这个候选。
- 不适用表达：哪些表达不应该选择这个候选。

用户问题：
{question}

候选：
A. 业务含义：服务使用的隧道的源网元
   适用表达：源网元、起点网元、来源设备

B. 业务含义：服务使用的隧道的目的网元
   适用表达：目的网元、终点网元、目标设备

C. 业务含义：服务使用的隧道经过的网元
   适用表达：经过的网元、穿过的设备、路径上的网元

D. 需要澄清

输出 JSON：
{
  "decision": "A|B|C|D",
  "reason": "一句中文理由"
}
```

LLM 返回后必须做强校验：

```text
1. decision 必须是允许选项之一
2. 不能返回候选之外的语义对象
3. 不能包含 Cypher
4. 选择结果必须再次通过语义视图和真实 schema 校验
```

### 12.10 澄清反问契约

澄清反问由语义视图匹配模块的工程规则触发。LLM 不是澄清范围的触发器，只能在已进入受控消歧流程后返回“无法可靠选择”。

触发澄清反问的情况包括：

```text
1. 多个合法语义对象候选分数接近，且缺少强消歧词
2. 同一个自然语言短语可以落到多条 path_semantics
3. 字段归属不明确，且上下文不能确定 owner
4. 返回字段不明确，return_policies 无法稳定补齐
5. 用户使用泛化关系词，且 disambiguation_rules 没有强规则
6. 受控 LLM 消歧后返回“需要澄清”
```

不触发澄清反问的情况包括：

```text
1. 没有任何合法候选，应返回语义视图未覆盖或无法匹配
2. 候选引用了真实 schema 中不存在的 label、edge 或 property，应直接拒绝
3. 存在强规则命中，例如“目的网元”明确命中 service.tunnel_destination
4. 最高候选已经由 trigger_phrases、negative_phrases 和 disambiguation_rules 稳定确定
```

澄清输出必须使用 `SemanticMatchResult` 中的 `needs_clarification`、`clarification_type`、`clarification_question` 和 `clarification_options` 字段表达。澄清选项必须来自语义视图对象或语义匹配内部候选，不能是自由生成的新对象。

第一版先保留结构契约，不要求完整实现多轮交互。

### 12.11 输出契约和运行中心展示

`SemanticMatchResult` 的完整字段解释见 12.2。本节只规定这个结果如何被后续模块消费和展示，避免重复维护两份结构定义。

对 planner 来说：

```text
1. accepted=true 且 needs_clarification=false 时，才能进入 LogicalQueryPlan 生成
2. needs_clarification=true 时，不进入 planner，直接返回澄清请求
3. accepted=false 且 needs_clarification=false 时，表示语义视图未覆盖、schema 不合法或候选无法成立
```

对运行中心来说，语义视图匹配结果应优先展示以下内容：

```text
1. 用户原始问题
2. 匹配到的实体、字段、值、路径和返回项
3. 被接受候选的 trace
4. 被拒绝或降级候选的原因
5. 是否触发受控 LLM 消歧
6. 如果需要澄清，展示澄清问题和候选选项
```

推荐落盘结构：

```yaml
# 语义视图匹配阶段推荐落盘给运行中心的展示结构。
semantic_match_artifact:
  # 用户原始自然语言问题。
  question: 查询金牌服务使用的隧道名称
  # 指向最终 SemanticMatchResult 的引用。
  result_ref: semantic_match_result
  # 运行中心优先展示的匹配摘要。
  display:
    # 已匹配到的业务实体。
    matched_entities: [service, tunnel]
    # 已匹配到的过滤条件摘要。
    matched_filters:
      - service.quality_of_service = Gold
    # 已匹配到的业务路径语义。
    matched_paths:
      - service.uses_tunnel
    # 已匹配到的返回字段。
    matched_returns:
      - tunnel.name
  # 运行中心用于排查问题的诊断信息。
  diagnostics:
    # 本次匹配是否调用了受控 LLM 消歧。
    used_llm_disambiguation: false
    # 被拒绝或淘汰的候选集合。
    rejected_candidates: []
    # 从原始问题到语义对象的关键证据链。
    trace:
      - 服务 -> entity service
      - 金牌 -> service.quality_of_service = Gold
      - 使用的隧道 -> path_semantics service.uses_tunnel
      - 隧道名称 -> return tunnel.name
```

## 13. 如何从语义视图生成 LogicalQueryPlan

语义视图不直接生成 Cypher。它先支持生成 `LogicalQueryPlan`。

生成步骤：

```text
1. 运行语义视图匹配模块，得到 SemanticMatchResult
2. 如果语义视图匹配需要澄清，输出 clarification_required，不进入 planner
3. 结合意图识别结果，确定答案形态 answer_shape
4. 根据匹配出的实体、字段、指标、过滤值和路径语义生成计划骨架
5. 将计划骨架组织成 scan / traverse / filter / aggregate / project / order / limit / exists 等逻辑操作符
6. 根据 return_policies 补齐默认返回字段
7. 根据 metrics / dimensions / facts 补齐聚合、分组、排序和输出别名
8. 根据 path_semantics 生成业务路径需求
9. 通过 schema graph path planning 展开真实点、边、方向和变量序列
10. 检查计划完整性；缺少用户可补充信息时输出 clarification_required
11. 输出 LogicalQueryPlan 和可渲染路径计划
```

语义视图匹配回答“自然语言里的业务表达对应哪些语义对象”；planner 负责把这些语义对象放进查询结构中。planner 仅使用 `SemanticMatchResult` 中已确认的业务对象、字段、指标、路径和过滤值，并在进入 renderer 前完成关键字段、路径和指标的完整性检查。

`LogicalQueryPlan` 的核心结构由 `answer_shape` 和 `operators` 组成。意图识别结果提供 `answer_shape`，语义视图匹配结果提供业务对象和约束，planner 将二者组合成逻辑操作符序列。实现层可以从 `operators` 派生 `renderer_family`，用于选择对应的确定性渲染器。

示例：

```jsonc
{
  // 逻辑计划版本，用于后续兼容升级。
  "version": 1,

  // 本次逻辑计划 ID，用于运行中心 trace 和 renderer/preflight 诊断关联。
  "plan_id": "logical_plan_001",

  // 答案形态，来自意图识别结果。
  "answer_shape": "ranking_table",

  // planner 生成的逻辑操作符序列。
  "operators": [
    {
      // 从服务实体开始。
      "op": "scan",
      "entity": "service",
      "as": "s"
    },
    {
      // 沿服务隧道路径扩展到路径网元。
      "op": "traverse",
      "path_semantic": "service.tunnel_path",
      "from": "service",
      "to": "network_element",
      "as": "path_1"
    },
    {
      // 计算服务隧道路径上的网元数量。
      "op": "aggregate",
      "metric_id": "service_tunnel_path.network_element_count",
      "alias": "cnt"
    },
    {
      // 按网元位置分组。
      "op": "group_by",
      "fields": ["network_element.location"]
    },
    {
      // 返回分组字段和指标别名。
      "op": "project",
      "items": [
        {
          "kind": "dimension",
          "field": "network_element.location",
          "alias": "location"
        },
        {
          "kind": "metric_alias",
          "field": "cnt",
          "alias": "cnt"
        }
      ]
    },
    {
      // 按统计结果倒序排序。
      "op": "order",
      "field": "cnt",
      "direction": "desc"
    },
    {
      // 返回前 10 条。
      "op": "limit",
      "value": 10
    }
  ],

  // schema graph path planning 的结果引用。
  "schema_path_ref": "schema_path_001",

  // renderer 派生提示，用于选择确定性渲染器。
  "renderer_hints": {
    "renderer_family": "ranking",
    "requires_path_variable": false
  },

  // 证据链引用，用于运行中心展示和 preflight 诊断。
  "trace_refs": [
    "intent:ranking_query.metric_ranking_query",
    "semantic_match:path_semantic=service.tunnel_path",
    "semantic_match:metric=service_tunnel_path.network_element_count",
    "schema_path:schema_path_001"
  ]
}
```

## 14. 语义视图建设流程

建立一套语义视图时，按以下步骤进行：

```text
1. 从真实 TuGraph schema 获取 label、edge、property
2. 定义业务实体 entities
3. 将属性分为 dimensions 和 facts
4. 定义常用 metrics
5. 定义单跳 relationships
6. 基于业务短语定义 path_semantics
7. 定义 return_policies
8. 定义 disambiguation_rules
9. 用能力评测集验证语义视图覆盖度
10. 多轮实验稳定后，再考虑补充 verified_examples
```

每次 schema 或业务规则变化，都应先更新语义视图，再更新匹配规则、planner 或 renderer。

## 15. 第一版建设边界

第一版语义视图建议覆盖以下能力：

```text
1. 服务基础属性查询
2. 服务质量 QoS 过滤、返回、分组
3. 服务使用隧道
4. 服务隧道源网元
5. 服务隧道目的网元
6. 服务隧道所经网元
7. 网元端口
8. 隧道协议
9. 基础 count / avg / sum / max / min 指标
10. 排序 TopN
```

第一版必须支持以下表达：

```text
名称和带宽
服务质量等级为 Bronze，同时返回服务质量等级
服务及其使用的隧道
目的网元
源网元
所经网元 / 穿过网元
包含端口
包含服务质量属性
按位置统计数量
按数量降序返回前 N
```

## 16. 语义视图验收标准

一份语义视图可用，至少需要满足：

```text
1. 每个 entity 都能映射到真实 TuGraph label
2. 每个 dimension / fact 都能映射到真实 property
3. 每个 relationship 都能映射到真实 edge
4. 每个 path_semantic 都能展开成合法图路径
5. 每个 metric 都有明确 aggregation 和 target
6. 每条 disambiguation rule 都有正例和反例
7. 语义视图中不能出现旧字段，例如 Service.type
8. 基于语义视图生成的查询计划和 Cypher 不能访问不存在属性
9. 当前能力评测集通过率达到实验要求
10. 第一版不要求提供 verified_examples
```

## 17. 总结

Graph Semantic View 的本质是：

```text
把真实图 schema 转换成业务语义资产。
```

它应该明确告诉 CGA：

```text
有哪些业务实体
有哪些维度和事实字段
有哪些指标
哪些实体之间存在关系
哪些业务短语对应哪些图路径
默认应该返回什么
遇到歧义应该如何选择
后续可选沉淀哪些已验证问题和查询计划
```

后续 CGA 应先通过语义视图形成 `LogicalQueryPlan`，再由确定性 renderer 生成 Cypher。
