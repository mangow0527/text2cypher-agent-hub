# NL2Cypher ChatBI 意图分类

## 1. 目标与原则

本文档定义 NL2Cypher ChatBI 场景下的意图分类，适用于单轮、一句话、一个问题原则上由一条 Cypher 回答的查询任务，覆盖资源设备信息查询、关系路径查询和常见分析型问数。

分类依据参考 NLIDB / Text-to-SQL / ChatBI / 语义解析实践：用 intent 选择用户问题的主要分析目标，用 slot 承载业务对象、属性、指标、条件和值，用 schema linking 映射到真实图谱 schema，用结构特征描述路径、聚合、排序、时间粒度等查询细节。

核心原则：

> intent 描述用户最终想得到的答案形态；路径、关系链、过滤条件、聚合函数、排序、limit、时间粒度等不单独扩展成一级 intent。

具体约束：

- 一级意图按用户最终目标划分，避免把 Cypher 写法、路径跳数或聚合函数混入分类标准。
- 二级意图只区分对后续查询计划有明显影响、且自然语言表达相对独立的子类。
- 同一个问题如果同时包含路径、聚合、排序，以最终答案形态为主分类，以结构特征记录其他信息。
- 对象、属性、关系、指标和值不放进 intent，而放进 slot、schema linking 和 metric linking。

## 2. 分类总览

建议使用两级意图分类：

- 一级意图：选择用户问题的主要答案形态。
- 二级意图：细化查询计划，但避免过细导致 embedding 匹配不稳定。

| 一级意图 | 中文名 | 说明 |
|---|---|---|
| `record_retrieval_query` | 明细/清单查询 | 返回实体、资源、记录或属性明细，不以统计值为最终答案 |
| `relationship_path_query` | 关系/路径查询 | 返回关系、路径、可达结果或拓扑结构 |
| `metric_query` | 指标查询 | 返回一个或少量聚合指标，如数量、均值、最大值、去重数 |
| `breakdown_query` | 分布/分组查询 | 按一个或多个维度分组，返回维度到指标的表格 |
| `ranking_query` | 排名查询 | 按属性或指标排序，返回最高、最低、最多、最少、前 N、后 N |
| `comparison_query` | 对比查询 | 明确比较两个或多个对象、分组、集合、指标或时间段 |
| `trend_query` | 趋势查询 | 按时间维度返回变化趋势、时间序列或周期变化 |
| `composition_query` | 占比/构成查询 | 返回比例、占比、构成、覆盖率、利用率等派生指标 |
| `set_operation_query` | 集合操作查询 | 返回差集、交集、并集或基于集合成员关系过滤的结果 |
| `existence_query` | 存在性查询 | 判断实体、关系、路径或条件是否存在，返回布尔语义 |

### 2.1 分类判定规则

为降低规则匹配和 embedding 匹配中的类别混淆，建议按以下优先级判定一级意图：

1. 是否问“是否存在、有没有、是否可达、是否满足”：优先归 `existence_query`。
2. 是否问“共同、只属于、未使用、差异、并集、任一、全部合并”：优先归 `set_operation_query`。
3. 是否要求时间序列、趋势、按时间粒度展开、同比、环比或变化率：归 `trend_query`。
4. 是否要求最高、最低、最多、最少、前 N、后 N、排名：归 `ranking_query`。
5. 是否明确比较两个或多个对象、指标、分组或时间段：归 `comparison_query`。
6. 是否要求占比、比例、构成、利用率、覆盖率：归 `composition_query`。
7. 是否按维度分组、分布、各类/每类/按 X 统计：归 `breakdown_query`。
8. 是否返回单个或少量聚合指标：归 `metric_query`。
9. 是否返回路径、关系、可达资源或拓扑结构：归 `relationship_path_query`。
10. 是否返回实体、资源、记录或属性明细：归 `record_retrieval_query`。

判定时遵循“最终答案优先”：

- “统计每个服务使用的隧道数量”归 `breakdown_query`，路径匹配记录为结构特征。
- “隧道数量最多的前 5 个服务”归 `ranking_query`，聚合函数和路径匹配记录为结构特征。
- “服务 A 和服务 B 使用的隧道数量谁更多”归 `comparison_query`。
- “服务 A 使用但服务 B 未使用的隧道”归 `set_operation_query`。
- “查询服务所使用的隧道”归 `record_retrieval_query`，关系链记录为结构特征。
- “查询服务到端口的完整路径”归 `relationship_path_query`。

