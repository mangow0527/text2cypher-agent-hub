# Knowledge Agent Full Schema, Typed Few-Shots, and Knowledge Editor Design

## Context

The current Knowledge Agent builds a prompt package from local knowledge files:

- `schema.json`
- `cypher_syntax.md`
- `business_knowledge.md`
- `few_shot.md`
- `system_prompt.md`

The service already returns a schema section, but the formatter collapses schema details into label/property and relationship summaries. The retriever also filters business knowledge and few-shot examples by question keywords. The frontend can request a prompt package and submit repair suggestions, but it does not provide direct document editing.

## Goals

1. Return the full schema in prompt packages.
2. Select few-shot examples by TuGraph-supported Cypher query capability rather than only by business keyword.
3. Add a frontend knowledge editor for editable knowledge documents.
4. Keep `schema.json` read-only in the frontend.
5. Preserve version history for manual knowledge edits.

## Non-Goals

- Do not allow editing `schema.json` from the frontend.
- Do not replace the existing repair workflow.
- Do not introduce database-backed knowledge storage.
- Do not add model-based query classification in the first implementation.

## Query Type Model

Few-shot examples will be grouped by TuGraph/Cypher query capabilities. The first implementation will use a conservative enum:

- `GENERAL`
- `MATCH_RETURN`
- `WHERE_FILTER`
- `PATH_TRAVERSAL`
- `MULTI_HOP`
- `AGGREGATION`
- `GROUP_AGGREGATION`
- `ORDER_LIMIT`
- `WITH_STAGE`
- `DISTINCT_DEDUP`
- `VARIABLE_LENGTH_PATH`

These groups map to commonly supported Cypher constructs such as `MATCH`, `WHERE`, `RETURN`, `WITH`, `ORDER BY`, `LIMIT`, aggregation functions, path traversal, and `DISTINCT`.

## Backend Design

### Full Schema Formatting

`schema_formatter.format_schema` will render the full schema instead of a compact summary.

For vertex labels, it will include:

- label name
- type
- description
- primary key
- every property with name, type, optional, unique, index, and description when present

For edge labels, it will include:

- edge label name
- type
- description
- every `(source)-[edge]->(target)` constraint
- every property with metadata when present

The formatter will keep backwards compatibility with the existing compact dict schema shape used in tests and bootstrapping.

### Retriever Changes

`KnowledgeRetriever.retrieve(question)` will always include full schema context.

Business knowledge can remain lightly filtered for now, because the user request only requires full schema. If filtering returns no relevant lines, the retriever will fall back to the full business document.

Few-shot selection will move from keyword-only matching to typed selection:

1. Infer query types from question text using deterministic rules.
2. Parse `few_shot.md` into blocks.
3. Select blocks tagged with matching query types plus `GENERAL`.
4. Add high-scoring business keyword matches as secondary tie-breakers.
5. Fall back to recent examples if no typed examples match.

### Few-Shot Document Format

The editor and parser will support examples like:

```text
[id: protocol_version_filter]
[types: WHERE_FILTER, PATH_TRAVERSAL]
Question: 查询协议版本为v2.0的隧道所属网元
Cypher: MATCH (n:NetworkElement)-[:FIBER_SRC]->(t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol) WHERE p.version = 'v2.0' RETURN n.id
Why: 展示路径遍历和协议版本过滤。
```

Existing examples without `[types: ...]` will be treated as `GENERAL`, so current files remain valid.

### Knowledge Document API

Add API endpoints under `/api/knowledge/documents`:

- `GET /api/knowledge/documents`
  - returns editable documents and read-only documents with metadata.
- `GET /api/knowledge/documents/{doc_type}`
  - returns document content.
- `PUT /api/knowledge/documents/{doc_type}`
  - saves editable document content through `KnowledgeStore.write_versioned`.

Editable documents:

- `system_prompt`
- `cypher_syntax`
- `business_knowledge`
- `few_shot`

Read-only document:

- `schema`

The API will reject writes to `schema` with a clear application error.

## Frontend Design

The frontend will keep the current prompt package and repair panels, and add a knowledge editor workspace.

Editor behavior:

- Document selector shows all documents.
- `schema` is marked read-only and opens in a read-only viewer.
- Editable Markdown documents open in a textarea.
- Save writes the full document content.
- Dirty state shows when local text differs from loaded text.
- Save success shows the updated timestamp or a compact success status.
- Save errors are shown inline.

The UI should stay operational and compact, closer to an admin console than a marketing page. No schema edit controls should be shown.

## Data Flow

Prompt package flow:

```text
Frontend question
-> POST /api/knowledge/rag/prompt-package
-> PromptService
-> KnowledgeRetriever
-> full schema formatter + typed few-shot selector
-> prompt package response
```

Editor flow:

```text
Frontend document selector
-> GET /api/knowledge/documents
-> GET /api/knowledge/documents/{doc_type}
-> user edits text
-> PUT /api/knowledge/documents/{doc_type}
-> KnowledgeStore.write_versioned
-> history snapshot written
```

## Error Handling

- Unknown document type returns a validation or application error.
- Attempts to save `schema` return an explicit read-only error.
- Empty editable documents are allowed only if the request is intentional; the frontend will show a confirmation-style warning through copy/state, not a blocking modal.
- JSON schema read failures surface as API errors and do not silently return partial schema.

## Testing

Backend tests:

- Full schema formatter includes property metadata and edge constraints.
- Retriever always includes full schema details.
- Few-shot selector returns examples matching inferred query types.
- Existing untyped few-shots remain available as `GENERAL`.
- Document list/read/update API works for editable docs.
- Saving editable docs writes a history snapshot.
- Saving `schema` is rejected.

Frontend tests or build checks:

- API client covers document list/read/update.
- TypeScript build passes.
- UI renders editor states without type errors.

## Implementation Notes

The first implementation should stay deterministic. Query type inference should use simple rules based on question text and known Cypher concepts rather than calling an LLM. This keeps prompt assembly explainable and testable.

The prompt still has an 80,000 character cap. If full schema plus examples exceeds the cap, prompt composition should clip lower-priority sections first, preserving system prompt, schema, generation requirements, and user question.
