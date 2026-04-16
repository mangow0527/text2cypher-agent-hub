# Knowledge Management Agent Design For Text2Cypher

## 1. Goal

Build a knowledge management agent for the local Text2Cypher system so the Cypher generation agent can first retrieve a RAG-style prompt string grounded in maintained knowledge, and the repair agent can automatically update the maintained knowledge through structured suggestions.

This design focuses on one bounded subsystem:

- retrieve generation knowledge for Text2Cypher
- maintain non-schema knowledge as editable documents
- apply repair suggestions automatically
- keep the output surface simple for upstream agents

This phase does not attempt to build a general long-term memory platform.

## 2. Scope

The knowledge management agent serves two consumers:

1. the Cypher generation agent
2. the repair agent

The generation agent needs a final prompt string, not raw documents.

The repair agent needs a single write endpoint that accepts a suggestion and optionally a target knowledge type.

## 3. Non-Goals

This design does not include:

- editing schema inside the knowledge management agent
- human approval workflow before knowledge writes
- a standalone UI for knowledge authoring
- multi-tenant knowledge isolation
- a generalized vector database memory product

## 4. Knowledge Boundaries

The system recognizes five knowledge categories:

- `schema`
- `cypher_syntax`
- `few_shot`
- `system_prompt`
- `business_knowledge`

### 4.1 Schema Is External And Read-Only

`schema` is not maintained by the knowledge management agent.

It is treated as a fixed JSON source provided by the surrounding system. The agent may read it and format it for prompt construction, but it may not modify it through repair writes.

This avoids polluting graph-structure truth with LLM-written edits.

### 4.2 Managed Knowledge Documents

The knowledge management agent automatically maintains four editable documents:

- `cypher_syntax.md`
- `few_shot.md`
- `system_prompt.md`
- `business_knowledge.md`

Each document is a business truth source for one kind of maintained knowledge and must be updated through constrained patching rather than whole-document rewrite.

## 5. Architecture

The subsystem uses a document-centered architecture.

`question -> internal retrieval -> prompt assembly -> final prompt string`

`repair suggestion -> document routing -> constrained patch generation -> atomic write`

### 5.1 Major Components

- `knowledge_retriever`
  - reads schema and maintained documents
  - selects relevant knowledge for a question
- `prompt_assembler`
  - formats retrieved knowledge into a single prompt string
- `repair_router`
  - maps a suggestion to one or more managed document types
- `patch_generator`
  - uses the model to propose minimal edits
- `document_store`
  - reads current document content
  - writes new versions atomically
  - preserves rollback history

### 5.2 Principles

- document files are the source of truth for managed knowledge
- schema is authoritative but external
- prompt output is simple on the outside and structured on the inside
- repair writes must be minimal and bounded
- automatic writes must remain reversible

## 6. HTTP API

The subsystem exposes two HTTP endpoints.

## 6.1 Retrieve Prompt Package

URL:

- `POST /api/knowledge/rag/prompt-package`

Purpose:

- accept `id` and `question`
- retrieve relevant knowledge
- return one final prompt string for the Cypher generation agent

Request body:

```json
{
  "id": "q_001",
  "question": "帮我查询协议版本为v2.0的隧道所属网元"
}
```

Response body:

```json
{
  "status": "ok",
  "id": "q_001",
  "prompt": "你是一个严格的 TuGraph Text2Cypher 生成器。\n\n【Schema】...\n\n【TuGraph Cypher 语法约束】...\n\n【业务知识】...\n\n【参考示例】...\n\n【生成要求】...\n\n【用户问题】帮我查询协议版本为v2.0的隧道所属网元"
}
```

The upstream caller does not choose what to retrieve. All retrieval and filtering are internal responsibilities of the knowledge management agent.

## 6.2 Apply Repair Suggestion

URL:

- `POST /api/knowledge/repairs/apply`

Purpose:

- accept a repair suggestion
- optionally accept explicit target knowledge types
- update the managed knowledge documents automatically

Request body:

```json
{
  "id": "q_001",
  "suggestion": "将 HAS_PORT 的方向修正为 NetworkElement -> Port，并补充一条对应 few-shot",
  "knowledge_types": ["few_shot", "business_knowledge"]
}
```

`knowledge_types` is optional. If not provided, the agent decides the target document type internally.

Allowed values are:

- `cypher_syntax`
- `few_shot`
- `system_prompt`
- `business_knowledge`

Response body:

```json
{
  "status": "ok"
}
```

The write path intentionally returns a minimal success contract. Detailed write logs remain internal system data.

## 7. Prompt Construction

The retrieve endpoint does not return raw knowledge sections. It returns a final RAG-style prompt string.

The internal assembly order is fixed:

1. role and task
2. formatted schema summary
3. TuGraph syntax constraints
4. business knowledge
5. few-shot examples
6. generation requirements
7. user question

### 7.1 Prompt Template

The final prompt should follow a stable template:

