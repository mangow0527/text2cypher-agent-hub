# Text2Cypher QA 对生成统一方案

## 1. 文档目标

本文档给出一套统一的 `Text2Cypher` QA 对生成方案，用于为 Agent 构建高质量 `自然语言问题 -> Cypher` 训练与评测样本。方案只保留一条主线，不提供分支建议，不依赖具体图 Schema 内容；Schema 将作为后续输入接入本方案。

本文档整合了当前公开资料中较成熟的学术与工业实践，包括：

- Neo4j `text2cypher-2024v1` 数据集及其构建方法
- `Text2Cypher: Bridging Natural Language and Graph Databases`
- `SyntheT2C`
- `Auto-Cypher`
- `Mind the Query`
- `Text2Cypher Across Languages`
- Neo4j GraphRAG `Text2CypherRetriever`
- LangChain `GraphCypherQAChain`
- `Text2GraphQuery-DataGen`

统一后的核心思想是：

`先生成并验证 Cypher，再基于 Cypher 生成问题，再进行多级校验与分层沉淀。`

这个顺序是本方案的唯一生成路径，也是全文的中心约束。

## 2. 统一方案的基本原则

### 2.1 生成对象不是“问题”，而是“可验证查询”

QA 对生成的起点不是自由文本问题，而是可执行、可校验、可归类的 Cypher 查询。原因是 Text2Cypher 的根本难点不在语言表面，而在图结构约束、关系方向、属性选择、聚合语义和路径语义的正确性。公开工作中，稳定的数据构造方法都以结构化查询为中心，再反向生成自然语言。

### 2.2 样本质量由验证链条决定，不由模型置信度决定

统一方案不接受“LLM 觉得合理”的样本作为最终样本。每条数据必须经过固定验证链：语法、Schema、一致性、执行、结果合理性、多样性。只有通过验证链的样本才进入数据集。

### 2.3 语言多样性放在查询正确性之后

语言改写必须建立在已验证 Cypher 之上。先保查询正确，再扩语言表达；而不是让模型从自然语言直接猜查询。这样可以显著降低关系类型幻觉、方向错误、属性拼写错误和聚合错配。

### 2.4 数据集必须同时服务训练、检索和评测

统一方案生成的不是单一用途数据，而是统一样本格式下的多级资产。每条样本都保留查询类别、难度、验证记录和结果摘要，以便同时用于：

- SFT 训练
- few-shot 示例检索
- 回归测试
- 基准评测
- 线上回流扩充

## 3. 统一方案总流程

统一方案由 8 个阶段组成，必须按顺序执行：

1. Schema 抽象化
2. 查询类型空间定义
3. Cypher 骨架生成
4. 查询实例化
5. 查询验证
6. 问题生成与改写
7. 样本复验与去重
8. 数据分层与发布

整体流程可表示为：

`Schema 输入 -> 查询模式库 -> 可执行 Cypher -> 多级验证 -> 中文问题生成 -> 复验 -> 数据分层输出`

## 4. 阶段一：Schema 抽象化

本阶段不直接产出 QA，而是把原始图结构转换成生成阶段可消费的统一 Schema 表示。

### 4.1 输入

后续由外部提供图 Schema。虽然本次任务不讨论具体 Schema 内容，但统一方案要求 Schema 在进入后续阶段前被标准化为以下抽象：

- 节点类型集合
- 关系类型集合
- 节点属性与类型
- 关系属性与类型
- 主标识属性或候选唯一键
- 常见枚举值或值域摘要
- 时间、数值、布尔、文本等属性类型标签
- 索引与约束信息

### 4.2 输出

输出为 `Canonical Schema Spec`，它是后续所有阶段唯一可读取的 Schema 视图。任何后续生成都只能访问该标准视图，不直接访问底层库结构。

### 4.3 设计要求

Schema 表示必须同时支持：

- 查询结构生成
- 实体/属性合法性检查
- 值类型约束
- 问题生成时的语义描述

这一设计吸收了 `Mind the Query` 中对 Schema、运行和取值一致性分别校验的做法，也与 Neo4j、LangChain 运行时链路中“先注入 schema context 再生成查询”的工业实践一致。

