# Schema Versioning Policy

> 日期：2026-05-27
> 状态：设计 v1
> 覆盖范围：cypher-generator-agent 生成链路中的结构化 schema

## 1. 目标

本策略定义各类 schema 如何独立演进、如何保持兼容、以及生产者和消费者升级顺序。它避免 DSL、trace、LLM structured output、repair decision 等 schema 各自升级后出现不可见断裂。

## 2. Schema 清单

| schema | 当前版本 | 生产者 | 消费者 | 是否可独立演进 |
| --- | --- | --- | --- | --- |
| Graph Semantic Model | `graph_semantic_model_v1` | model authoring / loader | registry、retriever、validator、compiler | 是，但 breaking change 需要新 major |
| Question Decomposition | `question_decomposition_v1` | Question Decomposer | Candidate Retriever、Coverage Validator | 是 |
| Grounded Understanding | `grounded_understanding_v1` | Grounded Understanding Selector | Semantic Binder、Repair Loop | 是 |
| Literal Resolver Request | `literal_resolver_request_v1` | Semantic Binder / Resolver caller | LiteralResolver | 是 |
| Literal Resolver Result | `literal_resolver_result_v1` | LiteralResolver | Semantic Validator、Repair Controller | 是 |
| Restricted Query DSL | `restricted_query_dsl_v1` | DSL Builder | DSL Parser、Compiler、Self-Validation | 否，compiler 必须同步支持 |
| Repair Controller Input | `repair_controller_input_v1` | Validator / Self-Validation | Repair Controller | 是 |
| Repair Controller Decision | `repair_controller_decision_v1` | Repair Controller | API response、runtime console、trace writer | 是 |
| Cypher Self-Validation Request | `cypher_self_validation_request_v1` | Model Loader、Compiler | Self-Validator | 是 |
| Cypher Self-Validation Result | `cypher_self_validation_result_v1` | Self-Validator | Repair Controller、Observability | 是 |
| Trace | `cga_graph_trace_v1` | trace writer | testing-agent、runtime console、repair-agent | 是，需兼容窗口 |

## 3. 版本命名

Schema version 使用稳定字符串，不使用浮点版本：

```text
<schema_name>_v<major>
```

示例：

- `restricted_query_dsl_v1`
- `restricted_query_dsl_v2`
- `cga_graph_trace_v1`

patch 级别的非破坏性扩展不改变 `schema_version`，但需要更新文档和 JSON Schema 文件 checksum。

## 4. Breaking Change

以下变更必须升 major：

- 删除字段。
- 字段改名。
- 字段类型改变。
- enum 删除或语义改变。
- 必填字段新增。
- DSL op 语义改变。
- trace stage 的核心字段改名。
- error code 语义改变。

以下变更可以不升 major：

- 新增可选字段。
- 新增 warning code。
- 新增 metric。
- enum 新增值，但消费者必须按 unknown value 降级处理。
- 描述性文本变更。

## 5. 升级顺序

兼容升级顺序：

1. 消费者先支持新旧两个版本。
2. 生产者开始输出新版本。
3. trace 和 metrics 同时记录新旧版本分布。
4. 兼容窗口结束后，消费者移除旧版本。

`restricted_query_dsl` 是例外：DSL Builder、DSL Parser、Compiler、Self-Validation 必须在同一个 release 中支持同一 major。不能让 Builder 输出 Parser/Compiler 不理解的新 DSL。

## 6. 兼容窗口

默认兼容窗口：

- LLM structured output schema：1 个 minor release。
- LiteralResolver schema：1 个 minor release。
- Repair Controller schema：1 个 minor release。
- Trace schema：2 个 minor release，因为 testing-agent 和 runtime console 可能滞后。
- Restricted DSL major：无跨 major 兼容窗口，必须同步升级。

## 7. Unknown Attribute 与 Unknown Enum

消费者规则：

- unknown optional attribute：保留到 trace，但不影响处理。
- unknown required attribute：schema validation failed。
- unknown enum value：如果字段有默认安全降级，按降级处理；否则 schema validation failed。
- unknown error code：按 `generation_failed` 或最安全的非成功状态处理，不自动继续生成。

## 8. Trace 要求

每次生成 trace 必须记录：

- 每个 stage 的输入 schema_version。
- 每个 stage 的输出 schema_version。
- DSL schema_version。
- graph semantic model spec_version。
- self-validation request/result schema_version。

当某 stage 因 schema 不兼容失败时，错误码必须为 `schema_version_incompatible`，并记录 expected/actual。
