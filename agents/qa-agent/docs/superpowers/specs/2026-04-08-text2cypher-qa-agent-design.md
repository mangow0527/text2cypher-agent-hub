# Text2Cypher QA Agent Design

## 1. Overview

This document defines the first implementation design for a local-running `Text2Cypher QA Agent` system.

The system goal is to generate high-quality `question -> Cypher` QA pairs locally while strictly following the generation flow and quality gates defined in [text2cypher_qa_generation_scheme.md](/Users/wangxinhao/muti-agent-offline-system/text2cypher_qa_generation_scheme.md).

The system will:

- accept user-provided graph schema as input
- use OpenAI API as the LLM backend
- connect to user-provided TuGraph with username and password for runtime validation
- support both offline batch generation and online on-demand generation
- provide CLI, HTTP API, and a React frontend
- keep frontend lightweight and custom-built, with the backend as the primary focus

This design defines a single implementation path. Online and offline modes are different entry modes into the same orchestration pipeline, not different business logics.

## 2. Goals

The first version must deliver:

- a local Python backend that programmatically enforces the full generation process
- a unified orchestration pipeline that matches the rule document
- full candidate validation including runtime validation against TuGraph
- structured intermediate and final artifacts
- online and offline task execution
- a React frontend for task launch, progress visibility, and result inspection

## 3. Non-Goals

The first version does not include:

- support for multiple graph database engines
- support for multiple model vendors beyond OpenAI API
- multi-user permissions, collaboration, or tenancy
- distributed worker clusters
- generalized workflow authoring UI
- heavy third-party admin templates or component systems on the frontend

## 4. Architecture

The system uses a `unified orchestration core`.

All generation jobs must pass through one fixed pipeline:

`schema input -> schema normalization -> query taxonomy mapping -> Cypher skeleton generation -> Cypher instantiation -> five-level validation -> Chinese question generation -> round-trip validation -> deduplication and splitting -> artifact packaging`

This pipeline is the executable realization of the rule document and is the only source of business truth.

### 4.1 Architectural Principles

- online and offline generation must share the same orchestration logic
- every stage must read structured upstream artifacts and write structured downstream artifacts
- validation gates must be stronger than model confidence
- LLM calls are controlled generation steps, not autonomous workflow control
- frontend is an operator surface, not a second implementation of generation logic

### 4.2 Major Modules

- `orchestrator`
  - owns the job state machine
  - advances stages in order
  - handles failures, retries, and artifact checkpoints
- `schema_service`
  - converts raw schema input into `CanonicalSchemaSpec`
- `generation_service`
  - creates Cypher skeletons and instantiated Cypher candidates
- `validation_service`
  - performs syntax, schema, type/value, TuGraph runtime, and result sanity validation
- `question_service`
  - generates canonical Chinese questions and paraphrase variants
- `roundtrip_service`
  - re-runs question-to-Cypher checks for consistency
- `artifact_service`
  - writes intermediate artifacts, release files, manifests, and reports
- `entrypoints`
  - CLI
  - HTTP API
  - React frontend

## 5. Runtime Modes

The system has one job model called `GenerationJob`.

### 5.1 Online Mode

Online mode is used for:

- low-volume runs
- quick preview generation
- prompt and rule verification
- inspecting validation outcomes before large jobs

Online mode still runs the same rule pipeline. It differs only in job size, timeout policy, and result presentation.

### 5.2 Offline Mode

Offline mode is used for:

- full batch generation
- formal artifact packaging
- producing `seed/silver/gold` datasets
- generating job reports and error reports

Offline mode also runs the same pipeline and shares the same internal services.

## 6. Technology Decisions

### 6.1 Backend

- implementation language: Python
- project style: service-oriented application with domain modules and integration adapters
- no business logic in CLI or HTTP controllers

### 6.2 Frontend

- implementation language: TypeScript
- framework: React
- component strategy: project-local reusable components only
- frontend scope: task creation, progress tracking, result browsing, and report viewing