```text
你是一个严格的 TuGraph Text2Cypher 生成器。

你的任务是：根据给定的 Schema、TuGraph Cypher 语法约束、业务知识和示例，为用户问题生成可执行且语义准确的 Cypher。

请遵守以下总原则：
1. 只能使用给定 Schema 中存在的节点、关系、属性。
2. 必须遵守 TuGraph 支持的 Cypher 语法与限制。
3. 优先采用业务知识中的术语映射与语义解释。
4. 参考 few-shot 示例的写法，但不得机械照抄。
5. 如果问题信息不足，生成语义最保守、结构最可靠的查询。
6. 不得虚构不存在的 schema 元素或业务含义。

【Schema】
{schema_context}

【TuGraph Cypher 语法约束】
{syntax_context}

【业务知识】
{business_context}

【参考示例】
{few_shot_examples}

【生成要求】
- 输出必须是单条 Cypher
- 不要输出解释
- 不要输出 Markdown
- 确保方向、属性、过滤条件、聚合语义正确

【用户问题】
{question}
```

### 7.2 Retrieval Policy

The retrieval policy is internal and should:

- always include the active system prompt guidance
- always include schema context
- retrieve only the most relevant syntax notes
- retrieve only the most relevant business notes
- include a small number of high-quality few-shot examples

The first implementation should keep few-shot count small, typically `2-4` examples.

## 8. Document Responsibilities

## 8.1 `cypher_syntax.md`

This document stores TuGraph-specific syntax facts:

- supported patterns
- unsupported patterns
- rewrite rules
- aggregation and `WITH` notes
- common execution pitfalls

It should not store business semantics.

## 8.2 `few_shot.md`

This document stores example pairs and example rationale:

- question
- cypher
- optional explanation of why the example matters

It should not become a dumping ground of arbitrary failures.

## 8.3 `system_prompt.md`

This document stores generation strategy:

- role
- core rules
- output constraints
- fallback strategy
- things the generator must not do

It should contain reusable behavior guidance, not schema facts.

## 8.4 `business_knowledge.md`

This document stores business semantics:

- terminology mapping
- field meaning
- question-intent patterns
- ambiguity notes

It should not redefine syntax constraints or schema truth.

## 9. Repair Write Design

Automatic repair writes are allowed, but they must not directly rewrite whole documents.

The write path must follow this shape:

`suggestion -> route document -> locate section -> generate minimal patch -> validate patch -> atomic write`

### 9.1 Routing Rules

If `knowledge_types` is present, the write scope is limited to those types.

If `knowledge_types` is absent, the agent infers the target category using these defaults:

- syntax and dialect issues -> `cypher_syntax`
- example insufficiency or misleading example -> `few_shot`
- behavior-rule problems -> `system_prompt`
- terminology or semantic mapping issues -> `business_knowledge`

The first implementation should avoid spreading one suggestion across too many documents. Inference should usually target one document and at most two.

### 9.2 Allowed Patch Actions

The model may only propose minimal patch actions:

- add one section item
- modify one section item
- append one example

The model may not replace the full document.

### 9.3 Patch Payload

The internal patch proposal should be structured:

```json
{
  "doc_type": "business_knowledge",
  "section": "Terminology Mapping",
  "action": "add_section_item",
  "target_key": "protocol_version_mapping",
  "new_content": "- “协议版本”优先映射到 `Protocol.version`。",
  "reason": "suggestion points to a semantic mapping gap"
}
```

The application layer, not the model, is responsible for editing the file.

## 10. File Format Constraints

Managed knowledge documents must use stable section names and stable item identifiers.

Example:

```md
## Terminology Mapping

[id: protocol_version_mapping]
- “协议版本”优先映射到 `Protocol.version`。

[id: network_element_alias]
- “网元”对应 `NetworkElement`。
```

This constraint makes deterministic patch application possible.

## 11. Safety And Recovery

Automatic writes take effect immediately, but every write must retain:

- pre-write content
- post-write content
- suggestion text
- target document types
- timestamp

This enables rollback when a bad automatic update pollutes a knowledge document.

The interface does not expose rollback in this phase, but the design requires internal rollback-ready storage.

## 12. Testing Strategy

The first implementation should include:

- endpoint contract tests for both HTTP APIs
- prompt assembly tests to verify section order and required content
- repair routing tests for explicit and implicit `knowledge_types`
- patch application tests to ensure only target sections change
- rollback snapshot tests to ensure write history is preserved

## 13. Recommended Implementation Order

1. define API request and response contracts
2. define managed document templates
3. implement prompt assembly over static document content
4. implement repair routing and minimal patch proposal flow
5. implement atomic write and rollback snapshot storage
6. add endpoint and integration tests

## 14. Open Decisions Already Resolved

This design fixes the following decisions:

- prompt retrieval input is only `id` and `question`
- prompt retrieval output is one final string `prompt`
- repair input is `id`, `suggestion`, and optional `knowledge_types`
- repair output is only `status: ok`
- schema is not editable through this subsystem
- managed knowledge is stored in four markdown documents