## 3. 二级分类与示例

### 3.1 `record_retrieval_query`：明细/清单查询

返回实体、资源、记录或属性明细。查询实现中可以包含过滤、排序、limit、关系连接或固定路径，但最终答案不是统计值、路径结构或布尔判断。

| 二级意图 | 中文名 | 说明 | 示例 |
|---|---|---|---|
| `entity_list_query` | 实体列表查询 | 返回实体、资源或记录列表 | 查询所有服务；查看前 5 条链路记录；网络中有哪些设备 |
| `entity_detail_query` | 实体详情查询 | 返回实体完整信息或较完整字段集合 | 查看前 5 条隧道的详细信息；查询 ID 为 sample 的网元信息；查询某服务的详细配置 |
| `attribute_projection_query` | 属性投影查询 | 返回实体的指定属性 | 查询所有服务的 ID 和名称；查询前 5 条链路的编号、名称和状态；查询隧道的时延值 |
| `related_record_query` | 关联明细查询 | 沿关系或固定路径返回相关实体或属性明细 | 查询服务所使用的隧道；查询链路及其目的端口信息；查询业务使用隧道对应源网元的厂商 |

分类边界：

- 只要最终返回的是明细行、对象列表或属性表，优先归 `record_retrieval_query`。
- 过滤、排序、limit 不改变该类意图。
- 需要通过关系或固定路径拿到相关记录时，归 `related_record_query`；关系链和跳数进入结构特征。
- 如果用户要求完整路径、所有路径、拓扑子图或可达关系，归 `relationship_path_query`。

### 3.2 `relationship_path_query`：关系/路径查询

返回关系、路径、可达结果或拓扑结构。该类强调图结构本身是答案的一部分。

| 二级意图 | 中文名 | 说明 | 示例 |
|---|---|---|---|
| `relationship_detail_query` | 关系详情查询 | 返回边、关系对象或关系属性 | 查询服务和隧道之间的使用关系；查询设备与端口之间的连接关系详情；查询链路关系的属性信息 |
| `path_trace_query` | 路径明细查询 | 返回确定路径、路径顺序、节点链路明细或路径对象 | 查询服务到端口的完整路径；查询隧道经过的网络设备顺序；查询业务经过隧道和网元到达端口的路径详情 |
| `reachable_entity_query` | 可达实体查询 | 返回从某实体出发可到达的实体集合 | 查询设备 A 最多 3 跳可以到达哪些设备；查询网元 A 可达的所有网元；查询某业务可以到达哪些端口 |
| `path_enumeration_query` | 路径枚举查询 | 返回两个实体之间的所有路径或多条候选路径 | 查询设备 A 到设备 B 之间的所有路径；查询网元 A 到网元 B 最多 3 跳的路径；查询服务 S 到端口 P 的所有可达路径 |
| `topology_subgraph_query` | 拓扑子图查询 | 返回局部拓扑子图，通常包含节点和关系 | 查询设备 A 周边两跳拓扑；查询网元 A 附近的链路拓扑；查询某业务关联的完整资源拓扑 |

分类边界：

- “连接关系、使用关系、关系属性”归 `relationship_detail_query`。
- “完整路径、路径详情、经过顺序、跳序号”归 `path_trace_query`。
- “能到达哪些实体”归 `reachable_entity_query`。
- “所有路径、多条路径、候选路径”归 `path_enumeration_query`。
- “周边拓扑、局部拓扑、资源拓扑、节点和关系”归 `topology_subgraph_query`。
- 如果只是为了取明细字段而经过关系，不返回图结构本身，归 `record_retrieval_query.related_record_query`。

### 3.3 `metric_query`：指标查询

返回一个或少量聚合指标。指标可以来自节点集合、关系集合或路径匹配结果。

