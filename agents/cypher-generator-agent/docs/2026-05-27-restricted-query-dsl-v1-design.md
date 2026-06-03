# Restricted Query DSL v1 设计

> 日期：2026-05-27
> 状态：设计 v1
> 上游：Graph Semantic Registry、Question Decomposer、Semantic Binder
> 下游：DSL Parser / AST、Cypher Compiler

## 1. 设计目标

Restricted Query DSL v1 是自然语言理解结果和 Cypher 编译器之间的受限中间表示。它使用 graph-native 术语：`vertex`、`edge`、`property`、`metric`、`path_pattern`。

原则：

- DSL 只引用 Graph Semantic Model v1 中存在的 `name`。
- DSL 不做逻辑名到物理名映射；`vertex_name` 等于 Cypher label，`edge_name` 等于 Cypher edge type，`property_name` 等于 Cypher property。
- DSL 结构必须能被 JSON Schema 校验。
- DSL 不允许嵌入任意原生 Cypher。
- DSL 无法表达时返回 `unsupported_query_shape`，不回退到 LLM 直接生成 Cypher。
- 每个 DSL 文档都必须声明 `query_shape`，方便校验、编译和观测。

## 2. v1 覆盖的查询模式

| query_shape | 支持能力 | 示例 |
| --- | --- | --- |
| `vertex_lookup` | 按 vertex 类型和 property 过滤返回 vertex 属性 | 查询设备 `ne-0001` 的信息 |
| `single_hop_traversal` | 从一个 vertex 经一条 edge 找另一类 vertex | Gold 服务使用了哪些隧道 |
| `variable_path_traversal` | 固定起止类型或 through 条件的多跳遍历 | 找出所有经过设备 `ne-0001` 的隧道 |
| `named_path_pattern` | 引用 graph semantic model 中的命名 path pattern | 使用 `tunnel_full_path` 查询隧道完整路径 |
| `metric_aggregate` | 使用已注册 metric 做聚合和分组 | 按设备类型统计设备数量 |
| `ad_hoc_aggregate` | 在受限模式下做 count/avg/sum 等简单聚合 | 按状态统计端口数量 |
| `top_n` | 排序取前 N | 端口最多的 5 台设备 |
| `two_step_aggregate` | 先聚合再过滤/排序/投影 | 先按设备统计端口，再取最多的 5 台 |

v1 不支持：

- 任意子查询嵌套。
- OPTIONAL MATCH。
- 图算法。
- 写操作。
- 未命名、无界的全图遍历。
- 原生 Cypher escape hatch。

## 3. v1 Operation Enum 与组合规则

v1 只允许以下 `op`：

| op | 作用 | 主要字段 |
| --- | --- | --- |
| `traverse_edge` | 单跳 edge 遍历 | `from`、`edge`、`to`、`direction` |
| `variable_path` | 变长路径遍历 | `start`、`through`、`allowed_edges`、`min_hops`、`max_hops` |
| `use_path_pattern` | 引用命名 path pattern | `path_pattern_name`、`parameters`、`bind_as` |
| `metric_aggregate` | 使用注册 metric | `metric_name`、`group_by`、`filters` |
| `aggregate` | ad hoc 分组聚合 | `group_by`、`measures` |
| `sort` | 排序 | `by` |
| `limit` | 限制返回数量 | `value` |
| `subquery` | v1 受限子查询 | `bind_as`、`query_shape`、`group_by`、`measures` |
| `filter_subquery` | 对 subquery 输出做过滤 | `source`、`predicate` |

query shape 与 op 序列约束：

| query_shape | 允许 op 序列 |
| --- | --- |
| `vertex_lookup` | 无 operation，或只使用顶层 `filters`、`projection`、`order_by`、`limit` |
| `single_hop_traversal` | `traverse_edge`，之后可选 `sort`、`limit` |
| `variable_path_traversal` | `variable_path`，之后可选 `sort`、`limit` |
| `named_path_pattern` | `use_path_pattern`，之后可选 `sort`、`limit` |
| `metric_aggregate` | `metric_aggregate`，之后可选 `sort`、`limit` |
| `ad_hoc_aggregate` | `aggregate`，之后可选 `sort`、`limit` |
| `top_n` | `metric_aggregate` 或 `aggregate`，然后必须有 `sort` 和 `limit` |
| `two_step_aggregate` | `subquery`，之后可选 `filter_subquery`，之后可选 `sort`，最后可选 `limit` |

子查询边界：

- `subquery.query_shape` v1 只能是 `ad_hoc_aggregate`。
- `subquery` 内不能再嵌套 `subquery`。
- `subquery` 内不能包含 `sort` 或 `limit`；排序和限制只能作用在子查询输出之后。
- `filter_subquery.source` 必须引用同一层前面出现过的 `subquery.bind_as`。
- `filter_subquery.predicate.property` 必须引用该 subquery 的 `group_by` alias 或 `measures.alias`。
- 不允许不带 `measures` 的 `subquery`。只过滤 vertex 应使用 `vertex_lookup` 或 traversal query shape。