## 5. 阶段二：查询类型空间定义

本方案采用固定查询类型空间，作为全量 QA 对生成的骨架。所有 Cypher 都必须映射到类型空间中的一种或多种组合标签。

### 5.1 查询类型集合

统一方案定义如下查询类型空间：

- `LOOKUP`：实体或属性查找
- `FILTER`：单条件或多条件过滤
- `SORT_TOPK`：排序、Top-K、排名
- `AGGREGATION`：`count`、`sum`、`avg`、`min`、`max`
- `GROUP_AGG`：分组聚合
- `MULTI_HOP`：两跳及以上关系查询
- `COMPARISON`：实体间比较、属性比较、聚合比较
- `TEMPORAL`：时间区间、时间顺序、最近/最早/近 N 年
- `PATH`：路径存在性、最短路径、路径约束
- `SET_OP`：交、并、差、去重、集合投影
- `SUBQUERY`：子查询、嵌套、局部聚合
- `HYBRID`：混合型复杂查询

### 5.2 类型空间的作用

类型空间的作用有四个：

- 约束生成覆盖面
- 标记难度层级
- 指导问题改写风格
- 支持后续训练集分层采样

这一做法综合了 `Neo4j text2cypher` 数据集、`Mind the Query` 的 benchmark 组织方式和 `Auto-Cypher` 中对大量查询类型进行覆盖的思想。

## 6. 阶段三：Cypher 骨架生成

统一方案的真正起点是 Cypher 骨架，不是自然语言。

### 6.1 骨架定义

Cypher 骨架是尚未填入具体 Schema 元素和值的抽象查询模板，包含：

- 匹配模式
- 变量关系结构
- 条件位
- 聚合位
- 排序位
- 返回位

例如，一个 `GROUP_AGG + SORT_TOPK + TEMPORAL` 骨架应包含：

- 至少一个时间过滤槽位
- 至少一个聚合表达式
- 至少一个排序表达式
- 至少一个 `LIMIT`

### 6.2 骨架生成原则

所有骨架必须满足：

- 可绑定到任意合法 Schema 子图
- 可实例化为可执行查询
- 可映射到明确查询类型
- 可逆向表达为自然语言问题

### 6.3 骨架来源

骨架库由三类理论与实践融合而成：

- 来自 `SyntheT2C` 的模板化生成思想
- 来自 `Text2GraphQuery-DataGen` 的图查询模式空间抽象
- 来自工业链路中实际高频查询模式的抽象总结

统一方案要求先建立一套稳定骨架库，再做后续实例化。骨架库一经确定，在同一轮数据生产中保持不变。

## 7. 阶段四：查询实例化

实例化是把骨架绑定到具体 Schema 元素和具体值，产出候选 Cypher。

### 7.1 实例化内容

对于每个骨架，依次完成：

- 节点类型绑定
- 关系类型绑定
- 属性槽位绑定
- 过滤表达式绑定
- 聚合表达式绑定
- 排序字段绑定
- 返回字段绑定
- 字面量值绑定

### 7.2 实例化约束

实例化时必须遵守以下约束：

- 节点和关系必须真实存在于 Schema
- 属性必须属于对应节点或关系
- 值类型必须与属性类型一致
- 返回列必须与问题可表达语义匹配
- 对复杂查询，必须能产生可解释的结果结构

### 7.3 实例化策略

统一方案采用“结构先行、值后绑定”的单一路径：

1. 先完成结构合法绑定
2. 再补充过滤值和时间值
3. 再生成聚合与排序
4. 再决定返回列

这个顺序吸收了 `Auto-Cypher` 中对“先保证查询可验证，再扩展数据”的思路，也与工业中 `schema-grounded generation` 的做法一致。

## 8. 阶段五：查询验证

这是统一方案的核心。候选 Cypher 只有全部通过验证链才能进入问题生成阶段。

### 8.1 验证链定义

每条候选 Cypher 必须依次通过以下 5 级验证：

