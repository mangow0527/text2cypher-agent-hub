# Cypher Self-Validation v1 设计

> 日期：2026-05-27
> 状态：设计 v1
> 上游：Graph Model Loader、Cypher Compiler
> 下游：Repair and Clarification Controller、Observability、testing-agent

## 1. 设计目标

Cypher Self-Validation 是 cypher-generator-agent 在不连接 TuGraph 的前提下，对生成 Cypher 和语义模型内 Cypher 模板做的静态防线。

它必须能被两个入口独立调用：

- `validate_model_artifact`：Graph Model Loader 加载 `path_patterns[].cypher` 和 `metrics[].full_cypher` 时调用，一次性校验并缓存结果。
- `validate_generated_query`：Cypher Compiler 生成最终 Cypher 后调用，作为 `Generation Output` 前的最后一道静态校验。

Self-Validation 不执行 `EXPLAIN`、dry-run、probe query 或正式查询，不读取数据库统计信息，也不判断结果是否为空或过大。

## 2. 输入输出契约

输入：

```json
{
  "schema_version": "cypher_self_validation_request_v1",
  "mode": "model_artifact | generated_query",
  "source_kind": "path_pattern | metric_full_cypher | compiled_query",
  "source_name": "tunnel_full_path",
  "cypher": "MATCH (t:Tunnel {id: $tunnel_id})-[p:PATH_THROUGH]->(ne:NetworkElement) RETURN ne, p.hop_order AS hop",
  "ast_summary": {},
  "dsl_projection": [],
  "graph_model_checksum": "sha256:abc123",
  "target_dialect": "tugraph_cypher_v1"
}
```

输出：

```json
{
  "schema_version": "cypher_self_validation_result_v1",
  "valid": true,
  "mode": "model_artifact",
  "checks": [
    {
      "name": "syntax",
      "status": "passed"
    },
    {
      "name": "readonly",
      "status": "passed"
    }
  ],
  "errors": [],
  "warnings": []
}
```

失败输出必须至少包含 `code`、`severity`、`message`、`check` 和可定位的 `location`。

## 3. 校验清单

| 失败码 | 判定规则 | 适用入口 |
| --- | --- | --- |
| `cypher_syntax_invalid` | Cypher 不能被配置的 v1 parser 解析；存在多语句；模板参数替换为类型占位值后仍不能解析 | model artifact、generated query |
| `cypher_readonly_violation` | 出现写子句、schema mutation、过程调用、文件加载或其他非只读能力 | model artifact、generated query |
| `cypher_schema_reference_invalid` | 引用了 graph semantic model 中不存在的 label、edge type、property，或 property 不属于当前绑定的 vertex/edge | generated query；model artifact 尽力校验 |
| `compiler_shape_mismatch` | 最终 `RETURN` 与 DSL projection、query_shape、limit/order 约束不一致 | generated query |
| `target_dialect_static_error` | 使用了 CGA v1 目标 TuGraph Cypher 子集中禁止的语法或函数 | model artifact、generated query |

`model_artifact` 模式没有完整 DSL/AST 时，不执行 `compiler_shape_mismatch`。schema 引用能从模板中静态推断时必须校验，不能推断时记录 warning，但不能跳过 syntax、readonly 和 dialect 校验。

## 4. Syntax Check

v1 的语法解析器是一个可替换 adapter。工程实现可以采用 ANTLR/openCypher grammar，也可以采用保守静态 parser；如果采用保守 parser，它必须只接受 v1 compiler 会生成的 TuGraph-oriented 只读子集，并通过覆盖测试证明所有 v1 query shape 的生成 Cypher 都能被解析。

无论底层 parser 选型如何，必须满足：

- 禁止多语句；单个请求只能包含一条 Cypher。
- `path_pattern.cypher` 和 `metric.full_cypher` 在加载时用声明参数生成占位值后解析。
- 参数占位值按类型替换：`string -> "sample"`、`int -> 1`、`float -> 1.0`、`boolean -> true`、`datetime -> datetime("2026-01-01T00:00:00Z")`。
- parser 只证明语法结构可解析，不证明 TuGraph 运行时可执行。

如果 parser 通过但目标子集不支持某结构，必须由 dialect check 返回 `target_dialect_static_error`，不要混成 syntax error。

当前 v1 代码采用保守 clause parser + schema/dialect/shape 静态检查组合；ANTLR/openCypher adapter 是可替换实现，不是 v1 运行时依赖。

## 5. Readonly Check

v1 采用白名单优先、黑名单兜底。

允许的只读子句：

- `MATCH`
- `WHERE`
- `WITH`
- `RETURN`
- `ORDER BY`
- `LIMIT`
- `SKIP`
- `UNWIND`

禁止的子句和能力：

- `CREATE`
- `MERGE`
- `SET`
- `DELETE`
- `DETACH DELETE`
- `REMOVE`
- `CALL`
- `LOAD CSV`
- `FOREACH`
- `CREATE INDEX`
- `DROP INDEX`
- `CREATE CONSTRAINT`
- `DROP CONSTRAINT`
- 多语句或分号拼接

即使某些 `CALL` 在数据库中是只读过程，CGA v1 仍然全部禁止。允许过程调用需要单独的白名单设计，不能在 v1 中临时放开。