## 4. 顶层 DSL 结构

```yaml
schema_version: restricted_query_dsl_v1
query_id: q-20260527-001
query_shape: named_path_pattern
source_question: "隧道 tun-mpls-001 经过哪些设备"
bindings:
  primary_vertex:
    vertex_name: Tunnel
operations:
  - op: use_path_pattern
    path_pattern_name: tunnel_full_path
    bind_as: path
    parameters:
      tunnel_id:
        raw: tun-mpls-001
        normalized: tun-mpls-001
        resolver_match_type: value_index_exact
projection:
  items:
    - alias: device
      source: path.ne
    - alias: hop
      source: path.hop
order_by: []
limit: null
assumptions: []
```

顶层字段：

| 字段 | 必需 | 含义 |
| --- | --- | --- |
| `schema_version` | 是 | 固定为 `restricted_query_dsl_v1` |
| `query_id` | 是 | trace 内稳定 ID |
| `query_shape` | 是 | v1 支持的查询形态 |
| `source_question` | 是 | 原始自然语言问题 |
| `bindings` | 是 | 主要 vertex、edge、property、metric 绑定 |
| `operations` | 是 | 查询操作序列 |
| `projection` | 是 | 输出列或路径 |
| `order_by` | 否 | 排序 |
| `limit` | 否 | 限制条数 |
| `assumptions` | 否 | 高置信 fuzzy 绑定或 warning-only 解释 |

当前 projection item 有三种合法形态：

| 形态 | 必填字段 | 用途 |
| --- | --- | --- |
| property projection | `target` + `property` | 返回某个绑定 vertex/edge 的具体属性。 |
| source projection | `source` | 返回 metric、group、subquery、path pattern 等上游输出列。 |
| vertex full projection | `target` + `vertex_full: true` | 返回完整 vertex，例如“服务信息”应编译为 `RETURN svc AS service`。 |

约束：

- 一个 projection item 只能使用上述三种形态之一。
- 裸对象 projection 不再默认降级为 `id`。只有题干显式要求 `ID/编号` 等字段时才使用 property projection 到 `id`。
- `path/relation` 结构锚点不是输出对象，不应为了输出口径生成 projection item。
- compiler 会对 projection alias 做通用保留字规避；DSL 不应依赖业务 schema 名称绕开 `end` 等保留字。

## 5. 单跳遍历

```yaml
query_shape: single_hop_traversal
bindings:
  start:
    vertex_name: Service
  edge:
    edge_name: SERVICE_USES_TUNNEL
  end:
    vertex_name: Tunnel
operations:
  - op: traverse_edge
    from: start
    edge: edge
    to: end
    direction: forward
filters:
  - target: start
    property:
      owner: Service
      name: quality_of_service
    operator: eq
    value:
      raw: Gold
      normalized: GOLD
      resolver_match_type: synonym
projection:
  items:
    - target: end
      property:
        owner: Tunnel
        name: id
```

校验规则：

- `edge.from` 必须匹配 `from` vertex。
- `edge.to` 必须匹配 `to` vertex。
- `direction` 只能是 `forward` 或 `backward`，不能靠编译器猜。
- filter property 必须属于对应 target 的 vertex 或 edge。
- 如果 edge 有 `anti_patterns` 命中当前问题，应进入 semantic validation failure 或 clarification。

## 6. 变长路径

变长路径用 `variable_path` 表达，必须有 hop 范围和 edge 白名单。

```yaml
query_shape: variable_path_traversal
bindings:
  start:
    vertex_name: Tunnel
  through:
    vertex_name: NetworkElement
operations:
  - op: variable_path
    bind_as: path
    start: start
    through:
      vertex_ref: through
      filters:
        - property:
            owner: NetworkElement
            name: id
          operator: eq
          value:
            raw: ne-0001
            normalized: ne-0001
            resolver_match_type: value_index_exact
    allowed_edges:
      - PATH_THROUGH
    min_hops: 1
    max_hops: 8
projection:
  items:
    - target: start
      property:
        owner: Tunnel
        name: id
```

校验规则：

- `max_hops` 必须存在，系统默认上限为 8，可由配置降低。
- `allowed_edges` 必须非空，且每个 edge 必须存在于 graph semantic model。
- `through` vertex 必须在路径 vertex 类型集合内。
- 如果用户没有给 hop 上限，Semantic Binder 可以使用 path_pattern 默认上限；若无默认上限则反问或拒绝。

## 7. 命名 path pattern

path pattern 由 graph semantic model 的 `path_patterns[]` 声明。DSL 通过 `path_pattern_name` 引用，不内联展开模板 Cypher。

