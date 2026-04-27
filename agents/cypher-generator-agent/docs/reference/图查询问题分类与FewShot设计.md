# 图查询问题分类与 Few-Shot 设计

## 说明

本文档用于记录当前 `Text2Cypher` 场景下的图查询问题分类，并结合远端 `knowledge-agent` 当前真实知识目录内容，为每一类问题补充适合的 `few-shot` 设计。

本文档依据的远端真实知识来源为：

- `system_prompt.md`
- `business_knowledge.md`
- `cypher_syntax.md`
- `few_shot.md`
- `schema.json`

拉取时间：`2026-04-17`

本文档里的 few-shot 分成两类：

1. `knowledge-agent 已有示例`
   - 直接来自当前远端 `few_shot.md`

2. `建议补充示例`
   - 不是远端当前已有原文
   - 但严格基于当前 knowledge-agent 的 schema、术语映射、语法约束与已有 few-shot 风格补充

---

## 一、分类总览

当前建议把图数据库查询问题分成 8 类：

1. `实体信息查询`
2. `关系关联查询`
3. `路径查询`
4. `条件过滤查询`
5. `聚合统计查询`
6. `排序 / Top-K 查询`
7. `属性投影查询`
8. `混合复合查询`

在工程实现里，这 8 类不一定都要作为一级分类独立建模，但它们适合拿来组织：

- semantic parsing
- return shape
- few-shot 示例库
- repair-agent 修复建议归因

---

## 二、分类与 Few-Shot 设计

## 1. 实体信息查询

### 定义

查询某类节点对象本身的信息，核心是：

- 主实体是谁
- 是否有过滤条件
- 返回对象本身还是字段

### 典型问题

- 查询10条带宽大于等于1的隧道信息
- 查询5条光纤信息
- 查询所有网络设备信息

### 推荐 return shape

- 默认：`entity_object`
- 如果用户明确点名字段，则改为 `scalar_fields`

### knowledge-agent 当前知识关联

- `business_knowledge.md`
  - “隧道” -> `Tunnel`
  - “网络设备” -> `NetworkElement`
  - “名称” -> `name`
  - “ID” -> `id`
- `cypher_syntax.md`
  - 使用 `WHERE`
  - 不允许虚构属性
- `system_prompt.md`
  - 当前存在“信息类问题倾向字段投影”的约束张力

### 建议补充正确 few-shot

```text
[id: tunnel_info_filtered_few_shot]
Question: 查询10条带宽大于等于1的隧道信息
Cypher: MATCH (t:Tunnel) WHERE t.bandwidth >= 1 RETURN t LIMIT 10
Why: 用户要求“隧道信息”而未明确字段，应返回 `Tunnel` 节点对象本身；过滤条件使用 `WHERE`。
```

```text
[id: network_element_info_few_shot]
Question: 查询5个网络设备信息
Cypher: MATCH (n:NetworkElement) RETURN n LIMIT 5
Why: “设备信息”表示对象级返回，而不是字段投影。
```

### 建议补充错误 few-shot

```text
Anti-Pattern: MATCH (t:Tunnel) WHERE t.bandwidth >= 1 RETURN t.id AS id, t.name AS name LIMIT 10
Why Not: 问题要求“隧道信息”且未指定字段时，过早压缩成字段投影会削弱对象语义。
```

```text
Anti-Pattern: MATCH (t:Tunnel) WHERE t.speed >= 1 RETURN t LIMIT 10
Why Not: `Tunnel` 不存在 `speed` 属性，属于 schema hallucination。
```

---

## 2. 关系关联查询

### 定义

查询一个实体和另一个实体的直接关系，重点是：

- 关系类型
- 方向
- 主实体与关联实体的返回结构

### 典型问题

- 查询5条链路及其目的端口信息
- 查询链路及其源端口名称
- 查询设备及其端口信息

### 推荐 return shape

- `key_plus_entity`
- 或 `scalar_fields`，取决于用户是否明确只要字段

