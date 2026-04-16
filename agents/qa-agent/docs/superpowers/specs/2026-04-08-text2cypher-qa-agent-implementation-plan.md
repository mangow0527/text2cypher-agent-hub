# Text2Cypher QA Agent Implementation Plan

## 1. Plan Goal

This plan turns the approved design into an implementation sequence for version one of the local `Text2Cypher QA Agent`.

The implementation must preserve the rule chain defined in [text2cypher_qa_generation_scheme.md](/Users/wangxinhao/muti-agent-offline-system/text2cypher_qa_generation_scheme.md) and the architecture defined in [2026-04-08-text2cypher-qa-agent-design.md](/Users/wangxinhao/muti-agent-offline-system/docs/superpowers/specs/2026-04-08-text2cypher-qa-agent-design.md).

## 2. Delivery Strategy

The system will be delivered in six phases:

1. project foundation
2. core domain and orchestration
3. external integrations
4. generation pipeline completion
5. operator surfaces
6. hardening and release readiness

Each phase produces a runnable checkpoint. No later phase should begin before the previous phase has a verifiable outcome.

## 3. Phase 1: Project Foundation

### Objectives

- initialize the Python backend structure
- initialize the React frontend structure
- define shared project conventions
- establish local configuration and artifact directories

### Tasks

- create backend package layout under `app/`
- create frontend app layout for React
- define configuration loading for:
  - OpenAI API
  - TuGraph connection
  - artifact root path
  - runtime mode defaults
- define base logging setup
- define base exception and error-code model
- define base typed models for requests and responses
- create artifact directory bootstrap logic

### Deliverables

- runnable backend app skeleton
- runnable frontend app skeleton
- environment configuration example files
- base typed model definitions

### Exit Criteria

- backend starts successfully
- frontend starts successfully
- local config is loaded without hardcoded secrets
- artifact directories are created automatically

## 4. Phase 2: Core Domain and Orchestration

### Objectives

- implement the main domain models
- implement the job state machine
- establish the orchestrator as the only stage driver

### Tasks

- implement:
  - `JobRequest`
  - `CanonicalSchemaSpec`
  - `CypherSkeleton`
  - `CypherCandidate`
  - `ValidatedSample`
  - `QASample`
- implement the fixed job states
- implement state transition recording
- implement stage result envelopes
- implement orchestrator stage execution contract
- implement failure handling and stop-on-failure behavior

### Deliverables

- domain model package
- orchestrator package
- stage transition logs

### Exit Criteria

- orchestrator can create and advance a mock job through all stages
- failed stages correctly stop the job and persist failure metadata

## 5. Phase 3: External Integrations

### Objectives

- implement controlled OpenAI access
- implement TuGraph execution adapter
- isolate both behind stable interfaces

### Tasks

- build `model_gateway` for:
  - structured generation
  - text generation
  - consistency judgment
- build prompt template loader and version manager
- build `graph_executor` for:
  - authenticated connection
  - query execution
  - result normalization
  - error normalization
  - latency capture
- implement retry boundaries
- implement timeout handling

### Deliverables

- `app/integrations/openai`
- `app/integrations/tugraph`
- prompt template assets

### Exit Criteria

- OpenAI calls can be made through `model_gateway`
- TuGraph queries can be executed through `graph_executor`
- errors are normalized to project error codes

## 6. Phase 4: Generation Pipeline Completion

### Objectives

- implement every stage of the rule pipeline
- produce valid intermediate and final artifacts

### Tasks

- implement `schema_service`
  - schema normalization
  - canonical schema generation
- implement `generation_service`
  - taxonomy loading
  - skeleton generation
  - candidate instantiation
- implement `validation_service`
  - syntax validation
  - schema validation
  - type/value validation
  - runtime validation via TuGraph
  - result sanity validation
- implement `question_service`
  - canonical Chinese question generation
  - Chinese variants generation
- implement `roundtrip_service`
  - question-to-Cypher regeneration
  - normalized Cypher comparison
  - semantic equivalence judgment
- implement deduplication rules
- implement split assignment:
  - `seed`
  - `silver`
  - `gold`