1. `Syntax Validation`
2. `Schema Validation`
3. `Type and Value Validation`
4. `Runtime Validation`
5. `Result Sanity Validation`

### 8.2 Syntax Validation

验证查询是否满足 Cypher 语法要求。可使用：

- `EXPLAIN`
- Cypher parser
- 数据库预编译接口

这一步对应 `Text2Cypher: Bridging Natural Language and Graph Databases` 中对 `EXPLAIN` 级清洗的做法。

### 8.3 Schema Validation

验证：

- 标签是否存在
- 关系类型是否存在
- 属性是否存在
- 关系方向是否与 Schema 子图一致
- 子查询引用变量是否作用域合法

这一步对应 `Mind the Query` 的 schema check，也与 Neo4j 运行时链和 LangChain `validate_cypher` 的工业机制一致。

### 8.4 Type and Value Validation

验证：

- 字面量类型与属性类型是否匹配
- 时间表达式是否落在合法形式
- 数值比较是否使用了数值属性
- 枚举值、实体值、类别值是否真实可用

这里综合了 `Auto-Cypher` 的可验证实例构造思想和 `Mind the Query` 的 value check 思路。

### 8.5 Runtime Validation

将查询在目标图数据库上执行，记录：

- 是否运行成功
- 是否报错
- 返回列结构
- 返回行数
- 执行耗时

### 8.6 Result Sanity Validation

运行成功后继续判断：

- 返回列是否与查询意图一致
- 是否出现明显空列或恒空值
- 聚合结果是否合理
- `Top-K` 是否真的经过排序
- `distinct`、计数、分组结果是否与表达式一致

这一步是把学术数据集里的“值级一致性”与工业里的“结果结构 sanity check”合并为同一层。

### 8.7 验证输出

每条候选 Cypher 的验证结果必须结构化存储，形成：

- 验证布尔值
- 失败原因码
- 失败阶段
- 结果摘要
- 执行元信息

未通过任一验证阶段的样本立即淘汰，不进入后续步骤。

## 9. 阶段六：问题生成与改写

只有通过全部验证的 Cypher 才能生成自然语言问题。

### 9.1 输入

问题生成模型只读取以下内容：

- 标准化 Schema 摘要
- 已通过验证的 Cypher
- 查询类型标签
- 返回列语义
- 小规模结果样例或结果摘要

### 9.2 生成目标

对于每条 Cypher，生成一组语义等价的中文问题。问题必须：

- 与 Cypher 语义严格一致
- 不引入查询中不存在的实体、条件或比较关系
- 不丢失聚合、时间、排序、Top-K 等关键约束
- 不将数据库术语错误转述为业务术语

### 9.3 生成机制

统一方案采用“两段式生成”：

1. 先生成 `Canonical Question`
2. 再围绕 `Canonical Question` 生成 `Paraphrase Variants`

#### Canonical Question

这是最标准、约束最完整、信息最齐全的问法，用于：

- 训练主标签
- 评测黄金问题
- 作为变体生成锚点

#### Paraphrase Variants

在不改变语义的前提下，生成多个中文等价问法，覆盖：

- 正式问法
- 口语问法
- 简洁问法
- 带上下文问法
- 业务简称问法

### 9.4 生成约束

问题生成阶段必须遵守以下硬约束：

- 不允许补充 Cypher 中没有的限制条件
- 不允许省略决定结果集边界的关键信息
- 不允许把返回值类型问错
- 不允许改变比较对象
- 不允许改变时间方向、数量约束或排序方向

### 9.5 多语言理论的吸收方式

`Text2Cypher Across Languages` 表明跨语言时语义漂移会显著增加。因此本方案对中文问题生成采用“原生中文改写”，而不是“英文问题先生成再翻译”。统一方案中，中文问题直接从 Cypher 和 Schema 语义生成，不经过英文中转。

## 10. 阶段七：样本复验与去重

问题生成完成后，样本仍未结束，必须进行一次“问题到查询”的闭环复验。

### 10.1 闭环复验

对每个 `问题 -> Cypher` 样本执行如下复验：