### knowledge-agent 当前知识关联

- `business_knowledge.md`
  - “链路目的端口” -> `(Link)-[:LINK_DST]->(Port)`
  - “设备有哪些端口” -> `(NetworkElement)-[:HAS_PORT]->(Port)`
  - `key` 对应主实体 `id`
- `few_shot.md`
  - 已有 `link_dst_port_few_shot`

### knowledge-agent 已有示例

```text
[id: link_dst_port_few_shot]
Question: 查询链路及其目的端口名称
Cypher: MATCH (l:Link)-[:LINK_DST]->(p:Port) RETURN l.id AS key, l.name AS link_name, p.name AS dst_port_name
Why: 使用 `Link` 到 `Port` 的 `LINK_DST` 关系，并返回链路和目的端口的必要属性。
Anti-Pattern: MATCH (l:Link)-[:DST_PORT]->(p:Port) RETURN l, p
Why Not: `DST_PORT` 不存在，且不应直接返回整节点。
```

### 建议补充正确 few-shot

```text
[id: link_dst_port_info_few_shot]
Question: 查询5条链路及其目的端口信息
Cypher: MATCH (l:Link)-[:LINK_DST]->(p:Port) RETURN l.id AS key, p LIMIT 5
Why: 主实体是 `Link`，关联实体是 `Port`；问题关注“目的端口信息”，返回 `key + 关联对象` 更贴近语义。
```

```text
[id: network_element_port_info_few_shot]
Question: 查询设备及其端口信息
Cypher: MATCH (n:NetworkElement)-[:HAS_PORT]->(p:Port) RETURN n.id AS key, p
Why: `HAS_PORT` 是设备到端口的直接关系，主实体标识可使用 `key`，端口对象作为关联结果返回。
```

### 建议补充错误 few-shot

```text
Anti-Pattern: MATCH (l:Link)-[:LINK_SRC]->(p:Port) RETURN l.id AS key, p LIMIT 5
Why Not: 用户问的是“目的端口”，不能误用 `LINK_SRC`。
```

```text
Anti-Pattern: MATCH (l:Link)-[:LINK_DST]->(p:Port) RETURN l, p LIMIT 5
Why Not: 返回结构过宽，主实体对象 `l` 不是问题真正关注的返回主体。
```

---

## 3. 路径查询

### 定义

查询多跳路径、经过节点、路径顺序等，重点是：

- 路径语义
- 可变长路径或路径边
- 顺序约束

### 典型问题

- 查询指定隧道经过的设备顺序
- 查询网元经过1到3跳的连接路径
- 查询某条隧道经过哪些设备

### 推荐 return shape

- `path_rows`
- 或对象 + 顺序字段

### knowledge-agent 当前知识关联

- `business_knowledge.md`
  - “隧道路径经过哪些设备” -> `(Tunnel)-[:PATH_THROUGH]->(NetworkElement)` 且按 `hop_order`
- `cypher_syntax.md`
  - `pt.hop_order` 必须排序
- `few_shot.md`
  - 已有 `tunnel_path_few_shot`

### knowledge-agent 已有示例

```text
[id: tunnel_path_few_shot]
Question: 查询指定隧道经过的设备顺序
Cypher: MATCH (t:Tunnel {id: 'tun-mpls-1'})-[pt:PATH_THROUGH]->(n:NetworkElement) RETURN t.id AS tunnel_id, pt.hop_order AS hop_order, n.name AS network_element_name ORDER BY pt.hop_order ASC
Why: `PATH_THROUGH` 是隧道经过设备的唯一主路径，并且必须按 `hop_order` 排序。
Anti-Pattern: MATCH (t:Tunnel)-[:TUNNEL_SRC|TUNNEL_DST]->(n:NetworkElement) RETURN n.name
Why Not: `TUNNEL_SRC` 和 `TUNNEL_DST` 只是端点，不代表完整路径。
```

### 建议补充正确 few-shot

