# repair-agent

This service analyzes failed evaluations, produces knowledge repair suggestions, and dispatches repair payloads to `knowledge-agent`.

## Contract

`repair-agent` consumes failed `IssueTicket` payloads from `testing-agent`. It does not call `cypher-generator-agent`, execute TuGraph, configure the generator LLM, or post to feedback endpoints owned by other services. Generation facts, execution results, evaluation evidence, and the prompt snapshot must arrive in the ticket.

Required prompt evidence comes from:

```text
IssueTicket.generation_evidence.input_prompt_snapshot
```

The LLM diagnosis response must be a JSON object with:

```text
repairable
non_repairable_reason
primary_knowledge_type
secondary_knowledge_types
confidence
suggestion
rationale
```

Allowed knowledge types are:

```text
cypher_syntax
few_shot
system_prompt
business_knowledge
```

When `repairable=true`, the outbound knowledge-agent apply payload is limited to:

```json
{
  "id": "q-001",
  "suggestion": "...",
  "knowledge_types": ["business_knowledge"]
}
```

## Endpoints

- `GET /health`: service liveness.
- `GET /api/v1/status`: local storage, knowledge-agent apply URL, and repair LLM diagnosis configuration.
- `POST /api/v1/issue-tickets`: analyze one failed ticket and, when repairable, apply the knowledge repair.
- `GET /api/v1/analyses/{analysis_id}`: fetch a saved repair analysis record.

## Settings

Environment variables use the `REPAIR_SERVICE_` prefix. Repair-owned settings are:

```text
APP_NAME
HOST
PORT
DATA_DIR
KNOWLEDGE_AGENT_REPAIRS_APPLY_URL
KNOWLEDGE_AGENT_REPAIRS_APPLY_CAPTURE_DIR
KNOWLEDGE_AGENT_REPAIRS_APPLY_MAX_ATTEMPTS
REQUEST_TIMEOUT_SECONDS
LLM_ENABLED
LLM_PROVIDER
LLM_BASE_URL
LLM_API_KEY
LLM_MODEL_NAME
LLM_TEMPERATURE
LLM_MAX_RETRIES
LLM_RETRY_BASE_DELAY_SECONDS
LLM_MAX_CONCURRENCY
```
