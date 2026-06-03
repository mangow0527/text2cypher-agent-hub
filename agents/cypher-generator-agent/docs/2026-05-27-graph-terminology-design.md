# Graph-native Terminology Design

> 日期：2026-05-27
> 状态：设计 v1
> 目的：统一 Graph Semantic Model、DSL、Validator、Trace 和 Cypher Compiler 的命名

## 1. Hard Cut 原则

本项目从本版本起采用 `Graph Semantic Model Specification v1` 作为单一权威定义。全链路使用图原生术语：

- `vertex`
- `edge`
- `property`
- `path_pattern`
- `metric`

禁止继续在语义层、DSL、Prompt、Trace、Validator 或 Compiler 内使用以下旧术语：

- `dataset`
- `relationship`
- `field`
- `source`
- `primary_key`
- `unique_keys`
- `from_columns`
- `to_columns`
- `expression.dialects`
- `dimension.is_time`
- `custom_extensions`

这次迁移不是兼容升级，而是 hard cut。实现层不提供旧字段 alias，不接受双轨输入，不在 trace 中输出旧字段。

## 2. 标准术语表

| 标准术语 | 含义 | 代码/DSL 用法 |
| --- | --- | --- |
| `vertex` | 图顶点类型，`name` 必须等于 Cypher label | `vertex_name`、`vertex_ref` |
| `edge` | 图边类型，`name` 必须等于 Cypher edge type | `edge_name`、`edge_ref` |
| `property` | 顶点或边上的属性，`name` 必须等于 Cypher property name | `property_name`、`property_ref` |
| `id_property` | vertex 唯一标识属性 | 必须引用该 vertex 的 property |
| `metric` | 可复用业务指标 | 来自 graph semantic model 的 `metrics[]` |
| `measure` | 查询 DSL 中本次聚合产生的输出 | 仅用于 DSL aggregate/subquery 输出别名 |
| `path_pattern` | 命名路径模板 | 来自 graph semantic model 的 `path_patterns[]` |
| `value_synonyms` | 枚举值自然语言映射 | property 顶层约束，不放入 `ai_context` |
| `direction_semantics` | edge 存储方向和业务方向说明 | edge 顶层权威约束 |
| `anti_patterns` | edge 使用反模式 | edge 顶层权威约束 |

## 3. 命名约定

推荐类型名：

- `GraphSemanticModel`
- `VertexDefinition`
- `EdgeDefinition`
- `PropertyDefinition`
- `MetricDefinition`
- `PathPatternDefinition`
- `VertexBinding`
- `EdgeBinding`
- `PropertyBinding`
- `MetricBinding`
- `CypherSelfValidationResult`

避免：

- `DatasetBinding`
- `RelationshipBinding`
- `FieldBinding`
- `Ontology*`
- `Osi*`

## 4. 文档与代码规则

- 文档标题不再使用 OSI。
- DSL 示例不出现 `semantic_type: dataset`、`relationship`、`field`。
- LiteralResolver 输入字段使用 `expected_vertex` 和 `expected_property`。
- Candidate Retriever 召回类型使用 `vertex`、`edge`、`property`、`metric`、`path_pattern`。
- Trace stage 内容使用 `vertex_bindings`、`edge_bindings`、`property_bindings`。
- Cypher Compiler 不做逻辑名到物理名映射；graph semantic model 中的 `name` 就是 Cypher 中的 label/type/property。

## 5. 迁移验收

迁移完成后，设计文档和后续实现必须通过以下检查：

- 不存在 `datasets`、`relationships`、`fields` 作为 schema 字段。
- 不存在 `expression.dialects`。
- 不存在 `from_columns` / `to_columns`。
- 不存在 `dimension.is_time`。
- 不存在 OSI adapter 或双轨兼容描述。
- 所有 edge 都有 `cardinality`。
- 所有反直觉方向 edge 都有 `direction_semantics`。
- 所有枚举 property 的 `value_synonyms` key 都出现在 `valid_values` 中。
