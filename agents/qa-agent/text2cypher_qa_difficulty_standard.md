# Text2Cypher QA 难度分级标准

## 1. 文档目的

本文档用于定义本项目中 `Text2Cypher QA 对` 的难度分级标准，作为后续 QA 生成、导入、评测和筛选时的统一依据。

本标准的来源是用户提供的论文 [v1.pdf](/Users/wangxinhao/muti-agent-offline-system/v1.pdf)。

需要先明确一点：

- 论文原始定义的是 **4 档查询难度**
- 并 **没有直接给出 8 档**
- 因此本文档采用：
  - **论文原始 4 档作为母标准**
  - **在不改变论文判定逻辑的前提下，细化为项目可执行的 8 级标准**

也就是说：

- **论文标准**：4 档
- **项目标准**：8 级
- **项目 8 级是对论文 4 档的工程化细分，不是重新发明一套规则**

---

## 2. 论文原始依据

根据 [v1.pdf](/Users/wangxinhao/muti-agent-offline-system/v1.pdf)：

- 第 7 页 `Table 2: Case Study of Graph Query Complexity Taxonomy`
- 第 7-8 页 `4.5 Difficulty Definition`

论文原始定义了 4 档：

1. `Easy`
2. `Medium`
3. `Hard`
4. `Extra Hard`

论文的核心判断原则不是句子长短，而是：

- **图查询的拓扑复杂度**
- **求解所需的推理深度**

论文原始定义如下：

### 2.1 Easy

特征：

- 单节点或单边模式
- 无聚合
- 无复杂过滤

论文关键词：

- `single node or edge pattern`
- `no aggregation`
- `no complex filtering`

### 2.2 Medium

特征：

- 一跳路径
- 简单聚合
- 基础过滤
- 无嵌套

论文关键词：

- `one-hop path`
- `simple aggregation`
- `basic filters`
- `no nesting`

### 2.3 Hard

特征：

- 多跳路径，最多 2 跳
- 或变长路径
- 多个条件
- 非嵌套聚合

论文关键词：

- `multi-hop paths (<=2 hops) or variable-length paths`
- `multiple conditions`
- `non-nested aggregation`

### 2.4 Extra Hard

特征：

- 复杂路径，3 跳及以上
- 多阶段 MATCH
- 嵌套聚合
- 高结构复杂度和高逻辑深度

论文关键词：

- `complex paths (>=3 hops)`
- `multi-step MATCH`
- `nested aggregation`
- `high structural and logical depth`

---

## 3. 项目 8 级标准

为了让 QA 生成系统更细粒度地控量、抽样、评测和分桶，项目采用 8 级标准：

1. `L1`
2. `L2`
3. `L3`
4. `L4`
5. `L5`
6. `L6`
7. `L7`
8. `L8`

它们与论文 4 档的映射关系如下：

| 项目等级 | 对应论文档位 | 含义 |
|---|---|---|
| `L1` | Easy | 最简单单实体查询 |
| `L2` | Easy | 简单过滤或单边查询 |
| `L3` | Medium | 一跳关系查询 |
| `L4` | Medium | 一跳 + 基础聚合/排序/限制 |
| `L5` | Hard | 两跳或变长路径，单主约束 |
| `L6` | Hard | 两跳/变长 + 多条件/非嵌套聚合 |
| `L7` | Extra Hard | 三跳及以上，或多阶段 MATCH |
| `L8` | Extra Hard | 高结构复杂度 + 多阶段推理 + 嵌套聚合 |

---

## 4. 8 级详细判定规则

以下规则按“就高不就低”的原则判定。

也就是说：

- 如果一个样本同时满足多个等级条件
- 则取 **最高等级**

### 4.1 L1: 单实体直接检索

定义：

- 只涉及一个节点类型
- 没有边遍历
- 没有聚合
- 没有复杂过滤
- 只做直接返回

典型形式：

```cypher
MATCH (n:Label)
RETURN n
LIMIT 5
```

判定关键词：

- 单节点
- 无 WHERE 或仅极简单常量过滤
- 无 ORDER BY
- 无聚合

### 4.2 L2: 单实体过滤或单边简单返回

定义：

- 仍处于论文 `Easy`
- 但比 L1 多了一层简单条件或单边模式

适用情况：

- 单节点 + 单字段过滤
- 单边关系模式，但不做路径推理
- 无聚合
- 无嵌套

典型形式：

```cypher
MATCH (n:Label)
WHERE n.id = 'x'
RETURN n
LIMIT 10
```

或

```cypher
MATCH (a:LabelA)-[:REL]->(b:LabelB)
RETURN a, b
LIMIT 5
```

### 4.3 L3: 一跳关系查询

定义：

- 对应论文 `Medium`
- 存在明确的一跳遍历
- 不包含复杂聚合
- 不包含多阶段结构

典型形式：

```cypher
MATCH (a:A)-[:REL]->(b:B)
RETURN b.name
LIMIT 10
```

### 4.4 L4: 一跳 + 基础分析

定义：

- 对应论文 `Medium`
- 一跳关系基础上加入：
  - 简单聚合
  - 排序
  - Top-K
  - 基础过滤
- 但仍无嵌套

典型形式：

```cypher
MATCH (a:A)-[:REL]->(b:B)
WHERE a.score > 0.5
RETURN b.level, b.created_at
LIMIT 10
```

或

```cypher
MATCH (n:Label)
RETURN count(n) AS total
```

