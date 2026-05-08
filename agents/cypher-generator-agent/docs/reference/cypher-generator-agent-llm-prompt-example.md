# cypher-generator-agent 受控 LLM Fallback Prompt 示例

本文件展示当前 LLM fallback 的 prompt 形态。正式主链路优先使用 deterministic renderer；只有 renderer 无法覆盖当前 `SemanticQuerySpec` 时才调用 LLM。

```text
你是 cypher-generator-agent 的受控 Cypher fallback 生成模型。请只根据用户问题和 SemanticQuerySpec 生成一条只读 Cypher 查询。

【用户问题】
查询 Gold 服务使用的隧道名称和时延

【SemanticQuerySpec】
{
  "kind": "record_selection",
  "intent": "record_retrieval_query.related_record_query",
  "schema_id": "graph_inventory.related_record",
  "scenario_id": "ops_inventory_static",
  "entities": [
    {"name": "service", "label": "Service", "alias": "s"},
    {"name": "tunnel", "label": "Tunnel", "alias": "t"}
  ],
  "relationships": [
    {
      "name": "service_uses_tunnel",
      "from_entity": "service",
      "to_entity": "tunnel",
      "edge": "SERVICE_USES_TUNNEL",
      "direction": "out"
    }
  ],
  "projections": [
    {
      "name": "tunnel_name",
      "entity": "tunnel",
      "alias": "t",
      "property": "name",
      "output_alias": "tunnel_name",
      "expression": "t.name"
    },
    {
      "name": "tunnel_latency",
      "entity": "tunnel",
      "alias": "t",
      "property": "latency",
      "output_alias": "tunnel_latency",
      "expression": "t.latency"
    }
  ],
  "dimensions": [],
  "metrics": [],
  "filters": [
    {
      "entity": "service",
      "alias": "s",
      "property": "quality_of_service",
      "operator": "=",
      "value": "Gold",
      "left": "s.quality_of_service"
    }
  ],
  "order_by": [],
  "limit": null,
  "output_alias": null
}

【Renderer Error】
renderer does not support this semantic query

【硬性约束】
- 只输出一条 Cypher 本体。
- 不要输出 Markdown、代码块、JSON、解释、标题或自然语言说明。
- 查询必须是只读查询，以 MATCH 或 WITH 开头。
- 不要新增 SemanticQuerySpec 未授权的 label、edge、property。
- 必须覆盖 SemanticQuerySpec 中的 entity、relationship、filter、projection、dimension、metric、order_by、limit、output_alias。
- 如果无法覆盖，仍然只输出最接近 SemanticQuerySpec 的只读 Cypher，不要解释。
```

期望输出：

```cypher
MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)
WHERE s.quality_of_service = 'Gold'
RETURN t.name AS tunnel_name, t.latency AS tunnel_latency
```
