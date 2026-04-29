# Full Schema, Typed Few-Shots, and Knowledge Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build full-schema prompt packaging, TuGraph/Cypher typed few-shot selection, and a frontend knowledge editor for viewing and modifying editable knowledge documents.

**Architecture:** Keep knowledge files on disk and extend existing services. `KnowledgeStore` owns document metadata/read/write, `schema_formatter` owns full schema rendering, `KnowledgeRetriever` owns deterministic query type inference and few-shot selection, the FastAPI entrypoint exposes document APIs, and the React app adds a compact editor workspace.

**Tech Stack:** Python 3, FastAPI, Pydantic, unittest, React, TypeScript, Vite.

---

### Task 1: Full Schema Formatting and Typed Few-Shot Retrieval

**Files:**
- Modify: `backend/app/domain/knowledge/schema_formatter.py`
- Modify: `backend/app/domain/knowledge/retriever.py`
- Modify: `backend/knowledge/few_shot.md`
- Test: `backend/tests/test_retriever.py`
- Test: `backend/tests/test_prompt_service.py`

- [ ] **Step 1: Write failing backend tests**

Add tests that assert full property metadata is included in schema context and that aggregation/path/order questions select typed few-shots.

Run: `cd knowledge-agent/backend && python3 -m unittest tests.test_retriever tests.test_prompt_service -v`

Expected before implementation: failures mentioning missing property metadata or missing typed examples.

- [ ] **Step 2: Implement full schema formatter**

Render list-based schema with label/type/description/primary, property metadata, edge constraints, and edge properties. Keep compact dict schema support.

- [ ] **Step 3: Implement deterministic typed few-shot selection**

Parse `[types: A, B]` metadata, infer query types from Chinese/English question text, select matching examples plus `GENERAL`, and keep legacy untyped examples as `GENERAL`.

- [ ] **Step 4: Update seed few-shot file**

Add `[types: ...]` tags to existing examples and add minimal examples for aggregation, order/limit, and path traversal.

- [ ] **Step 5: Verify backend retrieval and prompt tests**

Run: `cd knowledge-agent/backend && python3 -m unittest tests.test_retriever tests.test_prompt_service -v`

Expected after implementation: all tests pass.

### Task 2: Knowledge Document Store and API

**Files:**
- Modify: `backend/app/storage/knowledge_store.py`
- Modify: `backend/app/domain/models.py`
- Modify: `backend/app/entrypoints/api/main.py`
- Test: `backend/tests/test_knowledge_store.py`
- Test: `backend/tests/test_api_contracts.py`

- [ ] **Step 1: Write failing store and API tests**

Add tests for listing documents, reading editable docs, saving editable docs with history, and rejecting schema writes.

Run: `cd knowledge-agent/backend && python3 -m unittest tests.test_knowledge_store tests.test_api_contracts -v`

Expected before implementation: failures for missing methods/endpoints.

- [ ] **Step 2: Add document metadata to KnowledgeStore**

Expose `list_documents`, `read_document`, and `save_document`. Mark `schema` read-only and map editable document types to existing Markdown filenames.

- [ ] **Step 3: Add Pydantic request/response models**

Add document summary, document detail, document update request, and document update response models.

- [ ] **Step 4: Add FastAPI document endpoints**

Add `GET /api/knowledge/documents`, `GET /api/knowledge/documents/{doc_type}`, and `PUT /api/knowledge/documents/{doc_type}`.

- [ ] **Step 5: Verify document API tests**

Run: `cd knowledge-agent/backend && python3 -m unittest tests.test_knowledge_store tests.test_api_contracts -v`

Expected after implementation: all tests pass.

### Task 3: Frontend Knowledge Editor

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Extend API client types and functions**

Add `KnowledgeDocumentSummary`, `KnowledgeDocumentDetail`, `listKnowledgeDocuments`, `fetchKnowledgeDocument`, and `saveKnowledgeDocument`.

- [ ] **Step 2: Add editor state and load behavior**

Load document summaries on mount, select a default editable document, fetch content on selection, and show read-only schema content without save controls.

- [ ] **Step 3: Add editor UI**

Add a third workspace section with document selector, metadata, textarea/viewer, dirty state, save button, and inline errors.

- [ ] **Step 4: Style editor as an operational workspace**

Use compact panels, clear selected states, fixed-height editor area, and no schema edit affordance.

- [ ] **Step 5: Verify frontend build**

Run: `cd knowledge-agent/frontend && npm run build`

Expected after implementation: TypeScript and Vite build pass.

### Task 4: Final Verification

**Files:**
- Verify all modified backend and frontend files.

- [ ] **Step 1: Run full backend tests**

Run: `cd knowledge-agent/backend && python3 -m unittest discover tests -v`

Expected: all backend tests pass.

- [ ] **Step 2: Run frontend build**

Run: `cd knowledge-agent/frontend && npm run build`

Expected: build succeeds.

- [ ] **Step 3: Inspect git diff**

Run: `git -C knowledge-agent diff --stat`

Expected: changes are limited to the planned backend, frontend, knowledge, and docs files.