### 4.5 L5: 两跳或变长路径的基础推理

定义：

- 对应论文 `Hard`
- 涉及：
  - 两跳路径
  - 或变长路径
- 但逻辑条件仍然有限

典型形式：

```cypher
MATCH (a:A)-[:R1]->(:B)-[:R2]->(c:C)
RETURN c
LIMIT 5
```

或

```cypher
MATCH (a:A)-[]->{1,3}(b:B)
RETURN b
LIMIT 5
```

### 4.6 L6: 两跳/变长 + 多条件组合

定义：

- 仍对应论文 `Hard`
- 在 L5 基础上进一步加入：
  - 多个过滤条件
  - 多个返回目标
  - 非嵌套聚合
  - 多条件排序/限制

典型形式：

```cypher
MATCH (a:A)-[:R1]->(:B)-[:R2]->(c:C)
WHERE a.status = 'up' AND c.level > 3
RETURN c.id, count(*) AS total
ORDER BY total DESC
LIMIT 5
```

### 4.7 L7: 三跳以上或多阶段 MATCH

定义：

- 对应论文 `Extra Hard`
- 只要出现以下任一条件，至少判为 L7：
  - 三跳及以上
  - 多阶段 MATCH
  - 多段图结构推理

典型形式：

```cypher
MATCH (a:A)-[:R1]->(:B)-[:R2]->(:C)-[:R3]->(d:D)
RETURN d
```

或

```cypher
MATCH (a:A)-[:R1]->(b:B)
RETURN a
NEXT MATCH (a)-[:R2]->(c:C)
RETURN DISTINCT a.id, c.name
```

### 4.8 L8: 高结构复杂度 + 深推理

定义：

- 对应论文 `Extra Hard`
- 是项目中的最高等级
- 同时具备多项高复杂度特征

典型特征：

- 三跳及以上复杂路径
- 多阶段 MATCH
- 嵌套聚合
- 多子目标联合约束
- 需要对中间结果做全局推理

典型形式：

```cypher
MATCH (a:A)-[:R1]->(b:B)
WHERE ...
WITH a, count(b) AS cnt
MATCH (a)-[:R2]->(c:C)
WHERE cnt > 3
RETURN a.id, count(c)
```

---

## 5. 机器判定的优先级规则

后续系统里，难度不应靠 LLM 主观判断，而应按结构规则判定。

推荐优先级如下：

1. 是否存在嵌套聚合
2. 是否存在多阶段 MATCH
3. 最大路径跳数
4. 是否为变长路径
5. 条件数量
6. 是否存在聚合
7. 是否存在排序/Top-K
8. 是否仅为单实体检索

推荐判定流程：

1. 若存在嵌套聚合，判 `L8`
2. 若存在多阶段 MATCH 或 3 跳及以上路径，判 `L7`
3. 若存在两跳或变长路径，继续看条件复杂度：
   - 条件少，判 `L5`
   - 条件多或含非嵌套聚合，判 `L6`
4. 若只有一跳：
   - 仅关系查询，判 `L3`
   - 一跳 + 聚合/排序/过滤，判 `L4`
5. 若无路径：
   - 直接检索，判 `L1`
   - 简单过滤或单边简单返回，判 `L2`

---

## 6. QA 对最终输出字段是否需要这么多

如果你的目标只是“给 agent 消费最终 QA 对”，那你说得对：

- **最终导出视图没必要带太多字段**

你最关心的最小输出可以收缩为：

1. `id`
2. `question`
3. `cypher`
4. `answer`
5. `difficulty`

推荐最小导出格式：

```json
{
  "id": "qa_000001",
  "question": "网络元素总共有多少个？",
  "cypher": "MATCH (n:NetworkElement) RETURN count(n) AS total",
  "answer": [
    {"total": 40}
  ],
  "difficulty": "L4"
}
```

这里的 `answer` 推荐使用执行后的最终结果，而不是自然语言答案。

原因很简单：

- 对 agent 来说，最直接可验证的 supervision 是 `question -> cypher -> answer`
- 自然语言答案很容易引入额外表述噪声
- 结构化结果更适合训练、比对和回归

---

## 7. 项目中的双层格式建议

因此，项目里建议保留两层格式：

### 7.1 内部生产格式

用于生成、校验、去重、回放、抽检。

字段可以完整保留：

- `id`
- `question_canonical_zh`
- `question_variants_zh`
- `cypher`
- `cypher_normalized`
- `query_types`
- `difficulty`
- `validation`
- `result_signature`
- `split`
- `provenance`

### 7.2 对外导出格式

用于给 agent 直接消费。

字段建议收缩为：

- `id`
- `question`
- `cypher`
- `answer`
- `difficulty`

如果后续要做 few-shot 示例库，可以额外保留：

- `question_variants`

但默认不需要把内部治理字段全部暴露出去。

---

## 8. 本项目的最终结论

从现在开始，本项目难度标准采用如下规则：

- **依据论文原始 4 档难度定义**
- **工程上细化为 8 级：L1-L8**
- **QA 对外导出默认只保留最小字段：id / question / cypher / answer / difficulty**

即：

```text
论文标准 = 4 档
项目执行标准 = 8 级
最终导出标准 = 5 个核心字段
```

后续如果将该标准落到代码中，统一要求：

- 内部仍保留完整治理字段
- 对外发布使用最小导出格式
- `difficulty` 一律使用 `L1-L8`

