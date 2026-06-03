# Repair and Clarification Controller v1 设计

> 日期：2026-05-27
> 状态：设计 v1
> 上游：Semantic Validator、Cypher Self-Validation
> 下游：Grounded LLM Understanding、用户澄清、失败输出

## 1. 设计目标

Repair and Clarification Controller 决定当语义理解、绑定、语义校验或 Cypher 自校验出现问题时，系统是静默回灌 LLM 修复、反问用户、返回不支持，还是终止生成。

Question Decomposer 之前或之内的输入不清晰问题不由本 Controller 处理。该类问题由总体架构中的 Input Clarification Gate 负责：

- 问题缺少指代对象或没有可解析 substantive term 时，直接返回 `clarification_required`。
- Question Decomposer 连续 schema violation 且输入本身含糊时，由 Input Clarification Gate 构造澄清问题。
- 输入正常但 Decomposer LLM 连续输出非法结构时，返回 `generation_failed`，reason 为 `question_decomposer_schema_invalid`。

目标：

- 用户不应承担 LLM 可自行修复的 vertex/edge/property 类型错误。
- 用户必须看到会改变语义的问题，例如歧义、覆盖缺失、字面值无法解析。
- repair loop 有最大轮次和震荡检测，避免在两个错误状态之间来回切换。
- DSL 不支持时不绕过 DSL 安全边界。

## 2. 输入输出契约

输入：

```json
{
  "schema_version": "repair_controller_input_v1",
  "trace_id": "q-20260527-001",
  "question": "Gold 级别的服务都用了哪些 MPLS-TE 隧道",
  "attempt_no": 1,
  "selected_bindings": {},
  "validator_errors": [
    {
      "code": "edge_endpoint_mismatch",
      "severity": "error",
      "repairable": true,
      "message": "SERVICE_USES_TUNNEL cannot connect Service to NetworkElement"
    }
  ],
  "history": []
}
```

输出：

```json
{
  "schema_version": "repair_controller_decision_v1",
  "decision": "repair_with_llm | ask_user | unsupported | generation_failed | continue_with_assumption",
  "reason_code": "edge_endpoint_mismatch",
  "repair_prompt_delta": {},
  "clarification": null,
  "assumptions": [],
  "stop_reason": null
}
```

## 3. 决策矩阵

| 问题 | 默认决策 | 说明 |
| --- | --- | --- |
| edge 端点类型错误 | `repair_with_llm` | 给 LLM 错误和合法候选，让其重选 |
| edge 方向错误 | `repair_with_llm` | 编译器不猜方向 |
| metric/property 误用 | `repair_with_llm` | 用户通常不知道内部类型 |
| fuzzy 高置信首选 | `continue_with_assumption` | trace 记录 assumption，并必须返回用户可见 notice |
| 多候选接近 | `ask_user` | 最多给 3 个候选 |
| substantive term 未覆盖 | `ask_user` 或 `generation_failed` | 不允许静默丢词 |
| time term 未解析 | `ask_user` | 相对时间需要明确范围 |
| modality term 未落地 | `continue_with_assumption` | 作为 warning-only |
| 字面值 unresolved | `ask_user` | 使用 LiteralResolver alternatives |
| DSL 不支持 | `unsupported` | 给可改写建议；不走 raw Cypher |
| 编译后 shape mismatch | `generation_failed` | 视为 compiler bug，严重告警 |
| 目标方言静态校验失败 | `generation_failed` | 表示 compiler 输出不符合允许的 TuGraph Cypher 子集 |

## 4. Repair Loop

配置：

```yaml
repair_controller:
  max_repair_attempts: 3
  stop_if_same_error_repeats: true
  stop_if_binding_oscillates: true
```

循环流程：

1. Semantic Validator 返回错误列表。
2. Controller 按最高 severity 和产品策略选择 repair 或 clarification。
3. 若 decision 为 `repair_with_llm`，构造最小 repair prompt：
   - 原始问题。
   - 当前结构化理解。
   - 校验错误。
   - 可选合法候选。
   - 明确禁止发明候选。
4. LLM 重新输出结构化理解。
5. Semantic Binder 和 Validator 重新运行。
6. 若通过，继续 DSL。
7. 若失败，记录历史状态和错误原因。
8. 达到上限、重复错误或状态震荡时停止，转为 ask_user、unsupported 或 generation_failed。

## 5. 状态指纹与震荡检测

每一轮 repair 后记录 canonical state fingerprint。

状态指纹有两个来源：

- repair loop 中优先使用 Semantic Binding 指纹，因为此时 DSL 可能尚未生成。
- DSL Builder 之后使用 Normalized DSL 指纹，作为 compiler 和 Cypher self-validation 的状态依据。

两种来源必须归一到同一个 canonical state schema：

```json
{
  "query_shape": "two_step_aggregate",
  "vertices": [],
  "edges": [],
  "properties": [],
  "filters": [],
  "path_patterns": [],
  "projections": [],
  "groups": [],
  "metrics": [],
  "measures": [],
  "sorts": [],
  "limits": [],
  "subqueries": []
}
```

### 5.1 指纹字段与 DSL/绑定的映射

