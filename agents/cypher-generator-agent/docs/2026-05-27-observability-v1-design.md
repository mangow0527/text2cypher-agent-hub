# Observability v1 设计

> 日期：2026-05-27
> 状态：设计 v1
> 覆盖范围：cypher-generator-agent 的 graph-native 语义生成链路

## 1. 设计目标

这套架构的核心优势是“出错可定位”。Observability v1 让每次查询都产出完整 trace，覆盖从自然语言拆解到候选召回、字面值解析、LLM 绑定、校验、DSL、编译和 Cypher 自校验的全过程。

目标：

- 能回答“为什么生成了这个 Cypher”。
- 能回答“失败发生在哪一层”。
- 能复盘 repair loop 为什么停止。
- 能定位性能瓶颈。
- 能支持 testing-agent 和 runtime console 展示生成证据。

非目标：

- v1 不做分布式链路追踪系统替代品。
- v1 不记录敏感凭据或数据库连接串；CGA v1 不连接数据库，也不保存执行结果集。
- v1 不把 trace 作为业务事实来源。

## 2. Trace 顶层结构

```yaml
trace_schema_version: cga_graph_trace_v1
trace_id: q-20260527-001
question_id: qa-001
generation_run_id: cypher-run-001
source_question: "全网有多少台防火墙？"
started_at: "2026-05-27T10:00:00+08:00"
finished_at: "2026-05-27T10:00:02+08:00"
final_status: generated
semantic_model:
  model_name: network_topology
  spec_version: graph_semantic_model_v1
  checksum: sha256:abc123
stages: []
final_outputs:
  dsl: {}
  cypher: "MATCH ..."
  clarification: null
  user_visible_notices: []   # 由 assumptions 派生的展示字段
  failure: null
```

`final_status` 取值：

- `generated`
- `clarification_required`
- `unsupported_query_shape`
- `generation_failed`
- `service_failed`

## 3. Stage 结构

每个 stage 必须包含：

```yaml
stage: literal_resolver
status: success
started_at: "2026-05-27T10:00:00.300+08:00"
duration_ms: 42
input_ref: inline_or_redacted
output_ref: inline_or_artifact
metrics: {}
errors: []
warnings: []
```

字段规则：

| 字段 | 含义 |
| --- | --- |
| `stage` | 固定枚举，便于查询和统计 |
| `status` | `success`、`warning`、`failed`、`skipped` |
| `duration_ms` | 当前 stage 耗时 |
| `input_ref` | 小输入可 inline，大输入存 artifact 引用 |
| `output_ref` | 小输出可 inline，大输出存 artifact 引用 |
| `metrics` | stage 专用指标 |
| `errors` | 错误码、严重级别、消息 |
| `warnings` | assumption、低置信、覆盖 warning |

## 4. 必须记录的 stages

| stage | 关键内容 |
| --- | --- |
| `graph_model_loader` | model name、checksum、vertex/edge/property/metric/path_pattern 数量、validator 结果、path_pattern/metric Cypher 模板自校验结果 |
| `input_clarification_gate` | 原始问题是否缺少指代对象、Decomposer schema 失败后的澄清决策 |
| `question_decomposer` | 结构化拆解、substantive/stopword/modality/time/unparsed 分类、LLM 调用次数 |
| `candidate_retrieval` | 每类 vertex/edge/property/metric/path_pattern 候选数量、match_type、score、evidence |
| `literal_resolver` | 每个 literal 的解析结果、alternatives、value index/cache 信号 |
| `grounded_understanding` | LLM 结构化输出、schema 校验结果、候选选择 |
| `semantic_binder` | vertex_bindings、edge_bindings、property_bindings、normalized filters、path bindings |
| `semantic_validator` | errors、warnings、coverage report |
| `repair_controller` | decision、attempt_no、fingerprint、oscillation、clarification |
| `dsl_builder` | query_shape、DSL 输出、unsupported reason |
| `dsl_parser` | JSON Schema 校验、AST 规范化 |
| `cypher_compiler` | compiler template、输出 Cypher、target dialect |
| `cypher_self_validation` | parser 结果、read-only check、schema-aware check、DSL/AST shape check、target dialect static check |

## 5. Stage 示例

```yaml
- stage: literal_resolver
  status: success
  duration_ms: 38
  metrics:
    items_total: 2
    items_resolved: 2
    cache_hits: 1
  output_ref:
    type: inline
    value:
      results:
        - raw_literal: 防火墙
          expected_vertex: NetworkElement
          expected_property: elem_type
          resolved_value: firewall
          match_type: value_synonym
          confidence: 0.98
          alternatives: []
        - raw_literal: tun-mpls-001
          expected_vertex: Tunnel
          expected_property: id
          resolved_value: tun-mpls-001
          match_type: value_index_exact
          confidence: 1.0
          alternatives: []
  errors: []
  warnings: []
```

Repair stage 示例：

```yaml
- stage: repair_controller
  status: warning
  duration_ms: 5
  metrics:
    attempt_no: 2
    max_attempts: 3
    historical_fingerprints: 2
  output_ref:
    type: inline
    value:
      decision: ask_user
      reason_code: ambiguous_vertex_binding
      fingerprint: sha256:def456
      oscillation_detected: false
      clarification:
        expected_answer_type: single_choice
        options_count: 2
      assumptions: []
  errors: []
  warnings:
    - code: repair_limit_near
      message: "Repair attempt 2 of 3"
```

