# Graph Semantic Model Specification v1

> 日期：2026-05-27
> 状态：v1（替代之前的 OSI + Cypher 扩展双层定义）
> 用途：Cypher / TuGraph 场景的图原生语义模型定义

## 0. 重要声明

**本规范是单一权威定义**。从语义层到 DSL 到 Cypher 编译，**全程使用相同术语**，不再维护与 OSI 的字段映射。之前所有 OSI 风格的 YAML 和文档应按本规范迁移。

迁移指令见文末 §10。

---

## 1. 设计原则

1. **图原生术语贯穿全栈**：`vertex` / `edge` / `property`，禁止使用 `dataset` / `relationship` / `field`
2. **单方言**：只支持 Cypher，去掉 OSI 的 `dialects` 多版本机制
3. **无映射层**：`name` 直接等于 Cypher 中的 label / property name，不再有"逻辑名"到"物理名"的转换
4. **保留 OSI 的优秀设计**：`ai_context`、`synonyms`、`value_synonyms`、`direction_semantics`、`path_patterns`、metric 的 pattern + expression 拆分
5. **数据约束与 AI 提示分离**：`valid_values` 是数据约束，应放在 property 顶层；`synonyms` 是 AI 召回提示，放在 `ai_context` 内

---

## 2. 顶层结构

```yaml
semantic_model:
  - name: string                # 必填，模型唯一标识
    description: string         # 可选
    ai_context: object          # 可选
    vertices: [...]             # 必填，顶点定义数组
    edges: [...]                # 可选，边定义数组
    path_patterns: [...]        # 可选，命名路径模板
    metrics: [...]              # 可选，指标定义
```

`ai_context` 的标准结构：

```yaml
ai_context:
  instructions: string          # 给 LLM 的使用说明
  synonyms: [string]            # 同义词数组
  examples: [string]            # 示例问题
```

---

## 3. Vertex（顶点）

```yaml
vertices:
  - name: NetworkElement              # 必填，必须与 Cypher 中的 label 完全一致
    id_property: id                   # 必填，唯一标识属性的 name
    description: string               # 可选
    ai_context:                       # 可选
      synonyms: [string]              # 顶点的同义词（如"设备"、"network device"）
    properties: [...]                 # 该顶点的属性数组
```

### 字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 顶点名，必须与 Cypher 中的 label **一字不差**。约定使用 PascalCase |
| `id_property` | 是 | 唯一标识该顶点实例的属性名。必须在 `properties` 中定义 |
| `description` | 否 | 业务含义说明 |
| `ai_context.synonyms` | 否 | 用户自然语言中可能用到的同义词 |
| `properties` | 否 | 该顶点的属性定义数组 |

### 设计说明

- 不再有 `source` 字段（OSI 用来引用 SQL 表名）。图世界 vertex name 就是 label
- 不再有 `primary_key`（数组形式）。图模型几乎不存在复合主键，统一用单一 `id_property`。如果业务上确有复合标识需求，应在数据建模时合并为单一 `id` 属性
- 不再有 `unique_keys`。如有唯一约束，由数据库 schema 而非语义层维护

---

## 4. Property（属性）

```yaml
properties:
  - name: elem_type                   # 必填，与 Cypher 中属性名一致
    type: string                      # 必填
    required: false                   # 可选，默认 false
    description: string               # 可选
    ai_context:                       # 可选
      synonyms: [string]
    valid_values: [string]            # 可选，枚举值列表（数据约束）
    value_synonyms:                   # 可选，枚举值的自然语言映射
      router: ["路由器", "router"]
      firewall: ["防火墙", "FW"]
```

### 字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 属性名，必须与 Cypher 节点/边的属性名完全一致 |
| `type` | 是 | 数据类型，取值见下方 |
| `required` | 否 | 是否必填属性。默认 false |
| `description` | 否 | 业务含义 |
| `ai_context.synonyms` | 否 | 属性的同义词，用于自然语言映射 |
| `valid_values` | 否 | 如果该属性是枚举类型，列出所有合法值 |
| `value_synonyms` | 否 | 枚举值的自然语言映射（key 是 `valid_values` 中的值，value 是同义词数组） |

### `type` 取值

`string` | `int` | `float` | `boolean` | `datetime` | `list<T>`

### 设计说明

