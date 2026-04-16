# Job Redispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual redispatch action for any completed job so its final QA batch can be resent to downstream services from both the backend API and the frontend UI.

**Architecture:** Keep the existing automatic dispatch on job completion, then add a separate redispatch path that reads the job's final release artifact, resends that batch sequentially, and appends a new dispatch-history record into the job metadata. Frontend consumes the same updated job record and renders the latest dispatch plus the redispatch history with a dedicated button.

**Tech Stack:** Python, FastAPI, React, TypeScript, unittest

---

### Task 1: Redispatch Backend Path

**Files:**
- Modify: `app/integrations/qa_dispatcher.py`
- Modify: `app/orchestrator/service.py`
- Modify: `app/entrypoints/api/main.py`
- Test: `tests/test_pipeline.py`

- [ ] Add failing tests for redispatching a completed job and recording dispatch history.
- [ ] Implement redispatch by reading the job's `releases` artifact and resending only that final batch.
- [ ] Expose `POST /jobs/{job_id}/dispatch`.

### Task 2: Frontend Trigger And Feedback

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/pages/App.tsx`
- Modify: `frontend/src/components/JobDetail.tsx`

- [ ] Add frontend API call for job redispatch.
- [ ] Add a redispatch button in job detail with loading state.
- [ ] Render dispatch history and refresh the selected job after redispatch.
