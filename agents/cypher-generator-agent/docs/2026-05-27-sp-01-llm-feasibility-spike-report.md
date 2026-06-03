# SP-01 LLM Feasibility Spike Report

> 日期：2026-05-27
> 状态：真实 provider smoke 已执行；结论调整为 LLM 只填 surface slots，grounding 由工程化规则完成
> 适用分支：`cypher-generation-osi`
> 范围：Question Decomposer 与 Grounded Understanding prompt 可行性

> 2026-06-02 维护说明：本文是历史 spike 报告，不是当前 `question_decomposition_v1` 或 `grounded_understanding_v1` 的权威 schema。文中 `target_concepts`、`relation_phrases`、独立 `slot_terms` 等草案字段已被当前实现替换为 `substantive_terms[].slot` 等结构。当前权威状态见 [IR Closeout Status](./2026-05-28-ir-closeout-status.md) 与 [CGA OSI Follow-up Modification IR](./2026-05-28-cga-osi-follow-up-modification-ir.md)。

## 1. 结论摘要

本报告没有实际调用外部 LLM provider，没有真实 token usage、latency 或模型原始输出证据。本文档是本地离线 spike report，用 fixture、设计文档和手工样例输出提前验证 prompt/schema/candidate-boundary 的可行性。真实 provider run 必须在有 provider key 后补跑，不能把本文中的样例输出当作模型实测结果。

初步结论：

- 允许进入 IR-13 Question Decomposer 和 IR-14 Grounded LLM Understanding 的实现准备，但必须把 provider run 作为 IR-13/IR-14 合入前置检查。
- Question Decomposer 的 v1 prompt 草案可以进入真实 provider 验证，关键风险是把“增长”“最短路径”等 unsupported 语义误分类为普通 unparsed 或丢弃。
- Grounded Understanding 的 v1 prompt 草案可以进入真实 provider 验证，前提是 schema 强制所有绑定引用 candidate id，且 semantic validator 拒绝任何候选外 registry name。
- 必须保留降级策略：schema retry、provider unavailable -> `service_failed`、candidate invention -> stage failed、coverage gap -> `unsupported_query_shape` 或 clarification、DSL unsupported -> 不回退 raw Cypher。

## 2. Provider 候选

v1 provider 候选：

| 候选 | 用途 | 选择理由 | 风险 |
| --- | --- | --- | --- |
| OpenAI structured outputs | IR-13/IR-14 首选候选 | 支持结构化输出和 schema 约束，适合 Question Decomposer 与候选内选择 | 真实 schema adherence、中文术语分类和 token usage 未在本地验证 |
| Anthropic tool/schema output | 备选候选 | 长上下文和指令遵循强，可作为 provider abstraction 备份 | 需要验证 JSON/schema 失败率和 retry 行为 |
| 自建模型或内网模型 | 合规或私有化备选 | 可满足内网部署要求 | 候选发明率、中文分类稳定性和结构化输出能力风险最高 |

本 spike 的 provider 选择建议：IR-13/14 先实现 provider protocol 与 fake client，真实运行优先补 OpenAI structured outputs；Anthropic 或自建模型作为同一评测脚本的第二 provider，不阻塞确定性底座。

## 3. Fixture 与样本

输入来自：

- `services/cypher_generator_agent/tests/fixtures/golden_questions.yaml`
- `services/cypher_generator_agent/tests/fixtures/network_topology_graph_model.yaml`
- `services/cypher_generator_agent/tests/fixtures/value_index.json`

本次离线样本：

| id | question | expected_status | query_shape | 选择理由 |
| --- | --- | --- | --- | --- |
| gq-001 | Gold 服务使用了哪些隧道 | generated | single_hop_traversal | 服务到隧道单跳 happy path |
| gq-003 | 隧道 tun-mpls-001 经过哪些设备 | generated | named_path_pattern | 命名 path_pattern 与 ID literal |
| gq-008 | 全网有多少台防火墙 | generated | metric_aggregate | metric + enum filter |
| gq-019 | 收入增长情况怎么样 | unsupported_query_shape | unsupported | 未建模业务域和未知 metric |
| gq-020 | 计算 ne-0001 到 ne-0004 的最短路径 | unsupported_query_shape | unsupported | 图算法明确不在 v1 DSL 范围 |