- 删除 OSI 的 `expression.dialects` 包装。在 Cypher 中 `node.property` 直接访问，不需要表达式抽象
- 如果是**计算属性**（非数据库直接存储），可添加可选字段 `cypher_expression`：

```yaml
- name: full_name
  type: string
  description: 拼接的全名
  cypher_expression: "first_name + ' ' + last_name"  # 仅计算属性才需要
```

非计算属性一律省略 `cypher_expression`

- `valid_values` 和 `value_synonyms` **从 `ai_context` 中提升到 property 顶层**，因为它们是数据约束（影响校验），不是单纯 AI 提示
- 删除 OSI 的 `dimension.is_time` 标记。改用 `type: datetime` 直接表达；如果是日期相关的字符串属性，靠 `ai_context.synonyms` 中包含"时间"、"日期"类词来标识

---

## 5. Edge（边）

```yaml
edges:
  - name: PATH_THROUGH                # 必填，与 Cypher 中的 relationship type 一致
    from: Tunnel                      # 必填，起点 vertex name
    to: NetworkElement                # 必填，终点 vertex name
    cardinality: many_to_many         # 必填
    direction_semantics: |            # 强烈推荐
      存储方向：Tunnel → NetworkElement
      语义方向：隧道"经过"该设备
    anti_patterns: [string]           # 可选，使用反模式警告
    description: string               # 可选
    ai_context:                       # 可选
      synonyms: [string]
      examples: [string]
    properties: [...]                 # 可选，边自身的属性
```

### 字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 边的 type 名，必须与 Cypher 一致。约定 UPPER_SNAKE_CASE |
| `from` | 是 | 起点 vertex 的 `name`（不是 label 列表，不是属性数组） |
| `to` | 是 | 终点 vertex 的 `name` |
| `cardinality` | 是 | `one_to_one` / `one_to_many` / `many_to_one` / `many_to_many` |
| `direction_semantics` | 推荐 | 多行字符串，说明存储方向和语义方向。**当两者不一致时（如 TUNNEL_SRC）必填** |
| `anti_patterns` | 否 | 字符串数组，明确告诉 LLM 这条边**不应**怎么用 |
| `description` | 否 | 业务含义 |
| `ai_context.synonyms` | 否 | 关系动词的同义词（如"经过"、"使用"） |
| `properties` | 否 | 边自身的属性（如 PATH_THROUGH 的 `hop_order`） |

### 设计说明

- 删除 OSI 的 `from_columns` / `to_columns`（数组形式，对应 SQL 外键列）。图世界边是 label-to-label 的，单一 `from` / `to` 即可
- `direction_semantics` 和 `anti_patterns` **提升到 edge 顶层**，不再藏在 `ai_context` 里。它们是权威约束，不是 AI hints
- 边的 `properties` 数组结构和 vertex 的 `properties` **完全一致**，复用同一个 Property 结构

### 反直觉方向的示例

当存储方向和语义方向不一致时（典型如 TUNNEL_SRC），`direction_semantics` 必须显式标注：

```yaml
- name: TUNNEL_SRC
  from: Tunnel
  to: NetworkElement
  cardinality: one_to_one
  direction_semantics: |
    存储方向：Tunnel → NetworkElement
    语义方向：NetworkElement 是隧道的"源端设备"（入口 LSR）
    典型查询：MATCH (t:Tunnel {id:'tun-xxx'})-[:TUNNEL_SRC]->(ne)
              RETURN ne AS source_device
  anti_patterns:
    - "不要用 TUNNEL_SRC + TUNNEL_DST 推断隧道路径，路径查询必须用 PATH_THROUGH 并按 hop_order 排序"
  description: 隧道源端，用于快速定位入口设备
```

---

## 6. Path Pattern（命名路径模板）

```yaml
path_patterns:
  - name: tunnel_full_path
    description: 隧道完整路径（按 hop_order 升序）
    parameters:
      - name: tunnel_id
        type: string
        description: 目标隧道的 id
    cypher: |
      MATCH (t:Tunnel {id: $tunnel_id})-[p:PATH_THROUGH]->(ne:NetworkElement)
      RETURN ne, p.hop_order AS hop
      ORDER BY p.hop_order ASC
    ai_context:
      examples:
        - "隧道 tun-mpls-001 经过哪些设备"
        - "tun-xxx 的完整路径"
```