1. 将问题和 Schema 再次输入 Text2Cypher 生成器
2. 观察生成结果是否与目标 Cypher 语义一致
3. 若一致性显著不足，则样本降级或淘汰

这一步吸收了 `Auto-Cypher` 的生成-验证闭环思想，但在统一方案中，它被定义为数据后验质量检验，而不是另起一条生成路线。

### 10.2 去重规则

必须同时做三类去重：

- `Cypher 去重`：规范化后完全一致的查询去重
- `问题近义去重`：高度相似中文问题去重
- `结果等价去重`：不同查询文本但语义等价的样本去重

### 10.3 难例保留

统一方案显式保留复杂样本，不因“低频”而删除。复杂样本包括：

- 多跳约束
- 嵌套聚合
- 时间与聚合混合
- 路径与排序混合
- 多重过滤与比较混合

这一点吸收了 `Text2Cypher: Data Pruning using Hard Example Selection` 的思想，但在本方案中，难例不是后处理裁剪策略，而是数据集组成原则。

## 11. 阶段八：数据分层与发布

最终通过全部流程的样本不直接混用，而是按质量和用途分层发布。

### 11.1 分层定义

统一方案要求至少形成三层：

- `Seed`
- `Silver`
- `Gold`

### 11.2 Seed 层

包含：

- 最基础、覆盖面完整的查询类型
- 表达清晰的 Canonical Question
- 执行通过且人工抽检通过的样本

用于：

- few-shot 示例库
- 初始模型对齐
- 最小可用 benchmark

### 11.3 Silver 层

包含：

- 大规模自动生成样本
- 通过全部自动验证链
- 语言变体较多

用于：

- 大规模 SFT
- 检索增强示例库扩容

### 11.4 Gold 层

包含：

- 复杂查询
- 多样化中文表达
- 高风险查询类型
- 额外人工复核通过样本

用于：

- 正式离线评测
- 模型回归测试
- 上线前稳定性验证

## 12. 统一样本格式

统一方案要求输出结构化样本，字段如下：

```json
{
  "id": "qa_000001",
  "question_canonical_zh": "近三年参与项目数量最多的前五位专家是谁？",
  "question_variants_zh": [
    "最近三年里参与项目最多的5位专家是谁？",
    "过去三年项目参与数排名前五的专家",
    "近三年参与项目最多的专家前5名"
  ],
  "cypher": "MATCH ... RETURN ...",
  "schema_spec_version": "v1",
  "query_types": ["GROUP_AGG", "TEMPORAL", "SORT_TOPK"],
  "difficulty": "hard",
  "result_signature": {
    "columns": ["expert", "project_count"],
    "row_count": 5
  },
  "validation": {
    "syntax": true,
    "schema": true,
    "type_value": true,
    "runtime": true,
    "result_sanity": true,
    "roundtrip_check": true
  },
  "provenance": {
    "skeleton_id": "group_agg_temporal_topk_01",
    "instantiation_id": "inst_009182",
    "generation_mode": "cypher_first"
  },
  "split": "silver"
}
```

这个格式同时服务训练、检索和评测，避免后续重复加工。

## 13. 统一质量门禁

为了保证数据集长期可扩展，本方案规定统一质量门禁。

### 13.1 单条样本门禁

单条样本必须满足：

- 查询语法合法
- Schema 合法
- 类型和值合法
- 可执行
- 结果结构合理
- 问题语义与查询严格一致
- 问题表述自然且不含歧义性缺失

### 13.2 批次门禁

每次批量生成完成后，整个批次必须满足：

- 查询类型分布覆盖完整
- 难度分层完整
- 问题长度分布合理
- 中文表达风格有变化但不过度冗余
- 高重复率样本被压缩
- 错误类型被记录并可回溯

### 13.3 错误分类

所有失败样本必须被标记为以下错误之一：

- `SYNTAX_ERROR`
- `SCHEMA_ERROR`
- `RELATION_DIRECTION_ERROR`
- `PROPERTY_ERROR`
- `TYPE_ERROR`
- `VALUE_ERROR`
- `RUNTIME_ERROR`
- `RESULT_MISMATCH`
- `QUESTION_DRIFT`
- `DUPLICATE`