说明：当前 fixture 中“收入增长情况怎么样”是 `gq-019`；`gq-012` 是“找出所有经过设备 ne-0001 的隧道”。

## 4. Question Decomposer Prompt 草案

系统提示摘要：

```text
You are the Question Decomposer for a graph-native Cypher generation pipeline.
Return only JSON matching schema_version question_decomposition_v1.
Do not select graph schema objects. Do not output Cypher.
Classify Chinese query terms into substantive_terms, stopword_terms, time_terms,
modality_terms, and unparsed_terms.
Substantive terms include graph concepts, relationships, properties, metrics,
filter values, aggregation, ordering, limits, path or algorithm intent, and unsupported
business metrics. Do not drop unsupported terms.
If a term may change query semantics, keep it in substantive_terms or unparsed_terms.
Polite words and filler go to stopword_terms.
Relative time goes to time_terms. Uncertainty or expectation words go to modality_terms.
```

User prompt template 摘要：

```text
Question:
{source_question}

Return question_decomposition_v1 JSON with:
- intent_type
- target_concepts
- relation_phrases
- literal_candidates
- filter_phrases
- time_terms
- modality_terms
- substantive_terms
- stopword_terms
- unparsed_terms
- output_shape
```

Structured output schema 摘要：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `schema_version` | string | 固定 `question_decomposition_v1` |
| `intent_type` | enum | `lookup/list/count/aggregate/top_n/path/compare/unknown` |
| `target_concepts` | string[] | 领域无关自然语言概念，不放 registry name |
| `relation_phrases` | string[] | 用户表达中的关系动词或路径词 |
| `literal_candidates` | object[] | `text/kind_hint/attached_to` |
| `filter_phrases` | string[] | 过滤表达原文 |
| `time_terms` | string[] | 时间点、时间范围、相对时间 |
| `modality_terms` | string[] | 大概、应该、可能等 |
| `substantive_terms` | string[] | 改变查询语义的词 |
| `stopword_terms` | string[] | 礼貌词、口语引导 |
| `unparsed_terms` | string[] | 仍可能影响语义但未能归类的词 |
| `output_shape` | enum | `rows/scalar/grouped_rows/path/unknown` |

Schema validation expectation：

- JSON object only，不接受 Markdown 或解释文本。
- 必填字段缺失、枚举非法、数组元素类型错误均失败。
- `schema_version` 不匹配失败。
- `substantive_terms` 为空但问题含业务名词时失败或进入 retry。

## 5. Grounded Understanding Prompt 草案

系统提示摘要：

```text
You are Grounded Understanding for a graph-native Cypher pipeline.
Use only the provided candidates and literal resolver results.
Return only grounded_understanding_v1 JSON.
Never invent vertex, edge, property, metric, path_pattern, value, or query_shape.
Every selected object must reference a candidate_id from the input.
If no candidate can cover a substantive term, mark coverage_gap.
If the user asks for unsupported graph algorithms, raw Cypher features, writes,
or unknown business metrics, return unsupported with the correct uncovered terms.
Do not generate Cypher. Do not choose a lower-ranked candidate to force success.
```

User prompt template 摘要：

```text
Question decomposition:
{question_decomposition_v1}

Candidates:
{candidate_list_grouped_by vertex/edge/property/metric/path_pattern}

Literal resolver results:
{literal_resolver_results}

Return grounded_understanding_v1 JSON with selected_bindings, coverage, ambiguity,
unsupported, and confidence.
```

Structured output schema 摘要：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `schema_version` | string | 固定 `grounded_understanding_v1` |
| `status` | enum | `grounded/clarification_required/unsupported_query_shape/failed` |
| `query_shape` | enum | 必须是 DSL v1 支持形态或 `unsupported` |
| `selected_bindings` | object | vertex/edge/property/metric/path_pattern/filter 绑定 |
| `selected_bindings.*.candidate_id` | string | 必须存在于输入候选集合 |
| `literal_bindings` | object[] | 必须引用 literal resolver result 或标记 unresolved |
| `coverage.substantive_terms.covered` | string[] | 已覆盖实质词 |
| `coverage.substantive_terms.uncovered` | string[] | 非空时不得 `grounded` |
| `ambiguity` | object[] | 多候选接近时列出最多 3 个 candidate id |
| `unsupported.reason_code` | string/null | unsupported 时必填 |
| `confidence` | number | 0-1，仅作 trace，不作为绕过校验依据 |