| 二级意图 | 中文名 | 说明 | 示例 |
|---|---|---|---|
| `count_metric_query` | 数量指标查询 | 返回数量、总数、去重数量 | 网络元素总共有多少个；链路一共有多少条；统计不同厂商数量 |
| `numeric_metric_query` | 数值指标查询 | 返回求和、平均、最大、最小等数值指标 | 查询服务平均带宽；查询隧道最大时延；统计所有服务总带宽 |
| `multi_metric_query` | 多指标查询 | 同一问题返回多个并列指标，可包含带条件的子集指标 | 查询服务数量和平均带宽；返回链路数量、最大带宽和平均带宽；统计端口总数和 up 端口数量 |

分类边界：

- 不按维度分组、最终返回一个或少量指标，归 `metric_query`。
- 按维度返回多行指标表，归 `breakdown_query`。
- 如果指标用于排序取前 N，归 `ranking_query`。
- 如果结果是占比、比例、利用率，归 `composition_query`。

### 3.4 `breakdown_query`：分布/分组查询

按一个或多个维度分组，返回维度到指标的表格。

| 二级意图 | 中文名 | 说明 | 示例 |
|---|---|---|---|
| `single_dimension_breakdown_query` | 单维单指标分组查询 | 按一个维度分组，返回一个指标 | 按厂商统计设备数量；统计各端口状态的端口数量；按隧道类型统计隧道数量 |
| `multi_dimension_breakdown_query` | 多维单指标分组查询 | 按多个维度组合分组，返回一个指标 | 按厂商和设备类型统计设备数量；按业务类型和服务等级统计服务数量 |
| `multi_metric_breakdown_query` | 多指标分组查询 | 按维度分组后返回多个指标，可包含条件子集指标 | 按服务等级统计服务数量和平均带宽；按服务等级统计服务数量及其中使用 MPLS-TE 隧道的数量 |

分类边界：

- “各、每个、按 X、分布、分组统计”通常归 `breakdown_query`。
- 路径只是数据获取方式；如果最终是分组统计，归 `breakdown_query`。
- 分组后返回多个指标时，归 `multi_metric_breakdown_query`；只返回一个指标时，再区分单维或多维。
- 如果用户要求按分组统计结果排序取前 N，归 `ranking_query`。

### 3.5 `ranking_query`：排名查询

返回最高、最低、最多、最少、前 N、后 N 或排序后的重点对象。

| 二级意图 | 中文名 | 说明 | 示例 |
|---|---|---|---|
| `attribute_ranking_query` | 属性排名查询 | 按实体或关系自身属性排序 | 查询延迟最高的前 5 个隧道；查询长度最长的前 5 条光纤；按 ID 降序找出最大的 1 个网元 |
| `metric_ranking_query` | 指标排名查询 | 先计算数量、求和、平均、最大、最小等指标，再按指标排序 | 查询隧道数量最多的前 5 个服务；统计设备数量最多的前 3 个厂商；查询关联端口最多的前 10 台设备 |
| `derived_metric_ranking_query` | 派生指标排名查询 | 按占比、利用率、覆盖率、增长率等派生指标排序 | 查询端口利用率最高的前 10 个端口；查询故障率最高的前 5 条链路；查询增长率最高的业务类型 |

分类边界：

- “前 N 条记录”只是 limit，不一定是排名；如果没有排序依据或最高/最多语义，归 `record_retrieval_query`。
- “最高、最低、最多、最少、排名、按指标排序取前 N”归 `ranking_query`。
- 如果排名对象来自路径统计，路径信息进入结构特征，不单独扩展为排名类 intent。

### 3.6 `comparison_query`：对比查询

明确比较两个或多个对象、集合、指标、分组或时间段。

| 二级意图 | 中文名 | 说明 | 示例 |
|---|---|---|---|
| `attribute_comparison_query` | 属性对比查询 | 比较对象属性值或属性一致性 | 比较服务 A 和服务 B 的带宽；网元 A 和网元 B 的厂商是否一致 |
| `metric_comparison_query` | 指标对比查询 | 比较两个或多个对象、集合或分组的指标 | 比较 VendorA 和 VendorB 的设备数量；比较服务 A 和服务 B 使用的隧道数量 |
| `segment_comparison_query` | 分组/群体对比查询 | 比较两个业务分组、资源分组或筛选群体 | 比较 Gold 和 Silver 服务的平均带宽；比较不同厂商设备的软件版本分布 |
| `period_comparison_query` | 时间段对比查询 | 比较两个明确时间段的指标或状态 | 比较本月和上月的故障链路数量；比较今天和昨天的端口 up 数量 |