这样可以支持后续针对性修复与回放。

## 14. 统一方案的理论来源整合说明

本方案不是对公开工作的并列罗列，而是将它们整合为单一方法学。

### 14.1 来自 `SyntheT2C`

吸收内容：

- 模板化生成思想
- 由结构化查询出发构建训练数据
- 通过模板与 LLM 结合提升覆盖率

在本文中的统一表达：

- 阶段三的 Cypher 骨架生成
- 阶段四的结构先行实例化

### 14.2 来自 `Auto-Cypher`

吸收内容：

- 生成-验证闭环
- 不以单次 LLM 输出为准，而以可验证性为准
- 对执行环境进行约束以验证查询正确性

在本文中的统一表达：

- 阶段五的多级验证链
- 阶段七的 round-trip 闭环复验

### 14.3 来自 `Mind the Query`

吸收内容：

- `schema / runtime / value / human review` 的严格质量框架
- 对 benchmark 质量而非数量的强调

在本文中的统一表达：

- 阶段五验证链的具体层次
- 阶段八中的 `Gold` 层定义

### 14.4 来自 Neo4j `text2cypher-2024v1` 与相关论文

吸收内容：

- 多来源样本整合
- 去重、清洗、语法校验
- 统一样本表示

在本文中的统一表达：

- 阶段七去重
- 阶段十二统一样本格式

### 14.5 来自 `Text2Cypher Across Languages`

吸收内容：

- 多语言环境下不能依赖简单翻译
- 语义保持比表面翻译更重要

在本文中的统一表达：

- 阶段六中“直接从 Cypher 生成中文问题”的约束

### 14.6 来自 GraphRAG 与 LangChain 工业实践

吸收内容：

- 运行时始终基于 Schema 生成查询
- 对查询做预校验和执行校验
- 保留中间结构以便回溯

在本文中的统一表达：

- Schema 先行
- Validation 先行
- 统一样本记录执行元信息

## 15. 最终落地定义

统一方案的最终产物不是“若干问句”，而是一套结构完整、可训练、可检索、可评测、可持续扩展的 `Text2Cypher QA 数据资产`。它以 Cypher 为中心，以验证为边界，以中文语义改写为外层扩展。

因此，本文最终确定的唯一方案可以概括为：

`标准化 Schema -> 固定查询类型空间 -> Cypher 骨架生成 -> 结构先行实例化 -> 五级验证 -> 基于已验证 Cypher 生成中文问题 -> round-trip 复验 -> 去重与难例保留 -> Seed/Silver/Gold 分层发布`

这就是本方案的唯一生产流程，后续所有实现都应严格服从这一流程。

## 16. 实施规范

本节将统一方案扩展为可执行规范。后续实现时，应按本节定义的数据接口、处理顺序和输出格式进行落地。

### 16.1 阶段级输入输出

#### 阶段一：Schema 抽象化

输入对象：

- 原始图数据库 Schema
- 约束、索引、字段类型信息
- 值域样本或统计摘要

输出对象：

```json
{
  "schema_spec_version": "v1",
  "node_types": [],
  "edge_types": [],
  "node_properties": {},
  "edge_properties": {},
  "constraints": [],
  "indexes": [],
  "value_catalog": {},
  "semantic_alias": {}
}
```

执行要求：

- 所有节点、关系、属性均需带类型标注
- 所有可枚举值优先写入 `value_catalog`
- 同义业务术语统一进入 `semantic_alias`

#### 阶段二：查询类型空间定义

输入对象：

- `Canonical Schema Spec`
- 固定查询类型空间

输出对象：

```json
{
  "taxonomy_version": "v1",
  "query_types": [
    {
      "name": "LOOKUP",
      "required_slots": [],
      "optional_slots": [],
      "difficulty_floor": "easy"
    }
  ]
}
```

执行要求：

- 每种查询类型必须定义必填槽位
- 每种查询类型必须定义默认难度下限
- 不允许出现未归类查询

#### 阶段三：Cypher 骨架生成

输入对象：

- `Canonical Schema Spec`
- 查询类型定义