### 字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 路径模板唯一名，用于 DSL 中通过 `pattern_id` 引用 |
| `description` | 否 | 业务含义 |
| `parameters` | 否 | 模板参数（Cypher 中以 `$param_name` 引用） |
| `cypher` | 是 | 完整可执行的 Cypher 模板 |
| `ai_context.examples` | 否 | 示例问题，帮助 LLM 召回 |

### 设计说明

- Path pattern 是图原生概念，SQL 无对应。它把"会被反复用到的多跳遍历模式"做成命名宏
- 模板内的 Cypher **必须可独立执行**（参数绑定后）。这样可以在测试期直接验证模板正确性
- 复杂的角色暴露（如父 pattern exports 哪些 role）放在 DSL 层处理，本规范层只定义模板本身

---

## 7. Metric（指标）

```yaml
metrics:
  - name: device_count
    description: 网络中的设备总数
    pattern: "(ne:NetworkElement)"          # MATCH 子句模板
    expression: "count(ne)"                  # 聚合表达式
    valid_dimensions:                        # 可用于 group_by 的属性
      - ne.elem_type
      - ne.vendor
      - ne.location
    ai_context:
      synonyms: ["设备数量", "网元数量", "device count"]
      examples:
        - "全网有多少台防火墙"
        - "按厂商统计设备数量"
```

### 字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 指标唯一名 |
| `description` | 否 | 业务含义 |
| `pattern` | 是 | Cypher MATCH 模式片段（不带 MATCH 关键字） |
| `expression` | 是 | 聚合表达式（如 `count(ne)`、`avg(s.latency)`） |
| `valid_dimensions` | 否 | 该指标允许的 group_by 维度，每项形如 `alias.property` |
| `ai_context.synonyms` | 否 | 指标的同义词 |
| `ai_context.examples` | 否 | 示例问题 |

### 设计说明

- 删除 OSI 的 `expression.dialects` 包装，`expression` 直接是字符串
- `pattern` + `expression` 拆分是图原生设计——Cypher 聚合必须挂在 MATCH 上，不能像 SQL 那样写孤立的 `SUM(orders.amount)`
- `valid_dimensions` 只列**同一 pattern 中已绑定别名的属性**。如果需要跨 vertex 维度分组，**应写一个新的 metric 而不是扩展现有 metric**（避免复杂的 composable_with 设计）

### 两步聚合等复杂指标

如果指标需要两步聚合（如"平均跳数"需要先 count 后 avg），用 `full_cypher` 字段直接给完整模板：

```yaml
- name: average_tunnel_hops
  description: 隧道平均跳数
  full_cypher: |
    MATCH (t:Tunnel)-[p:PATH_THROUGH]->(:NetworkElement)
    WITH t, count(p) AS hops
    RETURN avg(hops) AS avg_hops
  valid_dimensions: []
  ai_context:
    synonyms: ["平均跳数", "average hops"]
```

`full_cypher` 和 `pattern + expression` **二选一**，不能同时存在。

---

## 8. 完整最小示例

下面是覆盖所有结构的最小示例（精简版网络拓扑）：