## 6. 覆盖报告

Semantic Validator 必须写入 coverage report：

```yaml
coverage:
  substantive_terms:
    total: 4
    covered: 4
    uncovered: []
  stopword_terms:
    ignored: ["麻烦", "帮我", "查一下"]
  modality_terms:
    warning_only: ["应该"]
  time_terms:
    covered: ["最近"]
    unresolved: []
  unparsed_terms:
    unresolved: []
```

规则：

- `substantive_terms.uncovered` 非空时不得生成 Cypher。
- `stopword_terms` 只记录，不触发失败。
- `modality_terms` 可以 warning-only，但必须出现在 trace 中。
- `unparsed_terms.unresolved` 非空时必须进入 clarification 或 generation_failed。`unparsed_terms` 只允许保存仍有潜在语义影响、且不能归入 substantive/stopword/modality/time 的残留词。

## 7. 指标与告警

核心指标：

| metric | 计数粒度 | 说明 |
| --- | --- | --- |
| `cga_graph_generation_success_count` | 每次生成 run | `final_status=generated` 时 +1 |
| `cga_graph_clarification_required_count` | 每次生成 run | 任一阶段返回用户澄清时 +1 |
| `cga_graph_unsupported_query_shape_count` | 每次生成 run | 返回 `unsupported_query_shape` 时 +1 |
| `cga_graph_generation_failed_count` | 每次生成 run | 返回 `generation_failed` 时 +1 |
| `cga_graph_stage_duration_ms` | 每个 stage | 记录 stage 耗时分布 |
| `cga_graph_llm_call_count` | 每次 LLM 调用 | 包含重试和 repair loop 调用 |
| `cga_graph_repair_attempt_count` | 每次 repair attempt | 每进入一次 `repair_with_llm` +1 |
| `cga_graph_repair_oscillation_count` | 每次生成 run | 检测到震荡时 +1 |
| `cga_graph_literal_cache_hit_rate` | 按 property/window 聚合 | 命中次数 / lookup 总次数 |
| `cga_graph_coverage_failure_count` | 每个 uncovered term | 每个未覆盖 substantive/time/unparsed term +1 |
| `cga_graph_input_clarification_required_count` | 每次生成 run | Input Clarification Gate 返回澄清时 +1 |
| `cga_graph_assumption_notice_count` | 每个 assumption | 每个可渲染为用户 notice 的 assumption +1 |
| `cga_graph_query_with_assumption_count` | 每次生成 run | 本次 run 存在至少一个 assumption 时 +1 |
| `cga_graph_compiler_shape_mismatch_count` | 每次生成 run | self-validation 发现 shape mismatch 时 +1 |
| `cga_graph_cypher_self_validation_failure_count` | 每个 failed check | syntax/readonly/schema/shape/dialect 任一 check 失败时 +1 |

严重告警：

- compiler shape mismatch。
- DSL Parser 通过但 compiler 输出无法通过 read-only/syntax 校验。
- graph semantic registry 引用不存在。
- graph model validator 失败。
- repair oscillation 频繁发生。
- literal cache stale suspected 突增。
- coverage failure 在某类问题上持续上升。

## 8. 隐私与截断

trace 可进入 testing-agent 和 runtime console，因此需要截断策略：

- 不记录数据库凭据；CGA 不应持有数据库连接配置。
- 不记录执行结果集、行数 sample 或 runtime error，因为 CGA 不执行 Cypher。
- LLM prompt 可记录，但候选列表超过阈值时用 artifact 引用。
- 用户问题原文保留，因为它是生成审计必要上下文。
- Cypher 保留，因为它是评测与复盘必要输出。

## 9. 与 testing-agent 的契约

生成成功时，`input_prompt_snapshot` 使用 `cga_graph_trace_v1` 的 JSON string。

非成功输出也必须提交 trace：

- `clarification_required`：保留 clarification、coverage、候选歧义证据。
- `clarification_required` 也覆盖 input clarification，例如问题缺少指代对象或 Decomposer 无法产出有效结构。
- `unsupported_query_shape`：保留 unsupported reason 和 suggested rewrites。
- `generation_failed`：保留 validator/compiler/self-validation 错误和最后可用 DSL/Cypher。
- `service_failed`：保留工程异常，但不构造正式评测 attempt。

testing-agent 不重新解释 trace，只负责保存、关联 attempt，并展示给 runtime console 或 repair-agent。

## 10. 查询排障视图

runtime console 至少应能展示：

- 总耗时和每个 stage 耗时。
- LLM 调用次数和 schema violation 次数。
- 候选召回 top results。
- LiteralResolver 的 resolved/unresolved/alternatives。
- 覆盖报告。
- repair loop 历史、fingerprint 和停止原因。
- 用户可见 assumption notices。
- DSL、AST 摘要、Cypher。
- Cypher 自校验摘要和非成功输出原因。

这样用户或开发者可以回答：

- 是问题拆解错了，还是语义召回错了？
- 是字面值不存在，还是值索引陈旧？
- 是 DSL 不支持，还是 compiler bug？
- 是自校验失败，还是已经生成可交付 Cypher？
