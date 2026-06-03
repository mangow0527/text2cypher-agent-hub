# LiteralResolver v1 设计

> 日期：2026-05-27
> 状态：设计 v1
> 上游：Question Decomposer、Candidate Retriever、Semantic Binder
> 下游：Semantic Validator、Repair and Clarification Controller

## 1. 设计目标

LiteralResolver 是独立子系统，负责把自然语言中的字面值解析为 Graph Semantic Model v1 中可用的 property 值、metric 参数或 path_pattern 参数。

字面值包括：

- 枚举值：`Gold`、`down`、`MPLS-TE`。
- 业务 ID：`ne-0001`、`svc-1234`。
- 名称：设备名、服务名、隧道名。
- 时间值：`最近 7 天`、`2024 年`。
- 数值过滤：`大于 100G`、`前 5 个`。

该组件不能只是 Candidate Retriever 的一条策略。网络拓扑场景中枚举值、ID 和名称高频出现，解析错误会直接导致错误 Cypher 或错误过滤条件。

## 2. 输入输出契约

输入：

```json
{
  "schema_version": "literal_resolver_request_v1",
  "raw_literal": "防火墙",
  "expected_vertex": "NetworkElement",
  "expected_edge": null,
  "expected_property": "elem_type",
  "literal_kind_hint": "enum_or_name",
  "question_context": "全网有多少台防火墙",
  "trace_id": "q-20260527-001"
}
```

输出：

```json
{
  "schema_version": "literal_resolver_result_v1",
  "raw_literal": "防火墙",
  "resolved": true,
  "resolved_value": "firewall",
  "normalized_value": "firewall",
  "match_type": "value_synonym",
  "confidence": 0.98,
  "expected_vertex": "NetworkElement",
  "expected_edge": null,
  "expected_property": "elem_type",
  "evidence": [
    {
      "source": "property.value_synonyms",
      "matched": "防火墙",
      "target": "firewall"
    }
  ],
  "alternatives": [],
  "requires_user_choice": false
}
```

失败输出：

```json
{
  "schema_version": "literal_resolver_result_v1",
  "raw_literal": "防火墙设备",
  "resolved": false,
  "resolved_value": null,
  "normalized_value": null,
  "match_type": "unresolved",
  "confidence": 0.0,
  "expected_vertex": "NetworkElement",
  "expected_edge": null,
  "expected_property": "elem_type",
  "evidence": [],
  "alternatives": [
    {
      "value": "firewall",
      "display": "防火墙",
      "confidence": 0.84,
      "source": "property.valid_values"
    }
  ],
  "requires_user_choice": true
}
```

## 3. 解析流水线

解析顺序固定：

1. `exact_value_match`：对 property `valid_values`、ID 值索引或参数索引做标准化精确匹配。
2. `value_synonym_match`：对 property 顶层 `value_synonyms` 做精确同义词匹配。
3. `typed_parser`：对 `datetime`、数值、容量、百分比、`list<T>` 等类型化字面值做解析。
4. `fuzzy_text_match`：对低风险名称类 property 做编辑距离或 token 相似度匹配。
5. `embedding_match`：仅对名称类或描述类 property 启用，默认不用于高风险枚举。
6. `value_index_lookup`：从预构建或同步进入 CGA 的 value index 中查找候选。

高风险枚举 property 策略：

- 先 exact，再 value_synonym，再 value index。
- 不使用 embedding 自动通过。
- fuzzy 命中只能给 alternatives，不能直接 resolved。
- `value_synonyms` 的 key 必须已由模型 validator 确认存在于 `valid_values`。

名称/ID property 策略：

- 如果 raw literal 符合 ID 形态，例如 `ne-0001`，只允许在预构建 value index 中精确查找。
- value index 命中即 `match_type=value_index_exact`。
- value index 未命中时可返回 alternatives，但不能把相近 ID 静默替换，也不能直连数据库补查。

## 4. Match Type

| match_type | 可自动通过 | 说明 |
| --- | --- | --- |
| `exact` | 是 | 规范值直接匹配 |
| `value_synonym` | 是 | property 顶层 `value_synonyms` 匹配 |
| `typed_parse` | 是 | 时间、数值、容量等解析成功 |
| `value_index_exact` | 是 | 预构建值索引精确命中 |
| `fuzzy_text` | 视置信度 | 名称类 property 可高置信通过 |
| `embedding` | 视字段风险 | 不用于枚举自动通过 |
| `distinct_candidate` | 否 | 只作为 alternatives |
| `unresolved` | 否 | 进入澄清或覆盖缺失 |

置信度阈值默认：

