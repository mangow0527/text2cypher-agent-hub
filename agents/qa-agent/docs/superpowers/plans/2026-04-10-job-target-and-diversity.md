# Job Target And Diversity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each job request generate an exact `1-50` final QA samples, avoid repeating previously released QA as much as possible, and dispatch only that final batch in order.

**Architecture:** Add a `target_qa_count` request field, keep the existing generation/validation pipeline unchanged, and insert a new final-selection layer that reads historical released QA, filters exact repeats, and greedily samples a diverse final batch. The dispatch side-chain stays non-blocking and continues to run only after the final release batch is ready.

**Tech Stack:** Python, Pydantic, FastAPI, React, unittest

---

### Task 1: Request Surface For Target Count

**Files:**
- Modify: `app/domain/models.py`
- Modify: `app/entrypoints/api/main.py`
- Modify: `app/entrypoints/cli/main.py`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/JobComposer.tsx`
- Test: `tests/test_pipeline.py`

- [ ] Add `target_qa_count` with bounds `1..50` to the job request model and pass it through API, CLI, and frontend payloads.
- [ ] Add failing tests that assert release output respects the requested final sample count.

### Task 2: Historical Repeat Avoidance

**Files:**
- Create: `app/storage/release_history_store.py`
- Modify: `app/orchestrator/service.py`
- Modify: `app/domain/questioning/service.py`
- Test: `tests/test_pipeline.py`

- [ ] Add a history reader over prior `artifacts/releases/*.jsonl`.
- [ ] Add tests that verify a new job does not emit a previously released question/cypher when enough alternatives exist.
- [ ] Implement exact-repeat filtering on normalized question and normalized cypher.

### Task 3: Diverse Final Batch Selection

**Files:**
- Modify: `app/orchestrator/service.py`
- Modify: `app/reports/builder.py`
- Test: `tests/test_pipeline.py`

- [ ] Add tests that verify the final batch is capped to `target_qa_count` and prefers a mix of `query_type`, `structure_family`, and `difficulty`.
- [ ] Implement a greedy diversity sampler on the post-roundtrip QA pool.
- [ ] Include selection diagnostics in the report so the frontend can explain how many samples were skipped for history or exact-duplicate reasons.

### Task 4: Final Batch Dispatch And UX

**Files:**
- Modify: `app/orchestrator/service.py`
- Modify: `frontend/src/components/JobDetail.tsx`
- Test: `tests/test_pipeline.py`

- [ ] Add tests that assert dispatch only receives the final selected release batch.
- [ ] Keep dispatch sequential and non-blocking to the main release flow.
- [ ] Expose requested count and final count in the report/UI.