Candidate-boundary 规则：

1. 所有 graph object selection 必须引用候选中的 `candidate_id`，不能只写 name。
2. 候选 id 与 registry name 双重校验：candidate id 存在，且 candidate payload 的 name 存在于 Graph Semantic Registry。
3. 输出引用不存在 candidate id 时，计为 candidate invention，stage failed，不进入 binder。
4. 输出 registry name 存在但未在候选集合出现时，仍计为 candidate invention。
5. literal value 必须来自 LiteralResolver 的 resolved result；未解析 ID 或枚举不能由 LLM 自行补全。
6. 多个候选分数接近时必须输出 ambiguity，不允许强行选择。
7. unsupported query shape 不能降级为近似查询；例如 shortest path 不能改写成普通 `PATH_THROUGH` 遍历。

## 6. 离线问题逐项结果

以下“样例模型输出”是手工编排的预期输出，不是 provider 实测输出。

### gq-001 Gold 服务使用了哪些隧道

原始 prompt 摘要：

- Decomposer 输入原问题，要求识别服务、隧道、使用、Gold、输出 rows。
- Grounded 输入包含候选：`Service`、`Tunnel`、`SERVICE_USES_TUNNEL`、`Service.quality_of_service`，literal resolver 将 `Gold` 解析为 `GOLD`。

预期/样例 Question Decomposer 输出：

```json
{
  "schema_version": "question_decomposition_v1",
  "intent_type": "list",
  "target_concepts": ["服务", "隧道"],
  "relation_phrases": ["使用"],
  "literal_candidates": [{"text": "Gold", "kind_hint": "enum_or_name", "attached_to": "服务"}],
  "filter_phrases": ["Gold 服务"],
  "time_terms": [],
  "modality_terms": [],
  "substantive_terms": ["Gold", "服务", "使用", "隧道"],
  "stopword_terms": [],
  "unparsed_terms": [],
  "output_shape": "rows"
}
```

预期/样例 Grounded Understanding 输出：

```json
{
  "schema_version": "grounded_understanding_v1",
  "status": "grounded",
  "query_shape": "single_hop_traversal",
  "selected_bindings": {
    "start_vertex": {"candidate_id": "v_service", "name": "Service"},
    "edge": {"candidate_id": "e_service_uses_tunnel", "name": "SERVICE_USES_TUNNEL"},
    "end_vertex": {"candidate_id": "v_tunnel", "name": "Tunnel"},
    "filter_property": {"candidate_id": "p_service_quality_of_service", "owner": "Service", "name": "quality_of_service"}
  },
  "literal_bindings": [{"raw": "Gold", "normalized": "GOLD", "resolver_match_type": "value_synonym"}],
  "coverage": {
    "substantive_terms": {"covered": ["Gold", "服务", "使用", "隧道"], "uncovered": []}
  },
  "ambiguity": [],
  "unsupported": null,
  "confidence": 0.93
}
```

Schema 校验预期：通过。后续 semantic validator 应确认 `SERVICE_USES_TUNNEL` 方向为 `Service -> Tunnel`，filter property 属于 `Service`。

风险点：

- LLM 可能把 Gold 当 `Service.id` 而不是 `quality_of_service`。
- “服务使用隧道”必须绑定 `SERVICE_USES_TUNNEL`，不能发明 `USES` 或 `SERVICE_TO_TUNNEL`。

### gq-003 隧道 tun-mpls-001 经过哪些设备

原始 prompt 摘要：

- Decomposer 输入原问题，要求识别隧道、设备、经过、literal `tun-mpls-001`、输出 path/rows。
- Grounded 输入包含候选：`Tunnel`、`NetworkElement`、`PATH_THROUGH`、`tunnel_full_path`，literal resolver 精确命中 `Tunnel.id=tun-mpls-001`。

预期/样例 Question Decomposer 输出：

