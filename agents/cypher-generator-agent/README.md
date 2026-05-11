# cypher-generator-agent

This service accepts natural-language questions, validates semantic assets, compiles questions into a controlled `LogicalQueryPlan`, renders read-only Cypher, and submits successful generation, clarification, or failure evidence to `testing-agent`.

The production path is semantic-view-pipeline first:

```text
question
  -> semantic contract alignment
  -> intent recognition
  -> semantic view matching
  -> LogicalQueryPlan
  -> schema graph path planning
  -> RAG knowledge selection
  -> deterministic renderer
  -> controlled LLM fallback when renderer cannot cover
  -> semantic + schema + execution preflight
  -> testing-agent submission / clarification_required / generation_failed / service_failed
```

LLM use is bounded to intent fallback, semantic-view disambiguation, and controlled Cypher fallback after a `LogicalQueryPlan` and selected schema path exist. The service does not execute Cypher or evaluate answer correctness.