分类边界：

- 明确出现两个或多个比较对象，归 `comparison_query`。
- 如果比较的是共同成员、差异成员或集合合并，归 `set_operation_query`。
- 如果返回随时间连续变化的序列，归 `trend_query`。

### 3.7 `trend_query`：趋势查询

按时间维度返回变化趋势、时间序列或周期变化。

| 二级意图 | 中文名 | 说明 | 示例 |
|---|---|---|---|
| `time_series_metric_query` | 时间序列指标查询 | 按时间粒度返回指标序列 | 按天统计链路故障数量；按月统计新增隧道数量 |
| `state_trend_query` | 状态变化趋势查询 | 返回状态随时间变化 | 查询端口状态最近 7 天变化；查看设备在线状态变化趋势 |
| `period_over_period_query` | 同比/环比查询 | 返回周期对比或变化率 | 查询本月链路故障数环比变化；统计服务数量同比增长率 |

分类边界：

- “趋势、变化、按天/月/年、最近 N 天、同比、环比”归 `trend_query`。
- 只比较两个明确时间段且不要求趋势序列，也可归 `comparison_query.period_comparison_query`；若表达为变化趋势或变化率，归 `trend_query`。

### 3.8 `composition_query`：占比/构成查询

返回比例、占比、构成、覆盖率、利用率等派生指标。

| 二级意图 | 中文名 | 说明 | 示例 |
|---|---|---|---|
| `scalar_ratio_query` | 单值比例查询 | 返回两个量之间的单个比例值 | 查询 up 端口占全部端口的比例；查询 MPLS-TE 隧道占全部隧道的比例 |
| `share_breakdown_query` | 构成占比查询 | 按维度返回各组占比 | 查询各厂商设备占比；查询各服务等级的业务占比 |
| `coverage_utilization_query` | 覆盖率/利用率查询 | 返回覆盖率、利用率、使用率等派生指标 | 查询端口利用率；查询隧道覆盖率；查询业务资源使用率 |

分类边界：

- “占比、比例、构成、覆盖率、利用率、使用率”归 `composition_query`。
- 如果只是分组数量，没有比例语义，归 `breakdown_query`。
- 如果按占比排序取前 N，归 `ranking_query`，并用结构特征标记 derived metric。

### 3.9 `set_operation_query`：集合操作查询

对两个或多个集合做差集、交集、并集，或用集合成员关系过滤目标。

| 二级意图 | 中文名 | 说明 | 示例 |
|---|---|---|---|
| `set_difference_query` | 集合差集查询 | 查询属于集合 A 但不属于集合 B 的对象 | 查询服务 A 使用但服务 B 未使用的隧道；查询网元 A 有但网元 B 没有的端口 |
| `set_intersection_query` | 集合交集查询 | 查询同时属于多个集合的对象 | 查询服务 A 和服务 B 共同使用的隧道；查询两个业务都经过的网元 |
| `set_union_query` | 集合并集查询 | 合并多个集合并返回去重结果 | 查询服务 A 或服务 B 使用的全部隧道；查询业务 A 和业务 B 涉及的全部网元 |
| `set_membership_filter_query` | 集合成员过滤查询 | 先得到一个集合，再用集合成员关系过滤目标 | 查询经过故障端口所在设备集合的业务；查询关联到 VendorA 网元集合的服务 |

分类边界：

- “共同、同时属于、只属于、未使用、差异、任一、全部合并”归 `set_operation_query`。
- “同时满足条件”不是集合交集；如果最终返回明细，归 `record_retrieval_query`；如果最终统计，归相应分析类。

### 3.10 `existence_query`：存在性查询

判断实体、关系、路径或条件是否存在，最终回答通常是布尔语义。