```json
{
  "schema_version": "question_decomposition_v1",
  "intent_type": "path",
  "target_concepts": ["隧道", "设备"],
  "relation_phrases": ["经过"],
  "literal_candidates": [{"text": "tun-mpls-001", "kind_hint": "id", "attached_to": "隧道"}],
  "filter_phrases": ["隧道 tun-mpls-001"],
  "time_terms": [],
  "modality_terms": [],
  "substantive_terms": ["隧道", "tun-mpls-001", "经过", "设备"],
  "stopword_terms": [],
  "unparsed_terms": [],
  "output_shape": "path"
}
```

预期/样例 Grounded Understanding 输出：

```json
{
  "schema_version": "grounded_understanding_v1",
  "status": "grounded",
  "query_shape": "named_path_pattern",
  "selected_bindings": {
    "primary_vertex": {"candidate_id": "v_tunnel", "name": "Tunnel"},
    "path_pattern": {"candidate_id": "pp_tunnel_full_path", "name": "tunnel_full_path"},
    "path_edge": {"candidate_id": "e_path_through", "name": "PATH_THROUGH"},
    "path_vertex": {"candidate_id": "v_network_element", "name": "NetworkElement"}
  },
  "literal_bindings": [{"raw": "tun-mpls-001", "normalized": "tun-mpls-001", "resolver_match_type": "value_index_exact"}],
  "coverage": {
    "substantive_terms": {"covered": ["隧道", "tun-mpls-001", "经过", "设备"], "uncovered": []}
  },
  "ambiguity": [],
  "unsupported": null,
  "confidence": 0.95
}
```

Schema 校验预期：通过。semantic validator 应偏好 `tunnel_full_path`，并确认不能用 `TUNNEL_SRC`/`TUNNEL_DST` 推断完整路径。

风险点：

- LLM 可能只选 `PATH_THROUGH` 而漏掉 `tunnel_full_path`；这仍可由 binder/DSL 处理，但 prompt 应优先命名 path pattern。
- LLM 可能误用 `TUNNEL_SRC` 或 `TUNNEL_DST`，必须由 anti_patterns 和 semantic validator 拦截。

### gq-008 全网有多少台防火墙

原始 prompt 摘要：

- Decomposer 输入原问题，要求识别 count intent、设备/防火墙过滤、全网范围。
- Grounded 输入包含候选：`NetworkElement`、`NetworkElement.elem_type`、metric `device_count`，literal resolver 将 `防火墙` 解析为 `firewall`。

预期/样例 Question Decomposer 输出：

```json
{
  "schema_version": "question_decomposition_v1",
  "intent_type": "count",
  "target_concepts": ["防火墙"],
  "relation_phrases": [],
  "literal_candidates": [{"text": "防火墙", "kind_hint": "enum_or_name", "attached_to": "设备类型"}],
  "filter_phrases": ["防火墙"],
  "time_terms": [],
  "modality_terms": [],
  "substantive_terms": ["全网", "多少", "防火墙"],
  "stopword_terms": [],
  "unparsed_terms": [],
  "output_shape": "scalar"
}
```

预期/样例 Grounded Understanding 输出：

```json
{
  "schema_version": "grounded_understanding_v1",
  "status": "grounded",
  "query_shape": "metric_aggregate",
  "selected_bindings": {
    "metric": {"candidate_id": "m_device_count", "name": "device_count"},
    "vertex": {"candidate_id": "v_network_element", "name": "NetworkElement"},
    "filter_property": {"candidate_id": "p_network_element_elem_type", "owner": "NetworkElement", "name": "elem_type"}
  },
  "literal_bindings": [{"raw": "防火墙", "normalized": "firewall", "resolver_match_type": "value_synonym"}],
  "coverage": {
    "substantive_terms": {"covered": ["全网", "多少", "防火墙"], "uncovered": []}
  },
  "ambiguity": [],
  "unsupported": null,
  "confidence": 0.9
}
```

Schema 校验预期：通过。semantic validator 应确认 `device_count` 支持 dimension/filter `ne.elem_type`。

风险点：

- “全网”应被理解为无额外 location 过滤，不应成为 unresolved literal。
- LLM 可能把“防火墙”作为 vertex，而不是 `NetworkElement.elem_type=firewall`；candidate payload 需明确防火墙是 enum value。

### gq-019 收入增长情况怎么样