```text
[id: tunnel_path_devices_few_shot]
Question: 查询隧道经过哪些设备
Cypher: MATCH (t:Tunnel)-[pt:PATH_THROUGH]->(n:NetworkElement) RETURN t.id AS tunnel_id, pt.hop_order AS hop_order, n ORDER BY pt.hop_order ASC
Why: 路径查询应通过 `PATH_THROUGH` 表达，并保留顺序信息。
```

### 建议补充错误 few-shot

```text
Anti-Pattern: MATCH (t:Tunnel)-[:TUNNEL_SRC]->(n:NetworkElement) RETURN t.id, n
Why Not: `TUNNEL_SRC` 只表示起点，不等于路径经过节点。
```

```text
Anti-Pattern: MATCH (t:Tunnel)-[pt:PATH_THROUGH]->(n:NetworkElement) RETURN n
Why Not: 如果问题关心“经过顺序”，丢掉 `hop_order` 会损失关键语义。
```

---

## 4. 条件过滤查询

### 定义

核心是条件筛选，重点是：

- 属性字段
- 比较符
- 阈值

### 典型问题

- 查询状态为 up 的端口
- 查询带宽大于1000的链路
- 查询延迟小于10的隧道

### 推荐 return shape

- 视主问题而定，通常是：
  - `entity_object`
  - 或 `scalar_fields`

### knowledge-agent 当前知识关联

- `cypher_syntax.md`
  - 使用 `WHERE`
- `business_knowledge.md`
  - 属性名映射：`状态`、`名称`、`类型`

### 建议补充正确 few-shot

```text
[id: port_status_filter_few_shot]
Question: 查询状态为up的端口信息
Cypher: MATCH (p:Port) WHERE p.status = 'up' RETURN p
Why: 使用 `Port.status` 进行过滤，问题要求“端口信息”时返回对象。
```

```text
[id: link_bandwidth_filter_fields_few_shot]
Question: 查询带宽大于1000的链路名称和ID
Cypher: MATCH (l:Link) WHERE l.bandwidth > 1000 RETURN l.id AS id, l.name AS name
Why: 用户明确点名字段时，返回字段投影。
```

### 建议补充错误 few-shot

```text
Anti-Pattern: MATCH (p:Port) WHERE p.admin_status = 'up' RETURN p
Why Not: `admin_status` 属于 `Link`，不是 `Port`。
```

```text
Anti-Pattern: MATCH (l:Link) RETURN l.id, l.name WHERE l.bandwidth > 1000
Why Not: `WHERE` 位置错误，Cypher 过滤应位于 `MATCH` / `RETURN` 之间。
```

---

## 5. 聚合统计查询

### 定义

问题目标不是对象本身，而是统计值、分组值或聚合结果。

### 典型问题

- 按类型统计隧道的数量
- 统计每种网络设备类型的数量
- 查询链路总数

### 推荐 return shape

- `aggregation_rows`

### knowledge-agent 当前知识关联

- `system_prompt.md`
  - 禁止 `GROUP BY`
- `cypher_syntax.md`
  - 聚合通过 `RETURN` / `WITH` 隐式分组
- `few_shot.md`
  - 已有 `network_element_port_count_few_shot`

### knowledge-agent 已有示例

```text
[id: network_element_port_count_few_shot]
Question: 统计每种网络设备类型的数量
Cypher: MATCH (n:NetworkElement) RETURN n.type AS group_key, count(n) AS total
Why: Cypher 通过 `RETURN` 隐式分组，不使用 `GROUP BY`。
Anti-Pattern: MATCH (n:NetworkElement) RETURN n.type, count(n) GROUP BY n.type
Why Not: Cypher 不支持 `GROUP BY`。
```

### 建议补充正确 few-shot

```text
[id: tunnel_type_count_few_shot]
Question: 按类型统计隧道的数量
Cypher: MATCH (t:Tunnel) RETURN t.type AS group_key, count(t) AS total
Why: “按类型统计数量”属于典型分组聚合，返回聚合行。
```