- implement artifact writing:
  - intermediate jsonl
  - final jsonl
  - manifest
  - error reports
  - job report

### Deliverables

- full pipeline implementation
- complete artifact writer
- end-to-end batch execution path

### Exit Criteria

- one complete job can run end-to-end against a provided schema and TuGraph
- pipeline emits all expected intermediate artifacts
- pipeline emits final release artifacts and reports

## 7. Phase 5: Operator Surfaces

### Objectives

- expose the system through CLI, HTTP API, and React frontend
- keep all surfaces thin and orchestration-driven

### Tasks

- implement CLI commands for:
  - schema import
  - job create
  - job run
  - job status
  - artifact export
- implement HTTP API endpoints for:
  - create job
  - list jobs
  - get job detail
  - get stage progress
  - browse samples
  - browse reports
  - download artifacts
- implement React pages for:
  - job launch
  - job list
  - job detail timeline
  - sample inspection
  - report inspection
- implement project-local React components for:
  - progress timeline
  - validation badge group
  - sample comparison table
  - report panels

### Deliverables

- usable CLI
- usable HTTP API
- usable React frontend

### Exit Criteria

- the same job can be created from CLI or API
- frontend can observe job state and inspect results
- no generation rules exist in frontend code

## 8. Phase 6: Hardening and Release Readiness

### Objectives

- make the system safe to iterate on
- ensure reproducibility and observability

### Tasks

- add unit tests for domain logic
- add integration tests for OpenAI and TuGraph adapters
- add rule regression tests with fixed schema fixtures
- implement observability metrics:
  - job timeline
  - per-stage counts
  - error distributions
  - query type coverage
  - difficulty coverage
  - OpenAI latency and failures
  - TuGraph latency and failures
- validate artifact manifests
- add startup and runtime documentation

### Deliverables

- automated test suite
- observability outputs
- operational run documentation

### Exit Criteria

- test suite passes
- observability data is available through reports
- system can be started and used from a clean local setup

## 9. Implementation Order by Module

The preferred module order is:

1. `app/domain/*` typed models
2. `app/orchestrator`
3. `app/storage`
4. `app/integrations/openai`
5. `app/integrations/tugraph`
6. `app/domain/schema`
7. `app/domain/generation`
8. `app/domain/validation`
9. `app/domain/questioning`
10. `app/domain/roundtrip`
11. `app/reports`
12. `app/entrypoints/api`
13. `app/entrypoints/cli`
14. frontend React app

This order ensures the core rule path exists before surfaces are added.

## 10. Milestones

### Milestone A

Foundation and orchestration complete.

Success means:

- backend project boots
- typed models exist
- orchestrator and state machine run with mocked services

### Milestone B

External integrations complete.

Success means:

- OpenAI and TuGraph adapters are callable through stable interfaces

### Milestone C

End-to-end pipeline complete.

Success means:

- one real job runs from schema input to packaged artifacts

### Milestone D

All operator surfaces complete.

Success means:

- CLI, API, and frontend can all drive or observe real jobs

### Milestone E

Release hardening complete.

Success means:

- tests, reports, and runtime docs are in place

## 11. Risks and Mitigations

### Risk 1: Prompt drift causes weak QA quality

Mitigation:

- keep prompts versioned
- enforce round-trip validation
- keep validation stronger than text generation confidence

### Risk 2: TuGraph execution behavior differs from assumptions

Mitigation:

- isolate TuGraph access in `graph_executor`
- normalize runtime errors
- keep runtime validation as a dedicated stage

### Risk 3: Online and offline paths diverge

Mitigation:

- allow only one orchestrator path
- keep entrypoints thin
- use the same `JobRequest` and state machine everywhere

### Risk 4: Frontend grows into a second backend

Mitigation:

- expose only data and commands over API
- keep frontend read-oriented except job launch
- avoid embedding generation logic in React code

## 12. Definition of Done

Version one is done when all of the following are true:

- schema input can be accepted and normalized
- Cypher generation follows the approved rule chain
- five-level validation is enforced
- question generation and round-trip validation work end-to-end
- `seed/silver/gold` outputs are produced
- CLI, API, and frontend all function
- reports and error artifacts are generated
- tests cover the critical rule path