原始 prompt 摘要：

- Decomposer 输入原问题，要求不要丢弃“收入”“增长”“情况”。
- Grounded 输入只有 network topology registry candidates；没有 revenue metric、time dimension 或 growth comparator candidates。

预期/样例 Question Decomposer 输出：

```json
{
  "schema_version": "question_decomposition_v1",
  "intent_type": "compare",
  "target_concepts": ["收入"],
  "relation_phrases": ["增长"],
  "literal_candidates": [],
  "filter_phrases": [],
  "time_terms": [],
  "modality_terms": [],
  "substantive_terms": ["收入", "增长", "情况"],
  "stopword_terms": [],
  "unparsed_terms": [],
  "output_shape": "unknown"
}
```

预期/样例 Grounded Understanding 输出：

```json
{
  "schema_version": "grounded_understanding_v1",
  "status": "unsupported_query_shape",
  "query_shape": "unsupported",
  "selected_bindings": {},
  "literal_bindings": [],
  "coverage": {
    "substantive_terms": {"covered": [], "uncovered": ["收入", "增长", "情况"]}
  },
  "ambiguity": [],
  "unsupported": {"reason_code": "coverage_gap_unknown_metric", "message": "No revenue or growth metric exists in the graph semantic model."},
  "confidence": 0.88
}
```

Schema 校验预期：通过。最终状态应为 `unsupported_query_shape`，reason code 对齐 golden set。

风险点：

- LLM 可能把“增长”降级为 modality 或忽略，导致 coverage false positive。
- LLM 可能发明 `revenue_growth` metric；candidate-boundary 必须计为 invention 并拒绝。

### gq-020 计算 ne-0001 到 ne-0004 的最短路径

原始 prompt 摘要：

- Decomposer 输入原问题，要求识别两个设备 ID、最短路径、计算/graph algorithm intent。
- Grounded 输入包含 `NetworkElement` 和 ID resolver results，但没有 shortest path algorithm 或 path_pattern candidate。

预期/样例 Question Decomposer 输出：

```json
{
  "schema_version": "question_decomposition_v1",
  "intent_type": "path",
  "target_concepts": ["设备", "路径"],
  "relation_phrases": ["到", "最短路径"],
  "literal_candidates": [
    {"text": "ne-0001", "kind_hint": "id", "attached_to": "设备"},
    {"text": "ne-0004", "kind_hint": "id", "attached_to": "设备"}
  ],
  "filter_phrases": ["ne-0001 到 ne-0004"],
  "time_terms": [],
  "modality_terms": [],
  "substantive_terms": ["计算", "ne-0001", "ne-0004", "最短路径"],
  "stopword_terms": [],
  "unparsed_terms": [],
  "output_shape": "path"
}
```

预期/样例 Grounded Understanding 输出：

```json
{
  "schema_version": "grounded_understanding_v1",
  "status": "unsupported_query_shape",
  "query_shape": "unsupported",
  "selected_bindings": {
    "vertex": {"candidate_id": "v_network_element", "name": "NetworkElement"}
  },
  "literal_bindings": [
    {"raw": "ne-0001", "normalized": "ne-0001", "resolver_match_type": "value_index_exact"},
    {"raw": "ne-0004", "normalized": "ne-0004", "resolver_match_type": "value_index_exact"}
  ],
  "coverage": {
    "substantive_terms": {"covered": ["ne-0001", "ne-0004"], "uncovered": ["计算", "最短路径"]}
  },
  "ambiguity": [],
  "unsupported": {"reason_code": "unsupported_shortest_path", "message": "Shortest path graph algorithm is outside Restricted Query DSL v1."},
  "confidence": 0.91
}
```

Schema 校验预期：通过。最终状态应为 `unsupported_query_shape`，不得改写为普通 `PATH_THROUGH` 或无界 traversal。

风险点：

- LLM 可能将 “ne-0001 到 ne-0004” 误套到 `PATH_THROUGH`，但 `PATH_THROUGH` 是 `Tunnel -> NetworkElement`，不表达设备间最短路径。
- “计算”本身可以是 intent 触发词，但与“最短路径”组合后必须保留 unsupported graph algorithm 信号。

## 7. 指标表