```text
[id: link_total_count_few_shot]
Question: 查询链路总数
Cypher: MATCH (l:Link) RETURN count(l) AS total
Why: 无分组字段时直接返回总计数。
```

### 建议补充错误 few-shot

```text
Anti-Pattern: MATCH (t:Tunnel) RETURN t.type, count(t) GROUP BY t.type
Why Not: TuGraph Cypher 不支持 `GROUP BY`。
```

```text
Anti-Pattern: MATCH (t:Tunnel) RETURN t
Why Not: 这是对象返回，不是统计结果。
```

---

## 6. 排序 / Top-K 查询

### 定义

用户明确要求排序或 Top-K，重点是：

- 排序字段
- 升降序
- `LIMIT`

### 典型问题

- 查询长度最长的前5条光纤
- 查询按ID降序排列的5条光纤
- 查询延迟最低的10条隧道

### 推荐 return shape

- `entity_object`
- 或 `scalar_fields`

### knowledge-agent 当前知识关联

- `schema.json`
  - `Fiber.length`
  - `Tunnel.latency`
  - 各实体 `id` / `name`
- `cypher_syntax.md`
  - 当前未专门给出排序示例，适合补充

### 建议补充正确 few-shot

```text
[id: fiber_topk_length_few_shot]
Question: 查询长度最长的前5条光纤信息
Cypher: MATCH (f:Fiber) RETURN f ORDER BY f.length DESC LIMIT 5
Why: 用户明确要求“最长的前5条”，必须使用 `ORDER BY ... DESC LIMIT 5`。
```

```text
[id: fiber_id_desc_few_shot]
Question: 查询5条光纤的ID，并按ID降序排列
Cypher: MATCH (f:Fiber) RETURN f.id AS id ORDER BY id DESC LIMIT 5
Why: 用户只要 ID，属于字段投影 + 排序。
```

### 建议补充错误 few-shot

```text
Anti-Pattern: MATCH (f:Fiber) RETURN f LIMIT 5
Why Not: 丢失了排序要求，“前5条”不等于“任意5条”。
```

```text
Anti-Pattern: MATCH (f:Fiber) RETURN f ORDER BY f.length ASC LIMIT 5
Why Not: “最长”意味着降序，不是升序。
```

---

## 7. 属性投影查询

### 定义

用户明确点名只要某些字段，而不是整个对象。

### 典型问题

- 查询所有网络设备的名称
- 查询链路的 ID 和带宽
- 查询隧道名称和协议版本

### 推荐 return shape

- `scalar_fields`

### knowledge-agent 当前知识关联

- `business_knowledge.md`
  - `名称 -> name`
  - `ID -> id`
  - `类型 -> type`
- `cypher_syntax.md`
  - `RETURN` 只返回题目需要的属性字段

### 建议补充正确 few-shot

```text
[id: network_element_name_few_shot]
Question: 查询所有网络设备的名称
Cypher: MATCH (n:NetworkElement) RETURN n.name AS name
Why: 用户明确只要“名称”，返回字段投影即可。
```

```text
[id: link_id_bandwidth_few_shot]
Question: 查询链路的ID和带宽
Cypher: MATCH (l:Link) RETURN l.id AS id, l.bandwidth AS bandwidth
Why: 用户明确点名字段，不应返回整个节点对象。
```

### 建议补充错误 few-shot

```text
Anti-Pattern: MATCH (n:NetworkElement) RETURN n
Why Not: 问题只要名称，返回整节点过宽。
```

```text
Anti-Pattern: MATCH (l:Link) RETURN l.id, l.bandwidth, l.name, l.status
Why Not: 返回了题目未要求的额外字段。
```

---

## 8. 混合复合查询

### 定义

一个问题同时包含多个意图，例如：

- 关系
- 条件
- 聚合
- 返回形状

### 典型问题