## 6. Schema Reference Check

Self-Validation 必须从 Cypher AST 中建立变量绑定表：

```text
variable -> owner kind -> owner name
n        -> vertex     -> NetworkElement
p        -> edge       -> PATH_THROUGH
```

校验规则：

- node pattern 的 label 必须存在于 `vertices[].name`。
- edge pattern 的 type 必须存在于 `edges[].name`。
- edge pattern 两端 label 若可静态确定，必须与 edge 的 `from` / `to` 匹配；反向匹配必须与 AST 方向一致。
- `var.property` 中的 property 必须存在于变量绑定的 vertex 或 edge 的 `properties[]`。
- map literal 里的属性，例如 `(n:NetworkElement {id: $id})`，也必须属于该 label。
- WHERE 比较、ORDER BY、聚合函数中引用的 property 也必须校验。
- property 类型必须与操作符兼容：数值比较只允许 `int/float`，字符串包含类操作只允许 `string`，布尔比较只允许 `boolean`，时间范围只允许 `date/datetime`。
- 聚合函数类型规则：`count` 允许任意变量或 property；`sum/avg/min/max` 必须匹配允许类型，且 `sum/avg` 只允许数值类型。

无法静态确定 owner 的变量，例如经过复杂 `WITH` 改名后丢失来源，必须返回 `target_dialect_static_error` 或 `compiler_shape_mismatch`，不能默默放行。

## 7. Compiler Shape Check

`generated_query` 模式必须把最终 Cypher 与 DSL AST 做一致性校验：

- `RETURN` alias 必须与 `projection.items[].alias` 集合相等。
- alias 顺序默认必须与 DSL projection 顺序一致；如果 API 明确声明输出为 unordered object，才可只校验集合。
- `query_shape=scalar` 或聚合类查询时，最终返回列数量必须符合 DSL projection。
- `limit` 存在时，最终 Cypher 必须包含不大于 DSL limit 的 `LIMIT`。
- `order_by` 存在时，最终 Cypher 的 `ORDER BY` source 和 direction 必须与 DSL/AST 匹配。
- Cypher 中出现的 label、edge type、property 不能超出 AST 中已声明或 path_pattern/metric 模板显式允许的范围。
- 内部 `WITH` alias 可以存在，但最终 `RETURN` 不能返回 DSL 未声明的列。

违反这些规则返回 `compiler_shape_mismatch`，视为 compiler bug，不进入 LLM repair。

## 8. Target Dialect Static Check

v1 定义的是 CGA 允许生成的 TuGraph Cypher 静态子集，不等同于完整 TuGraph 能力清单。

禁止：

- `OPTIONAL MATCH`
- `UNION` / `UNION ALL`
- `CALL { ... }` 子查询
- procedure call
- `shortestPath` / `allShortestPaths`
- 未注册图算法函数
- pattern comprehension
- list comprehension 中嵌套 pattern
- variable-length path 无上界，例如 `*` 或 `*1..`
- 未在 allowlist 中的函数
- map projection、动态 property key、动态 label/type

函数 allowlist v1：

- `count`
- `sum`
- `avg`
- `min`
- `max`
- `collect`
- `toString`
- `toInteger`
- `toFloat`
- `coalesce`

如果后续确认 TuGraph 支持更多语法，也必须先更新本 spec 和 allowlist，再允许 compiler 生成。

## 9. Model Loader 接入

Graph Model Loader 加载模型时必须调用：

```text
CypherSelfValidator.validate_model_artifact(path_pattern.cypher)
CypherSelfValidator.validate_model_artifact(metric.full_cypher)
```

加载期规则：

- `path_pattern.cypher` 必须通过 syntax、readonly、schema reference 和 dialect 静态校验；失败则拒绝加载整个模型。
- `metric.full_cypher` 必须通过 syntax、readonly、schema reference 和 dialect 静态校验；失败则拒绝加载整个模型。
- 校验结果按 `graph_model_checksum + source_kind + source_name + cypher_hash` 缓存。
- 模型 checksum 变化时缓存失效。
- 加载期校验不依赖运行时 query trace，但必须把失败写入 graph model loader trace。

这意味着 Self-Validation 是可复用服务，不是流水线末端的私有步骤。

## 10. Observability

`cypher_self_validation` stage 必须记录：

- parser 名称和版本。
- target dialect allowlist 版本。
- 每个 check 的 passed/failed/skipped。
- rejected clause 或 function。
- schema reference error 的 owner、property、variable。
- compiler shape mismatch 的 expected/actual projection。
- mode：`model_artifact` 或 `generated_query`。

不得记录数据库连接信息，因为 CGA v1 不持有数据库连接。

## 11. 测试要求

v1 实现至少覆盖：

- `MATCH ... RETURN ...` 正常通过。
- `CREATE`、`MERGE`、`SET`、`DELETE`、`CALL` 被 readonly check 拦截。
- 未知 label、edge type、property 被 schema reference check 拦截。
- property 类型与操作符不兼容被拦截。
- DSL projection 与 RETURN alias 不一致被 `compiler_shape_mismatch` 拦截。
- 无上界 variable-length path 被 dialect check 拦截。
- path_pattern 模板加载期含 `SET` 时拒绝加载模型。
- metric `full_cypher` 多语句时拒绝加载模型。