| canonical 字段 | Semantic Binding 来源 | Restricted DSL 来源 |
| --- | --- | --- |
| `query_shape` | `binding.query_shape` | `query_shape` |
| `vertices[]` | `selected_bindings[*].vertex_name/role` | `bindings.*.vertex_name`，key 作为 role |
| `edges[]` | `selected_bindings[*].edge_name/role/direction` | `bindings.*.edge_name`、`operations[op=traverse_edge].edge/direction`、`operations[op=variable_path].allowed_edges` |
| `properties[]` | `property_bindings[*].owner/name/role` | `filters[].property`、`projection.items[].property`、`operations[].group_by[].property`、`operations[].measures[].property` |
| `filters[]` | `filters[*].target/property/operator/value.normalized` | 顶层 `filters[]`、`operations[].filters[]`、`operations[].through.filters[]`、`filter_subquery.predicate` |
| `path_patterns[]` | `selected_path_pattern.name` | `operations[op=use_path_pattern].path_pattern_name` |
| `projections[]` | `projection_items[*].source/alias/target/property` | `projection.items[].property` 或 `projection.items[].source`；同时保留 alias 和 target role |
| `groups[]` | `group_by[*].target/property/alias` | `operations[op=aggregate].group_by[]`、`operations[op=metric_aggregate].group_by[]`、`operations[op=subquery].group_by[]` |
| `metrics[]` | `metrics[*].name` | `operations[op=metric_aggregate].metric_name` |
| `measures[]` | `measures[*].alias/function/target/property/metric_name` | `operations[op=aggregate].measures[]` 和 `operations[op=subquery].measures[]` |
| `sorts[]` | `sorts[*].source/direction` | `operations[op=sort].by[]` 和顶层 `order_by[]` |
| `limits[]` | `limit.value` | `operations[op=limit].value` 和顶层 `limit` |
| `subqueries[]` | `subqueries[*]` | `operations[op=subquery]` 的递归指纹 |

### 5.2 两步聚合与子查询指纹

`two_step_aggregate` 必须递归生成子查询指纹：

顶层字段只描述外层查询状态；子查询内部的 vertex、edge、property、group、measure 必须进入对应 `subqueries[].fingerprint_payload`。如果外层只消费子查询输出，顶层 `vertices`、`edges`、`properties` 可以为空，不能把子查询内部对象重复摊平到顶层。

```json
{
  "query_shape": "two_step_aggregate",
  "vertices": [],
  "edges": [],
  "properties": [],
  "path_patterns": [],
  "projections": [
    {
      "alias": "device",
      "source": "device_port_counts.device"
    },
    {
      "alias": "port_count",
      "source": "device_port_counts.port_count"
    }
  ],
  "groups": [],
  "metrics": [],
  "measures": [],
  "subqueries": [
    {
      "bind_as": "device_port_counts",
      "fingerprint_payload": {
        "query_shape": "ad_hoc_aggregate",
        "vertices": [
          {
            "role": "device",
            "name": "NetworkElement"
          },
          {
            "role": "port",
            "name": "Port"
          }
        ],
        "edges": [
          {
            "role": "device_ports",
            "name": "HAS_PORT",
            "direction": "forward"
          }
        ],
        "properties": [
          {
            "owner": "NetworkElement",
            "name": "id",
            "role": "group"
          },
          {
            "owner": "Port",
            "name": "id",
            "role": "measure"
          }
        ],
        "filters": [],
        "path_patterns": [],
        "projections": [],
        "groups": [
          {
            "target": "device",
            "property": "NetworkElement.id"
          }
        ],
        "measures": [
          {
            "alias": "port_count",
            "function": "count",
            "target": "port",
            "property": "Port.id"
          }
        ],
        "sorts": [],
        "limits": [],
        "subqueries": []
      }
    }
  ],
  "filters": [
    {
      "target": "device_port_counts",
      "property": "port_count",
      "operator": "gt",
      "value": 10
    }
  ],
  "sorts": [
    {
      "source": "device_port_counts.port_count",
      "direction": "desc"
    }
  ],
  "limits": [
    {
      "value": 5
    }
  ]
}
```

子查询指纹规则：

- 子查询 `bind_as` 进入父指纹。
- 子查询内部按同一 canonical state schema 递归规范化。
- 子查询内 confidence、reason、原始 LLM 文本不进入指纹。
- 两个子查询如果 `bind_as` 不同但语义引用相同，v1 判定为不同状态，避免错误合并。

不进入状态指纹的字段：

- confidence 数值。
- reason 文本。
- LLM 原始输出文本。
- candidate 返回顺序。
- duration、token usage。
- trace stage id。

canonicalization：

- 对对象 key 做稳定排序。
- 对集合类绑定按 `(kind, name, role)` 排序。
- 对 filter 按 `(target, property, operator, value)` 排序。
- 删除空字段和 null 字段。
- 生成 sorted JSON 后取 sha256。

震荡定义：

- 新 fingerprint 与历史任一轮 fingerprint 相同，即判定 oscillation。
- v1 使用集合相等的简单判定，不做复杂语义等价。
- confidence 变化不会解除震荡判定。