2026-05-28 使用 OpenAI-compatible DashScope/Qwen provider 做了真实 smoke。API key 只通过本地环境变量注入，未写入仓库、trace fixture 或报告。

关键结论：

- 让 LLM 同时生成 `question_decomposition_v1` 和 `grounded_understanding_v1` 会出现输出形态漂移，例如嵌套 `binding/literal`、`property:Port.status` 简写、遗漏 literal slot 等。
- 主链路已改为 deterministic grounding 优先：LLM 只负责 Question Decomposer 的 surface slot 填充；candidate retrieval、literal request 补全、literal resolving、edge/vertex 连接、count 聚合和 projection 由代码从语义层确定。
- Grounded Understanding LLM 仍保留为 fallback/repair 能力，但不作为 v1 常规路径的正确性前提。

| metric | live smoke value | 目标阈值建议 | 说明 |
| --- | --- | --- | --- |
| Decomposer schema failure rate | 0/5 | <= 5% after retry on golden v1 smoke | 5 条真实 smoke 均返回可解析 `question_decomposition_v1` |
| generated rate after deterministic grounding | 5/5 | smoke 样本全部 generated | 生成后均经过 DSL parser、compiler、self-validation |
| critical filter preservation | 5/5 | 0 critical miss | `Gold级别`、`tun-mpls-001`、`ne-0001`、`防火墙`、`down` 均进入最终 Cypher 过滤或路径约束 |
| Grounded LLM dependency | 0 regular-path calls | 常规路径不依赖 | trace 仍有 `grounded_understanding` stage，但其输出由 deterministic grounding 构造 |
| candidate invention rate in regular path | 0% | 0% | 常规路径不接受 LLM invented candidate；所有 binding 均来自 retrieval candidates |

真实 smoke 结果：

| id | question | status | generated Cypher shape |
| --- | --- | --- | --- |
| live-001 | Gold级别的服务都使用了哪些隧道 | generated | `Service -[:SERVICE_USES_TUNNEL]-> Tunnel` + `svc.quality_of_service = $quality_of_service` |
| live-002 | 隧道 tun-mpls-001 经过哪些设备 | generated | `Tunnel -[:PATH_THROUGH]-> NetworkElement` + `tun.id = $id` |
| live-003 | 设备 ne-0001 有哪些端口 | generated | `NetworkElement -[:HAS_PORT]-> Port` + `ne.id = $id` |
| live-004 | 有多少台防火墙 | generated | `count(NetworkElement.id)` + `ne.elem_type = $elem_type` |
| live-005 | 显示down状态的端口 | generated | `Port` lookup + `port.status = $status` |

## 8. 进入 IR-13/14 的条件与降级策略

允许进入：

- IR-13 保留 Question Decomposer schema、prompt builder、provider protocol、schema retry 和 trace。
- IR-14 的 Grounded Understanding schema、candidate-boundary validator 和 repair-loop 测试保留，但常规主路径不得依赖 LLM 生成 grounded JSON。
- 真实 provider run 已补齐，v1 的可行性前提是“LLM 填槽 + 工程化 grounding”，不是“LLM 直接给最终 semantic binding”。

必须保留的降级策略：

- Question Decomposer schema invalid：最多 retry 2 次；仍失败返回 `generation_failed` 或输入明显缺指代时返回 `clarification_required`。
- LLM provider unavailable：返回 `service_failed`，trace 记录 provider、错误类型和 retry 次数；不使用 deterministic 猜测冒充 LLM。
- Deterministic grounding 无法覆盖：才进入 Grounded Understanding fallback；candidate invention 仍然 stage failed，交给 Repair Controller 或返回 `generation_failed`；不进入 binder。
- `coverage.substantive_terms.uncovered` 非空：不得生成 Cypher，进入 clarification 或 `unsupported_query_shape`。
- unknown metric / unsupported graph algorithm：返回 `unsupported_query_shape`，不近似改写。
- DSL 不支持：不回退 raw Cypher。

## 9. 后续建议

后续应把本报告中的五个样本固化为 provider-contract fixture：fake client 只返回 `question_decomposition_v1`，主路径断言不会调用 Grounded LLM。真实 provider eval 可以生成 JSONL artifact，但 artifact 不得包含 API key、Authorization header 或完整敏感环境变量。