```yaml
semantic_model:
  - name: network_topology
    description: 网络拓扑语义模型
    ai_context:
      instructions: |
        网络拓扑分层：Service → Tunnel → NetworkElement → Port。
        隧道路径查询必须用 PATH_THROUGH 按 hop_order 排序，
        不要用 TUNNEL_SRC/TUNNEL_DST 推断路径。
      synonyms: ["网络拓扑", "network topology"]

    vertices:
      - name: NetworkElement
        id_property: id
        description: 网络设备
        ai_context:
          synonyms: ["设备", "device", "网元"]
        properties:
          - name: id
            type: string
            required: true
            description: 设备唯一标识

          - name: name
            type: string
            description: 设备名

          - name: elem_type
            type: string
            description: 设备类型
            ai_context:
              synonyms: ["类型", "device type"]
            valid_values: [router, switch, firewall, load_balancer]
            value_synonyms:
              router: ["路由器"]
              switch: ["交换机"]
              firewall: ["防火墙", "FW"]
              load_balancer: ["负载均衡器", "LB"]

          - name: location
            type: string
            description: 物理位置
            ai_context:
              synonyms: ["机房", "位置"]

      - name: Tunnel
        id_property: id
        description: 隧道实例
        ai_context:
          synonyms: ["隧道", "tunnel"]
        properties:
          - name: id
            type: string
            required: true

          - name: bandwidth
            type: float
            description: 隧道带宽

    edges:
      - name: PATH_THROUGH
        from: Tunnel
        to: NetworkElement
        cardinality: many_to_many
        direction_semantics: |
          存储方向：Tunnel → NetworkElement
          语义方向：隧道"经过"该设备
          ⭐ 隧道路径查询的唯一权威边，必须按 hop_order 排序
        description: 隧道路径序列（RFC 3209 ERO）
        ai_context:
          synonyms: ["经过", "路径", "走过"]
        properties:
          - name: hop_order
            type: int
            required: true
            description: 路径序号（0=入口，递增到出口）
            ai_context:
              synonyms: ["跳数", "第几跳"]

      - name: TUNNEL_SRC
        from: Tunnel
        to: NetworkElement
        cardinality: one_to_one
        direction_semantics: |
          存储方向：Tunnel → NetworkElement
          语义方向：NetworkElement 是隧道的"源端设备"
        anti_patterns:
          - "不要用 TUNNEL_SRC + TUNNEL_DST 推断隧道路径，路径查询必须用 PATH_THROUGH"
        description: 隧道源端
        ai_context:
          synonyms: ["源端", "入口设备"]

    path_patterns:
      - name: tunnel_full_path
        description: 隧道完整路径
        parameters:
          - name: tunnel_id
            type: string
        cypher: |
          MATCH (t:Tunnel {id: $tunnel_id})-[p:PATH_THROUGH]->(ne:NetworkElement)
          RETURN ne, p.hop_order AS hop
          ORDER BY p.hop_order ASC
        ai_context:
          examples:
            - "隧道 tun-mpls-001 经过哪些设备"

    metrics:
      - name: device_count
        description: 设备总数
        pattern: "(ne:NetworkElement)"
        expression: "count(ne)"
        valid_dimensions:
          - ne.elem_type
          - ne.location
        ai_context:
          synonyms: ["设备数量", "device count"]
          examples:
            - "全网有多少台防火墙"
            - "各机房有多少台设备"
```

---

## 9. 校验规则（实现 validator 时必须执行）

1. **唯一性**：
   - `vertices[].name` 在模型内唯一
   - `edges[].name` 在模型内唯一
   - `path_patterns[].name` 在模型内唯一
   - `metrics[].name` 在模型内唯一
   - 同一 vertex 内 `properties[].name` 唯一

2. **引用完整性**：
   - 每条 edge 的 `from` 和 `to` 必须是已定义的 vertex name
   - 每个 vertex 的 `id_property` 必须在该 vertex 的 `properties` 中定义
   - 每个 metric 的 `valid_dimensions` 中引用的 `alias.property` 必须能在 `pattern` 里追溯到合法 vertex 和 property

3. **结构性**：
   - property 的 `value_synonyms` 的 key 必须全部出现在 `valid_values` 中
   - edge 的 `cardinality` 必须是四个枚举值之一
   - property 的 `type` 必须是允许的类型
   - metric 的 `pattern + expression` 和 `full_cypher` 互斥（不能同时存在）

4. **Cypher 合法性**（弱校验）：
   - `path_patterns[].cypher` 可通过 openCypher 解析器解析
   - `metrics[].full_cypher` 可通过 openCypher 解析器解析

---

## 10. 迁移指令（给 Codex）

请按以下规则迁移现有的 OSI 风格 YAML 与相关代码。

### 10.1 YAML 字段重命名

| OSI 字段 | 新字段 | 备注 |
|---|---|---|
| `datasets` | `vertices` | 顶层数组重命名 |
| `relationships` | `edges` | 顶层数组重命名 |
| `fields` | `properties` | 在 vertex 和 edge 内 |
| `source` | （删除） | vertex name 直接等于 label |
| `primary_key: [x]` | `id_property: x` | 数组改单值；如果原数组有多个值，报错并提示需要数据建模调整 |
| `unique_keys` | （删除） | 由数据库 schema 约束 |
| `from_columns: [x]` | `from: VertexName` | 改为单一 vertex name |
| `to_columns: [x]` | `to: VertexName` | 改为单一 vertex name |
| `expression.dialects[].expression` | （直接用 property name） | 对非计算属性，整个 `expression` 块删除 |
| `dimension.is_time` | （删除） | 改用 `type: datetime` |
| `custom_extensions` | （删除） | v1 不需要 |