输出对象：

```json
{
  "skeleton_id": "group_agg_temporal_topk_01",
  "query_types": ["GROUP_AGG", "TEMPORAL", "SORT_TOPK"],
  "pattern_template": "MATCH (...) ...",
  "slots": {
    "node_slots": [],
    "edge_slots": [],
    "property_slots": [],
    "filter_slots": [],
    "agg_slots": [],
    "order_slots": [],
    "return_slots": []
  }
}
```

执行要求：

- 每个骨架必须包含可实例化槽位描述
- 每个骨架必须标注可支持的查询类型组合
- 骨架不得绑定具体值

#### 阶段四：查询实例化

输入对象：

- `Canonical Schema Spec`
- 骨架库

输出对象：

```json
{
  "instantiation_id": "inst_009182",
  "skeleton_id": "group_agg_temporal_topk_01",
  "bound_schema_items": {
    "nodes": [],
    "edges": [],
    "properties": []
  },
  "cypher": "MATCH ... RETURN ...",
  "query_types": ["GROUP_AGG", "TEMPORAL", "SORT_TOPK"]
}
```

执行要求：

- 所有绑定必须可追踪到具体 Schema 项
- 所有字面量值必须可回溯到值目录或合法生成规则
- 输出 Cypher 必须已标准化格式

#### 阶段五：查询验证

输入对象：

- 实例化 Cypher
- `Canonical Schema Spec`

输出对象：

```json
{
  "validation": {
    "syntax": true,
    "schema": true,
    "type_value": true,
    "runtime": true,
    "result_sanity": true
  },
  "result_signature": {
    "columns": [],
    "row_count": 0,
    "column_types": []
  },
  "runtime_meta": {
    "latency_ms": 0,
    "planner": "",
    "warnings": []
  }
}
```

执行要求：

- 验证必须按顺序执行，前置失败即终止
- 所有失败必须记录失败阶段与原因码
- 运行成功不等于样本通过，仍需进行 `result_sanity`

#### 阶段六：问题生成与改写

输入对象：

- 已验证通过的 Cypher
- Schema 摘要
- 返回列语义
- 结果摘要

输出对象：

```json
{
  "question_canonical_zh": "",
  "question_variants_zh": [],
  "question_meta": {
    "style_tags": [],
    "constraint_keywords": [],
    "language": "zh-CN"
  }
}
```

执行要求：

- 每条 Cypher 必须先生成 1 条 `Canonical Question`
- 再生成固定数量的中文变体
- 变体只改变表达，不改变边界条件

#### 阶段七：样本复验与去重

输入对象：

- `Question -> Cypher` 候选样本
- Text2Cypher 生成器

输出对象：

```json
{
  "roundtrip_check": true,
  "dedup_key": {
    "cypher_norm_hash": "",
    "question_sem_hash": "",
    "result_sig_hash": ""
  }
}
```

执行要求：

- 所有样本必须进行 round-trip 复验
- 去重必须在 Cypher、问题、结果语义三个维度同时进行

#### 阶段八：数据分层与发布

输入对象：

- 已通过全部流程的样本

输出对象：

- `seed.jsonl`
- `silver.jsonl`
- `gold.jsonl`
- `manifest.json`
- `error_report.jsonl`

执行要求：

- 每次发布必须携带 `manifest`
- `manifest` 必须记录版本、统计量、错误分布、查询类型覆盖率

### 16.2 统一字段字典

为避免后续实现时字段漂移，统一定义关键字段含义。

#### 顶层字段

- `id`：样本唯一标识
- `schema_spec_version`：Schema 标准版本
- `taxonomy_version`：查询类型空间版本
- `skeleton_id`：骨架标识
- `instantiation_id`：实例化标识
- `split`：所属层级，取值为 `seed/silver/gold`

#### 查询字段

- `cypher`：原始标准化查询文本
- `cypher_normalized`：归一化后的查询文本
- `query_types`：查询类型标签数组
- `difficulty`：难度标签
- `query_depth`：跳数深度
- `constraint_count`：过滤约束数量
- `aggregation_count`：聚合表达式数量