| 二级意图 | 中文名 | 说明 | 示例 |
|---|---|---|---|
| `entity_existence_query` | 实体存在性查询 | 判断实体或资源是否存在 | 是否存在 ID 为 svc-mpls-vpn-1001 的服务；系统里有没有名称为 Tunnel_001 的隧道 |
| `relationship_existence_query` | 关系存在性查询 | 判断两个对象之间是否存在关系 | 服务 A 是否使用了隧道 B；设备 A 是否连接了端口 P |
| `path_existence_query` | 路径存在性查询 | 判断两个对象之间是否存在路径或可达关系 | 设备 A 和设备 B 之间是否存在连接路径；业务 S 是否能到达端口 P |
| `condition_satisfaction_query` | 条件满足性查询 | 判断是否存在满足条件的记录或对象 | 是否有状态为 down 的端口；是否存在带宽大于 1000 的链路 |

分类边界：

- 期望回答“是/否、有/无、能/不能”，归 `existence_query`。
- “有哪些/是什么/列出”不是存在性查询，按最终返回形态归类。

## 4. 建议输出结构

意图识别模块建议输出精简 intent，结构细节由槽位、schema linking 和结构特征承接。

```json
{
  "primary_intent": "ranking_query",
  "secondary_intent": "metric_ranking_query",
  "confidence": 0.86,
  "source": "embedding",
  "decision": "accept"
}
```

配套结构特征示例：

```json
{
  "answer_type": "table",
  "requires_path": true,
  "hop_count": 2,
  "aggregation_functions": ["count"],
  "group_by": true,
  "order_limit": "topn",
  "time_grain": "none"
}
```

建议输出以下辅助结构特征，用于 query planning、评测和错误分析：

| 字段 | 含义 | 示例值 |
|---|---|---|
| `answer_type` | 返回结果类型 | `record`、`attribute_table`、`relation`、`path`、`subgraph`、`scalar`、`metric_table`、`boolean` |
| `requires_path` | 是否需要图关系或路径匹配 | `true`、`false` |
| `hop_count` | 查询路径跳数 | `0`、`1`、`2`、`3`、`variable` |
| `relation_chain_type` | 关系链类型 | `none`、`direct`、`fixed_chain`、`variable_chain` |
| `filter_level` | 过滤位置 | `none`、`record_filter`、`relation_filter`、`path_filter`、`post_aggregate_filter`、`set_filter` |
| `aggregation_functions` | 聚合函数 | `count`、`count_distinct`、`sum`、`avg`、`max`、`min` |
| `group_by` | 是否分组 | `true`、`false` |
| `group_by_dimensions` | 分组维度 | `vendor`、`status`、`service_type` |
| `order_limit` | 排序截断 | `none`、`order_only`、`limit_only`、`topn`、`bottomn` |
| `time_grain` | 时间粒度 | `none`、`hour`、`day`、`week`、`month`、`year` |
| `comparison_target_type` | 对比对象类型 | `none`、`entity`、`segment`、`metric`、`period` |
| `derived_metric_type` | 派生指标类型 | `none`、`ratio`、`share`、`coverage`、`utilization`、`growth_rate` |
| `set_operation_type` | 集合操作类型 | `none`、`difference`、`intersection`、`union`、`membership_filter` |
| `path_return_type` | 路径返回类型 | `none`、`trace`、`reachable_entities`、`path_list`、`subgraph` |

## 5. 参考依据

- [WikiSQL / Seq2SQL](https://arxiv.org/abs/1709.00103)：采用选择列、聚合函数、过滤条件等 sketch 组件表达查询结构。
- [TypeSQL](https://arxiv.org/abs/1804.09769)：将 Text-to-SQL 建模为 slot filling 问题，强调结构槽位和类型感知。
- [Spider](https://arxiv.org/abs/1809.08887)：覆盖多表关系、复杂条件、聚合、排序、嵌套查询等查询结构。
- [RAT-SQL](https://arxiv.org/abs/1911.04942)：强调 schema linking 与关系感知编码。
- [RASAT](https://arxiv.org/abs/2205.06983)：进一步利用关系感知结构增强 Text-to-SQL 语义解析。
- [Text-to-SQL / NLIDB survey](https://link.springer.com/article/10.1007/s00778-022-00776-8)：将 schema linking、query decoding、output refinement 等作为独立模块。
- ATIS / SNIPS 等 NLU 任务：采用 intent detection + slot filling 的经典拆分。