- `>= 0.95`：可自动通过。
- `0.80 - 0.95`：如 property 低风险且候选领先明显，可带 assumption 通过。
- `< 0.80`：不得自动通过。
- top-2 差距 `< 0.10`：必须反问用户。

## 5. Value Index Cache

配置：

```yaml
literal_resolver:
  value_index_cache:
    ttl_seconds: 3600
    negative_ttl_seconds: 60
    max_values_per_property: 5000
    hot_value_window_seconds: 86400
    allow_database_live_lookup: false
```

缓存键：

```text
semantic_model_name + vertex_or_edge_name + property_name + tenant_id + data_version
```

失效策略：

- 支持 TTL 被动失效。
- 支持上游值索引刷新触发主动失效：CDC、write callback 或手动 refresh endpoint 由索引构建服务处理，CGA 只接收刷新后的索引快照。
- 对 ID/名称 property，cache miss 不直连数据库；若 raw literal 形态像精确 ID，也只能在本地 value index 或已同步缓存中确认。
- negative cache TTL 必须短，避免新写入数据被长时间误判不存在。

热点策略：

- `max_values_per_property` 只限制完整 value 列表缓存。
- 另维护 hot value map，记录近期命中值和命中次数。
- 大维度 property 优先缓存 hot values，再按需分页 value index lookup。

监控指标：

- `literal_cache_hit_rate`
- `literal_cache_miss_rate`
- `literal_negative_cache_hit_count`
- `literal_unresolved_count`
- `literal_ambiguous_count`

上线后如果某 property cache hit rate 长期偏低，需调整 TTL、主动失效或 hot value 策略。

## 6. 数据库连接边界

CGA v1 不做 live DB lookup，不连接 TuGraph 或业务数据库。LiteralResolver 只能使用：

- `ai_context.synonyms`。
- property 顶层 `valid_values` 和 `value_synonyms`。
- 随 graph semantic model 发布的枚举表。
- 预构建 value index。
- 已同步到本地或近端缓存的 hot values。

如果索引中没有某个新写入的设备或服务，CGA 返回 `literal_unresolved` 或 alternatives，并在 trace 中标记 `value_index_miss`。索引新鲜度由上游 semantic/value-index 服务负责。

禁止：

- 在 CGA 内对 TuGraph 发起实时查询。
- 在 CGA 内对业务数据库做 distinct values 查询。
- cache miss 后为了确认 ID 是否存在而执行 Cypher。

需要实时查库能力时，应由独立 value-index 服务或 testing-agent/runtime service 处理，不纳入 cypher-generator-agent v1。

## 7. Alternatives 生成规则

alternatives 面向用户澄清，必须简短、可选择、有证据：

```json
{
  "value": "firewall",
  "display": "防火墙",
  "confidence": 0.84,
  "source": "property.valid_values",
  "why": "与输入“防火墙设备”最接近，并且属于 NetworkElement.elem_type 的合法值"
}
```

规则：

- 最多返回 3 个。
- 按 confidence 降序。
- 不能返回跨 property 候选，例如把 `status=down` 候选给 `elem_type`。
- 对用户可见的 display 使用业务展示值，不暴露内部编码，除非编码本身就是用户输入对象。

## 8. 与语义校验的关系

LiteralResolver 不决定整个查询是否可生成。它只输出：

- resolved value。
- match_type。
- confidence。
- alternatives。
- evidence。

Semantic Validator 根据这些信号决策：

- 高置信 exact/value_synonym 可放行。
- fuzzy/embedding 需要结合 property 风险和候选差距。
- unresolved 的 substantive literal 必须澄清或失败。
- 低置信高风险枚举不能自动通过。

## 9. 错误码

| 错误码 | 含义 | 默认处理 |
| --- | --- | --- |
| `literal_unresolved` | 找不到可用值 | 反问或 semantic coverage failure |
| `literal_ambiguous` | 多候选接近 | 反问用户 |
| `literal_property_mismatch` | 字面值被绑定到错误 property | repair loop |
| `literal_value_index_miss` | 值索引没有该值 | 反问用户或提示语义值索引可能未更新 |
| `literal_cache_stale_suspected` | 值索引版本落后于语义模型或数据版本 | 记录 stale signal，等待上游刷新 |

## 10. 测试要求

v1 实现时至少覆盖：

- `防火墙` -> `firewall` value synonym。
- 拼写错误或近似说法进入 alternatives，不自动通过。
- 新写入 ID cache miss 后不触发数据库查询，返回 unresolved 并记录 `value_index_miss`。
- 高风险枚举不因 embedding 相近被自动改写。
- top-2 候选接近时要求用户选择。
- negative cache 在短 TTL 后允许重新查询。
- CGA 内没有数据库 live lookup 调用路径。