```yaml
query_shape: named_path_pattern
operations:
  - op: use_path_pattern
    path_pattern_name: tunnel_full_path
    bind_as: path
    parameters:
      tunnel_id:
        raw: tun-mpls-001
        normalized: tun-mpls-001
        resolver_match_type: value_index_exact
projection:
  items:
    - alias: device
      source: path.ne
    - alias: hop
      source: path.hop
```

校验规则：

- `path_pattern_name` 必须存在。
- 引用的 path pattern 必须在 Graph Model Loader 阶段通过 `validate_model_artifact`，且缓存状态为 `passed`。
- `parameters` 必须覆盖 path pattern 定义中必填参数。
- 参数类型必须与 path pattern `parameters[].type` 一致。
- path pattern 的模板 Cypher 必须只包含只读子句；如果加载期校验发现 `SET`、`CREATE`、`MERGE`、`DELETE`、`CALL` 等非 v1 子集能力，拒绝加载整个 graph semantic model。
- v1 不允许在 DSL 中修改 path pattern 模板内部 Cypher。
- 如果用户过滤条件不能绑定到 path pattern 已声明参数、返回 alias 或 role，不能临时改模板；应返回 `unsupported_query_shape`，或改用 `variable_path_traversal`。
- v1 不支持 path pattern 内部嵌套另一个 path pattern。后续版本若支持，必须给 role namespace 加前缀，例如 `outer.transit_device` 和 `inner.link_device`。

## 8. 聚合、Top-N 与两步聚合

注册 metric 聚合：

```yaml
query_shape: metric_aggregate
operations:
  - op: metric_aggregate
    metric_name: device_count
    group_by:
      - alias: elem_type
        target: ne
        property:
          owner: NetworkElement
          name: elem_type
    filters:
      - target: ne
        property:
          owner: NetworkElement
          name: elem_type
        operator: eq
        value:
          raw: 防火墙
          normalized: firewall
          resolver_match_type: value_synonym
projection:
  items:
    - alias: elem_type
      source: group.elem_type
    - alias: device_count
      source: metric.device_count
```

`metric_aggregate` 和 `ad_hoc_aggregate` 使用同一套 `target + property` 结构，不允许 `dimension: ne.elem_type` 字符串简写。差异只在 `target` 的来源：

- `metric_aggregate.target` 必须引用 metric `pattern` 中的变量 alias，例如 `ne`。
- `ad_hoc_aggregate.target` 必须引用 DSL bindings 或当前 query shape 中声明的角色 alias，例如 `port`。
- 两者的 `property.owner/name` 都必须能在 graph semantic model 中校验。

ad hoc 聚合：

```yaml
query_shape: ad_hoc_aggregate
operations:
  - op: aggregate
    group_by:
      - alias: status
        target: port
        property:
          owner: Port
          name: status
    measures:
      - alias: port_count
        function: count
        target: port
        property:
          owner: Port
          name: id
```

两步聚合：

```yaml
query_shape: two_step_aggregate
operations:
  - op: subquery
    bind_as: device_port_counts
    query_shape: ad_hoc_aggregate
    group_by:
      - alias: device
        target: device
        property:
          owner: NetworkElement
          name: id
    measures:
      - alias: port_count
        function: count
        target: port
        property:
          owner: Port
          name: id
  - op: filter_subquery
    source: device_port_counts
    predicate:
      property: port_count
      operator: gt
      value: 10
  - op: sort
    by:
      - source: device_port_counts.port_count
        direction: desc
  - op: limit
    value: 5
```

v1 的 `subquery` 只允许包裹 `ad_hoc_aggregate`，不能任意嵌套。复杂指标优先通过 graph semantic model 的 `metrics[].full_cypher` 表达。

## 9. DSL 不支持时的产品策略

不支持分三类处理：

| 类型 | 示例 | 返回 |
| --- | --- | --- |
| 可拆解 | “先找经过 A 的隧道，再看这些隧道的端口数” | clarification，建议拆成两个支持查询 |
| 超出 v1 能力 | shortest path、connected components、OPTIONAL MATCH | `unsupported_query_shape` |
| 语义层缺失 | 用户问增长率但 graph semantic model 无对应 metric 或 datetime property | semantic coverage failure |

明确禁止：

- 不允许 fallback 到 LLM 直接生成 Cypher。
- 不允许 DSL 中出现 `raw_cypher`、`cypher_fragment`、`where_text`。
- 不允许用字符串拼接绕过 AST。

## 10. 编译前校验

DSL Parser 必须在进入 compiler 前完成：

- JSON Schema 校验。
- vertex/edge/property/metric/path_pattern 存在性校验。
- property 所属 owner 校验。
- edge endpoint 和 direction 校验。
- path_pattern 参数校验。
- metric `valid_dimensions` 校验。
- aggregate function 与 property 类型校验。
- limit、max_hops、order_by 合法性校验。

只有 AST 通过这些校验，compiler 才能生成 Cypher。