#### 语言字段

- `question_canonical_zh`：标准中文问题
- `question_variants_zh`：中文变体数组
- `style_tags`：问法风格标签
- `constraint_keywords`：问题中显式出现的关键约束词

#### 验证字段

- `syntax`：语法校验结果
- `schema`：Schema 校验结果
- `type_value`：类型和值校验结果
- `runtime`：执行校验结果
- `result_sanity`：结果合理性校验结果
- `roundtrip_check`：闭环复验结果

#### 结果字段

- `columns`：返回列名
- `column_types`：返回列类型
- `row_count`：返回行数
- `result_preview`：结果样例摘要

#### 追溯字段

- `generation_mode`：固定取值 `cypher_first`
- `provenance`：生成来源信息
- `error_code`：失败时的统一错误码

### 16.3 难度定义规范

统一方案使用固定规则打标 `easy / medium / hard`。

#### easy

满足以下全部条件：

- 单跳或零跳
- 无子查询
- 聚合数不超过 1
- 过滤条件不超过 2
- 无时间与排序复合约束

#### medium

满足以下任一条件：

- 双跳查询
- 过滤条件 3 到 4 个
- 单层聚合加排序
- 存在时间过滤
- 存在比较操作

#### hard

满足以下任一条件：

- 三跳及以上
- 包含子查询
- 同时存在聚合、时间、排序三类约束
- 存在路径语义
- 存在多重分组或复杂比较

### 16.4 批处理执行规则

统一方案的每一批生成任务必须遵守固定批处理规则。

#### 批次最小单位

一批次由以下三元组唯一标识：

- `schema_spec_version`
- `taxonomy_version`
- `batch_id`

#### 批次处理顺序

1. 加载标准化 Schema
2. 加载固定查询类型空间
3. 生成骨架候选
4. 执行实例化
5. 运行五级验证
6. 对通过样本生成中文问题
7. 执行 round-trip 复验
8. 去重
9. 分层输出
10. 生成批次报告

#### 批次终止条件

任一批次在以下情况下必须终止并产出错误报告：

- `syntax` 失败率异常升高
- `schema` 失败率异常升高
- `runtime` 失败率超过设定阈值
- `question_drift` 比例超过设定阈值
- 查询类型覆盖率低于最低门槛

### 16.5 Prompt 模板规范

统一方案允许使用 LLM，但 LLM 只能在受控位置工作，并必须使用固定模板。

#### Prompt A：Canonical Question 生成

用途：

- 从已验证 Cypher 生成 1 条标准中文问题

模板：

```text
你是一个严格的 Text2Cypher 数据构造器。

任务：根据给定的 Schema 摘要、Cypher 查询、查询类型标签和返回列语义，生成一条严格等价的中文问题。

要求：
1. 只能表达 Cypher 中已有的语义，不得补充条件。
2. 不得遗漏排序、时间、Top-K、比较、聚合等关键约束。
3. 问题必须自然、明确、无歧义。
4. 不要输出解释，只输出问题文本。

输入：
Schema 摘要：
{schema_summary}

Cypher：
{cypher}

查询类型：
{query_types}

返回列语义：
{return_semantics}
```

#### Prompt B：中文变体生成

用途：

- 基于标准问题生成多个语义等价中文变体

模板：

```text
你是一个严格的中文问题改写器。

任务：对给定的标准问题生成若干个中文等价问法。

要求：
1. 所有改写必须与原问题语义完全一致。
2. 不得新增或删除任何约束。
3. 必须覆盖正式、口语、简洁三类表达。
4. 每条改写单独一行输出。

标准问题：
{canonical_question}

约束关键词：
{constraint_keywords}
```

#### Prompt C：问题语义复核

用途：

- 检查问题是否偏离 Cypher

模板：

```text
你是一个严格的 QA 一致性审查器。

任务：判断给定中文问题是否与 Cypher 查询严格等价。

输出格式：
PASS 或 FAIL

判定标准：
1. 条件、时间、排序、比较、聚合必须一致。
2. 返回对象和返回粒度必须一致。
3. 若问题表达比查询更宽或更窄，判定为 FAIL。

中文问题：
{question}

Cypher：
{cypher}
```