处理：

- 若 oscillation 出现在 repairable 类型错误上，停止 repair，返回 `generation_failed`，reason 为 `repair_binding_oscillation`。
- 若 oscillation 涉及歧义候选，在还有 alternatives 时改为 `ask_user`。

## 6. Continue With Assumption 的用户可见性

`continue_with_assumption` 不能只写 trace。Controller 的源输出只包含结构化 `assumptions`；用户可见 notice 必须由 API 响应层或 runtime console 根据 assumption 模板确定性渲染。`user_visible_notices` 是派生字段，不是 Controller 可自由填写的独立事实来源。

```json
{
  "decision": "continue_with_assumption",
  "reason_code": "high_confidence_fuzzy_literal",
  "assumptions": [
    {
      "kind": "literal_binding",
      "raw": "防火墙",
      "assumed_as": "firewall",
      "confidence": 0.87,
      "property": "NetworkElement.elem_type"
    }
  ]
}
```

派生 notice 示例：

```json
{
  "derived_user_visible_notices": [
    "我把“防火墙”理解为设备类型 firewall。"
  ]
}
```

规则：

- 每个会影响查询语义的 assumption 必须能渲染出用户可见 notice。
- notice 由固定模板基于结构化 assumption 生成，不由自由文本 LLM 生成。
- `assumptions[]` 是机器可读源数据；`user_visible_notices[]` 是最终响应中的派生展示字段。
- warning-only modality 也应生成 notice，例如“问题中的‘应该’没有被解释为查询约束”。
- notice 不阻塞查询，但必须随结果返回。

## 7. Clarification 输出

澄清问题必须短、可回答、带选项：

```json
{
  "source_stage": "semantic_validator",
  "reason_code": "ambiguous_vertex_binding",
  "question_zh": "你说的“端口”是指设备端口 Port，还是服务暴露端口 ServicePort？",
  "expected_answer_type": "single_choice",
  "options": [
    {
      "id": "Port",
      "label": "设备端口",
      "vertex_name": "Port",
      "confidence": 0.77
    },
    {
      "id": "ServicePort",
      "label": "服务端口",
      "vertex_name": "ServicePort",
      "confidence": 0.74
    }
  ]
}
```

规则：

- 单轮最多问一个关键问题。
- 选项最多 3 个。
- 不向用户暴露内部错误栈。
- 对覆盖缺失必须说明哪个词没有被使用，例如“问题中的‘增长’没有在当前语义模型中找到对应 metric”。

## 8. DSL 不支持的降级策略

采用 A + C：

- A：直接返回 `unsupported_query_shape`，说明系统 v1 不支持该查询形态。
- C：如果能拆成多个支持查询，给用户改写建议。

不采用 B：

- 不允许 fallback 到 LLM 直接生成 Cypher。
- 不允许低信任 raw Cypher 路径。
- 不允许在 DSL 中加入 escape hatch。

## 9. Cypher 自校验接入

cypher-generator-agent 不连接数据库，因此 Controller 只接收生成链路内部的自校验错误。

具体判定规则以 [Cypher Self-Validation v1](./2026-05-27-cypher-self-validation-v1-design.md) 为准；本节只定义 Controller 如何消费这些错误。

| 自校验反馈 | 决策 |
| --- | --- |
| Cypher 语法解析失败 | `generation_failed`，reason 为 `cypher_syntax_invalid` |
| 出现写操作或非只读子句 | `generation_failed`，reason 为 `cypher_readonly_violation` |
| Cypher 引用未绑定 label、edge type 或 property | `generation_failed`，reason 为 `cypher_schema_reference_invalid` |
| 返回列与 DSL projection 不一致 | `generation_failed`，reason 为 `compiler_shape_mismatch` |
| 目标 TuGraph 方言静态规则不支持 | `generation_failed`，reason 为 `target_dialect_static_error` |

不属于 CGA v1 的反馈：

- 空结果。
- 结果过大。
- 查询超时。
- TuGraph runtime error。
- golden answer 对比。

这些反馈属于 testing-agent、runtime service 或其他下游执行服务。下游可以把失败报告提交给 repair-agent，但不应要求 cypher-generator-agent 在生成阶段连接数据库。

## 10. 配置

```yaml
repair_controller:
  max_repair_attempts: 3
  ambiguous_top2_gap_threshold: 0.10
  max_clarification_options: 3
```

CGA v1 不配置结果行数阈值和执行 timeout，因为它不执行 Cypher。

## 11. 测试要求

v1 实现时至少覆盖：

- edge 端点错误 repair 成功。
- repair 3 次仍失败后终止。
- A -> B -> A 绑定震荡被 fingerprint 捕获。
- 多候选接近时反问用户。
- substantive term 未覆盖时不生成 DSL。
- DSL 不支持时不出现 raw Cypher fallback。
- compiler shape mismatch 标为严重失败，不自动重试。
- two_step_aggregate 的子查询递归指纹能区分 measure、filter_subquery、sort、limit 的实质变化。
- continue_with_assumption 的 assumptions 必须能派生用户可见 notice。