### 10.2 字段位置调整

| 原位置 | 新位置 |
|---|---|
| property 的 `ai_context.valid_values` | property 顶层 `valid_values` |
| property 的 `ai_context.value_synonyms` | property 顶层 `value_synonyms` |
| edge 的 `ai_context.anti_patterns` | edge 顶层 `anti_patterns` |
| edge 的 `ai_context.direction_semantics` | edge 顶层 `direction_semantics` |

### 10.3 Metric 结构调整

旧 OSI 风格：

```yaml
- name: device_count
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: count(ne)
```

新规范：

```yaml
- name: device_count
  pattern: "(ne:NetworkElement)"
  expression: "count(ne)"
  valid_dimensions: [ne.elem_type]
```

如果旧 metric 用了 `composable_with` 字段，删除该字段，根据其内容**拆分成多个独立 metric**。

如果旧 metric 用了 `full_query_template`，重命名为 `full_cypher`，并删除 `pattern` 与 `expression`。

### 10.4 代码侧需要更新的内容

- **YAML 解析器**：所有 dict key 按 §10.1 改名
- **JSON Schema / 校验逻辑**：按 §9 的 4 类规则重写
- **DSL parser / compiler**：内部引用从 `dataset` / `relationship` 改为 `vertex` / `edge`，从 `field` 改为 `property`
- **Prompt 模板**：所有给 LLM 的提示词中，把 `datasets / relationships / fields` 改为 `vertices / edges / properties`
- **测试 fixture**：所有引用旧字段名的测试数据同步更新

### 10.5 不可遗漏的检查清单

迁移完成后，请确认：

- [ ] 不存在 `datasets`、`relationships`、`fields` 任何残留（grep 全仓库）
- [ ] 不存在 `expression.dialects` 结构（应已被简化）
- [ ] 不存在 `from_columns` / `to_columns`（应已被 `from` / `to` 单值替代）
- [ ] 不存在 `dimension.is_time`（应已改为 `type: datetime`）
- [ ] 所有 edge 都有 `cardinality` 字段
- [ ] 反直觉方向的 edge（如 TUNNEL_SRC）都有 `direction_semantics`
- [ ] 反模式风险的 edge（如 TUNNEL_SRC、TUNNEL_DST）都有 `anti_patterns`
- [ ] 所有枚举 property 的 `value_synonyms` key 都出现在 `valid_values` 中
- [ ] §9 的 4 类校验规则全部实现并通过

### 10.6 不允许的变更

- **禁止保留双轨术语**。不允许"既支持 datasets 也支持 vertices"这种向后兼容。这次是 hard cut
- **禁止保留 dialects 多版本机制**。即使未来要扩展到其他图数据库方言，也通过 v2 spec 解决，不在 v1 中保留兜底
- **禁止增加未在本规范中定义的字段**。如有真实需求，先提 spec 修订，再实现

---

## 11. 版本与演进

- v1：本规范
- 任何字段添加、删除、改名都需要新版本号
- 向后兼容策略由 v2 时讨论；v1 内不允许 deprecated 字段共存

---

## 附：与 OSI 的关系

本规范借鉴了 OSI Open Standard Interchange 0.2.0.dev0 的以下设计：

- 整体的"semantic_model → 实体 → 属性"分层结构
- `ai_context` 作为一等公民承载 AI 提示
- `synonyms` / `instructions` / `examples` 的 ai_context 子字段
- metric 与属性分离的设计

本规范**不再兼容** OSI 的以下设计：

- 多 dialect 表达式（OSI 为支持 ANSI_SQL/Snowflake/MDX/Tableau 等而设计；本规范只服务 Cypher）
- `datasets` / `relationships` / `fields` 术语（不符合图原生语义）
- `source` 引用物理表（图世界 label 即 source）
- `primary_key` / `unique_keys` 数组（图世界用单一 id_property）
- `from_columns` / `to_columns` 数组（图世界 edge 是 label-to-label）

如未来需要重新对齐 OSI（例如为了语义层互换），由专门的 adapter 层负责双向翻译，不污染本规范。