### 16.6 Round-Trip 复验规则

统一方案要求所有样本进行闭环复验，规则如下：

1. 输入：`question_canonical_zh + schema_summary`
2. 由 Text2Cypher 生成器重新生成查询
3. 将新查询做标准化
4. 与目标 `cypher_normalized` 比较
5. 若文本不同但执行结果签名一致且语义判定一致，可视为通过
6. 否则判定 `roundtrip_check = false`

通过标准必须同时满足以下至少一项：

- 归一化查询完全一致
- 查询不同但语义一致且结果签名一致

### 16.7 去重与保留规则

统一方案对去重使用固定优先级：

1. 先按 `cypher_normalized` 去重
2. 再按问题语义向量或相似度去重
3. 再按结果签名去重
4. 对重复样本保留验证信息更完整者
5. 若验证信息相同，保留问题更自然者
6. 若二者仍相同，保留查询更复杂者

### 16.8 产出目录规范

统一方案建议所有产出按以下目录组织：

```text
artifacts/
  schema/
    schema_spec_v1.json
  taxonomy/
    taxonomy_v1.json
  skeletons/
    skeletons_v1.jsonl
  instantiated/
    batch_001_candidates.jsonl
  validated/
    batch_001_validated.jsonl
  qa/
    batch_001_qa.jsonl
  releases/
    seed.jsonl
    silver.jsonl
    gold.jsonl
    manifest.json
  reports/
    batch_001_report.json
    error_report.jsonl
```

### 16.9 批次报告规范

每批次必须生成 `batch_report`，至少包含：

- 总骨架数
- 总实例化数
- 语法通过率
- Schema 通过率
- 执行通过率
- 结果合理性通过率
- round-trip 通过率
- 去重前后样本数
- 各查询类型样本数
- 各难度样本数
- 错误码分布

### 16.10 发布门禁

只有同时满足以下条件的数据集版本才允许发布：

- 三层数据均存在
- `gold` 层已完成人工抽检
- 所有查询类型均有覆盖
- 复杂查询占比未低于最低目标
- 去重完成
- `manifest` 和错误报告齐全

## 17. 参考资料

- [Neo4j Text2Cypher 2024 数据集](https://huggingface.co/datasets/neo4j/text2cypher-2024v1)
- [Introducing the Neo4j Text2Cypher Dataset](https://neo4j.com/blog/developer/introducing-neo4j-text2cypher-dataset/)
- [Text2Cypher: Bridging Natural Language and Graph Databases](https://aclanthology.org/2025.genaik-1.11/)
- [SyntheT2C: Generating Synthetic Data for Fine-Tuning Large Language Models on the Text2Cypher Task](https://aclanthology.org/2025.coling-main.46/)
- [SyntheT2C Code](https://github.com/ZGChung/SyntheT2C)
- [Auto-Cypher: Improving LLMs on Cypher generation via LLM-supervised generation-verification framework](https://aclanthology.org/2025.naacl-short.53/)
- [Auto-Cypher arXiv](https://arxiv.org/abs/2412.12612)
- [Mind the Query: A Benchmark Dataset towards Text2Cypher Task](https://research.ibm.com/publications/mind-the-query-a-benchmark-dataset-towards-text2cypher-task)
- [Text2Cypher Across Languages: Evaluating and Finetuning LLMs for Translating Multilingual Natural Language Questions into Cypher Queries](https://arxiv.org/abs/2506.21445)
- [Text2GraphQuery-DataGen](https://github.com/ldbc/Text2GraphQuery-DataGen)
- [Neo4j GraphRAG Text2Cypher Guide](https://neo4j.com/blog/genai/text2cypher-guide/)
- [LangChain Neo4j GraphCypherQAChain](https://docs.langchain.com/oss/python/integrations/graphs/neo4j_cypher)
- [Verify Neo4j Cypher Queries with CyVer](https://neo4j.com/blog/developer/verify-neo4j-cypher-queries-with-cyver/)