### 6.3 External Dependencies

- LLM backend: OpenAI API
- graph runtime validation: TuGraph

## 7. Backend Structure

Recommended structure:

- `app/entrypoints/cli`
- `app/entrypoints/api`
- `app/orchestrator`
- `app/domain/schema`
- `app/domain/generation`
- `app/domain/validation`
- `app/domain/questioning`
- `app/domain/roundtrip`
- `app/integrations/openai`
- `app/integrations/tugraph`
- `app/storage`
- `app/reports`
- `app/frontend_contracts`

### 7.1 Boundary Rules

- domain modules must not directly call OpenAI or TuGraph
- integrations must not encode generation rules
- orchestrator is the only module allowed to advance a job across stages
- entrypoints may assemble requests and return responses, but may not implement generation logic

## 8. Core Data Model

The system must define structured types for all main entities.

### 8.1 `JobRequest`

Contains:

- mode
- schema input
- taxonomy version
- generation limits
- validation config
- model config
- tugraph config
- output config

### 8.2 `CanonicalSchemaSpec`

Represents the normalized schema used by every downstream stage.

### 8.3 `CypherSkeleton`

Represents:

- skeleton id
- query type tags
- pattern template
- slot definitions
- default difficulty floor

### 8.4 `CypherCandidate`

Represents:

- instantiated Cypher
- bound schema items
- bound values
- source skeleton id

### 8.5 `ValidatedSample`

Represents:

- validated Cypher
- validation booleans
- runtime metadata
- result signature

### 8.6 `QASample`

Represents:

- canonical Chinese question
- Chinese variants
- Cypher
- query types
- difficulty
- validation
- roundtrip check
- split

## 9. Job State Machine

All jobs use the following fixed states:

- `created`
- `schema_ready`
- `skeleton_ready`
- `cypher_ready`
- `validated`
- `questions_ready`
- `roundtrip_done`
- `deduped`
- `packaged`
- `completed`
- `failed`

### 9.1 State Transition Rules

- transitions are one-way only
- each transition must record timing and summary metadata
- failures record stage, error code, and error message
- no stage may be skipped

## 10. Generation Pipeline

### 10.1 Schema Normalization

Input:

- user-provided schema

Output:

- `CanonicalSchemaSpec`

Responsibilities:

- normalize node types, edge types, properties, constraints, and value catalogs

### 10.2 Query Taxonomy and Skeleton Creation

Input:

- normalized schema
- fixed taxonomy

Output:

- `CypherSkeleton` list

Responsibilities:

- generate skeletons that map to the predefined query type space

### 10.3 Cypher Instantiation

Input:

- normalized schema
- skeletons

Output:

- `CypherCandidate` list

Responsibilities:

- bind schema elements and legal values
- emit executable candidate Cypher queries

### 10.4 Five-Level Validation

Validation order is fixed:

1. syntax validation
2. schema validation
3. type and value validation
4. TuGraph runtime validation
5. result sanity validation

Only candidates that pass all five levels continue.

### 10.5 Question Generation

Input:

- validated Cypher
- schema summary
- return semantics
- result summary

Output:

- canonical Chinese question
- Chinese paraphrase variants

Rules:

- no added constraints
- no dropped constraints
- no semantic broadening
- no English pivot generation

### 10.6 Round-Trip Validation

Input:

- generated question
- schema summary

Output:

- consistency status

Rules:

- use question-to-Cypher regeneration
- compare normalized Cypher and semantic equivalence
- reject or downgrade drifted samples

### 10.7 Deduplication and Splitting

Deduplication dimensions:

- normalized Cypher
- semantic question similarity
- result signature equivalence

Output splits:

- `seed`
- `silver`
- `gold`

## 11. Model Access Design

All LLM access goes through `model_gateway`.

Allowed operations:

- structured generation
- text generation
- consistency judgment

Prompt templates must be managed as versioned assets, not embedded ad hoc across modules.

Prompt groups:

- schema-to-slot support
- canonical question generation
- variant generation
- question-Cypher consistency review
- round-trip text-to-Cypher

## 12. TuGraph Integration Design

All graph execution goes through `graph_executor`.

Responsibilities:

- connection management
- authenticated execution
- query result retrieval
- execution error normalization
- execution timing capture

`validation_service` consumes standardized runtime results from `graph_executor` and applies rule-level judgments.

## 13. Artifact Design

The system must store both intermediate and final artifacts.

### 13.1 Intermediate Artifacts

- `skeletons.jsonl`
- `candidates.jsonl`
- `validated.jsonl`
- `qa.jsonl`
- `deduped.jsonl`

### 13.2 Final Artifacts

- `seed.jsonl`
- `silver.jsonl`
- `gold.jsonl`
- `manifest.json`
- `error_report.jsonl`
- `job_report.json`

### 13.3 Directory Shape

```text
artifacts/
  schema/
  taxonomy/
  skeletons/
  instantiated/
  validated/
  qa/
  releases/
  reports/
```

## 14. API and CLI Responsibilities

### 14.1 CLI

CLI must support:

- schema import
- job creation
- job execution
- job status query
- artifact export

CLI is the formal batch interface and is designed for automation and repeatability.

### 14.2 HTTP API

HTTP API must support:

- create job
- get job list
- get job detail
- get stage progress
- browse samples
- download artifacts
- browse reports

### 14.3 React Frontend

Frontend must support:

- launching online and offline jobs
- viewing job timeline and stage progress
- browsing generated QA samples
- reviewing validation and round-trip outcomes
- viewing reports and distributions

The frontend must remain thin and must not embed generation logic.

## 15. Error Model

All failures must be normalized under fixed error codes:

- `SCHEMA_PARSE_ERROR`
- `SCHEMA_VALIDATION_ERROR`
- `SKELETON_BUILD_ERROR`
- `CYPHER_INSTANTIATION_ERROR`
- `SYNTAX_ERROR`
- `TYPE_VALUE_ERROR`
- `TUGRAPH_RUNTIME_ERROR`
- `RESULT_SANITY_ERROR`
- `QUESTION_GENERATION_ERROR`
- `QUESTION_DRIFT`
- `ROUNDTRIP_FAILED`
- `DEDUP_CONFLICT`
- `PACKAGING_ERROR`

## 16. Observability

The first version must provide:

- job timeline
- per-stage input and pass counts
- error code distribution
- query type coverage
- difficulty distribution
- OpenAI call count, failure count, and average latency
- TuGraph execution count, failure rate, and average latency

These metrics must be visible in reports and consumable by the frontend.

## 17. Testing Strategy

### 17.1 Unit Tests

Cover:

- schema normalization
- taxonomy mapping
- skeleton and candidate modeling
- error code mapping
- difficulty tagging
- deduplication and split logic

### 17.2 Integration Tests

Cover:

- OpenAI gateway response parsing
- TuGraph runtime adapter
- orchestrator stage transitions
- CLI and API orchestration entrypoints

### 17.3 Rule Regression Tests

Cover:

- fixed schema fixtures
- expected stage ordering
- rejection of invalid or drifted samples
- stable output structure for seed/silver/gold

### 17.4 Manual Review Hooks

The system must expose easy sample review points for complex, high-risk query classes.

## 18. First-Version Scope

Version one is complete when it can:

- ingest provided schema
- call OpenAI API through a unified gateway
- connect to TuGraph for runtime validation
- run the full generation rule chain
- expose both online and offline execution
- generate structured intermediate artifacts
- generate `seed/silver/gold`
- expose reports and errors through CLI, API, and frontend

## 19. Open Questions Resolved

The following design decisions are fixed for version one:

- rule source: `text2cypher_qa_generation_scheme.md`
- backend language: Python
- frontend framework: React
- model provider: OpenAI API
- graph runtime validation target: TuGraph
- modes: both online and offline
- frontend priority: lightweight operator surface, backend-first system