- 查询带宽大于100的链路及其目的端口名称
- 按类型统计带宽大于100的隧道数量
- 查询经过设备A的前5条隧道信息

### 推荐 return shape

- 取决于主意图
- 通常需要 semantic parsing 先拆成多个语义槽位

### knowledge-agent 当前知识关联

- 这类问题通常会同时用到：
  - `business_knowledge.md` 里的术语映射
  - `cypher_syntax.md` 里的过滤 / 聚合 / 路径规则
  - `few_shot.md` 里的关系型与聚合型示例

### 建议补充正确 few-shot

```text
[id: link_dst_port_name_filtered_few_shot]
Question: 查询带宽大于100的链路及其目的端口名称
Cypher: MATCH (l:Link)-[:LINK_DST]->(p:Port) WHERE l.bandwidth > 100 RETURN l.id AS key, p.name AS dst_port_name
Why: 这是“关系 + 过滤 + 字段投影”的复合问题，需要同时满足路径、过滤和返回字段约束。
```

```text
[id: tunnel_type_count_filtered_few_shot]
Question: 按类型统计带宽大于100的隧道数量
Cypher: MATCH (t:Tunnel) WHERE t.bandwidth > 100 RETURN t.type AS group_key, count(t) AS total
Why: 这是“过滤 + 聚合”复合问题，应先过滤再聚合。
```

### 建议补充错误 few-shot

```text
Anti-Pattern: MATCH (l:Link)-[:LINK_DST]->(p:Port) RETURN l.id AS key, p.name AS dst_port_name
Why Not: 丢失了带宽过滤条件。
```

```text
Anti-Pattern: MATCH (t:Tunnel) RETURN count(t) AS total WHERE t.bandwidth > 100
Why Not: `WHERE` 位置错误，且丢失了“按类型统计”的分组语义。
```

---

## 三、对当前 knowledge-agent 知识的直接启发

从远端 `knowledge-agent` 当前真实知识看，有几个很重要的现状：

1. `knowledge-agent 已经有较好的 schema / 术语映射 / 路径 few-shot`
   - 特别是：
     - `link_dst_port`
     - `tunnel_path`
     - `service_tunnel`
     - `network_element 聚合`

2. `knowledge-agent 当前 few-shot 更偏字段投影`
   - 例如 `link_dst_port_few_shot`
   - 当前 `system_prompt.md` 也倾向“不要直接返回整节点”

3. `实体信息查询` 和 `返回形状` 相关 few-shot 还偏少
   - 尤其缺：
     - “信息/详情 -> 返回对象”
     - “主实体及其关联对象 -> key + entity”

4. `排序 / Top-K` 类型 few-shot 当前明显不足

因此，如果后续要增强 knowledge-agent 的 few-shot 库，我建议优先补这几类：

1. `实体信息查询`
2. `关系关联查询（key + entity）`
3. `排序 / Top-K 查询`
4. `混合复合查询`

---

## 四、建议的工程落地方式

这些分类和 few-shot 设计最适合支持下面几件事：

1. `semantic parsing`
   - 先判断题型，再决定 return shape

2. `few-shot 检索`
   - 按问题类型优先召回对应 few-shot，而不是所有 few-shot 混着喂

3. `repair-agent 修复建议`
   - 如果 `projection_match_score` 低，优先回到“实体信息查询 / 关系关联查询”对应 few-shot

4. `knowledge-agent 知识治理`
   - 未来 few-shot 可以按问题类型分目录或分标签管理

---

## 五、当前结论

当前最值得记住的一点是：

> 图查询问题不是只有“实体 / 关系 / 条件”三件事，`返回形状` 本身也是问题类型的一部分。

而结合远端 `knowledge-agent` 真实知识看，当前最应该增强的不是 schema 基础知识，而是：

- `实体信息查询` 的对象级返回 few-shot
- `关系关联查询` 的 `key + entity` few-shot
- `排序 / Top-K` few-shot
- `混合复合查询` few-shot
