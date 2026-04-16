# Frontend Task Clarity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the task console so users can clearly see active jobs, completion state, download points, and import state without cross-panel busy confusion.

**Architecture:** Keep the existing API surface and data model, but simplify the UI into a light workbench layout with separate busy states, an auto-focused current job panel, and a cleaner Google-inspired visual system. Use polling in the page container so newly created jobs are visible immediately and remain selected through completion.

**Tech Stack:** React, TypeScript, Vite, CSS

---

### Task 1: Split Busy State And Selection Behavior

**Files:**
- Modify: `frontend/src/pages/App.tsx`
- Test/Verify: `frontend/src/pages/App.tsx` behavior via build and live flow

- [ ] Split the single `busy` flag into independent job/import states.
- [ ] Auto-select the newly created job and keep it selected after refresh.
- [ ] Add polling for the selected job until it reaches `completed` or `failed`.

### Task 2: Simplify Information Architecture

**Files:**
- Modify: `frontend/src/components/JobComposer.tsx`
- Modify: `frontend/src/components/JobList.tsx`
- Modify: `frontend/src/components/JobDetail.tsx`
- Modify: `frontend/src/components/ImportComposer.tsx`
- Modify: `frontend/src/components/ImportList.tsx`

- [ ] Turn the top of the page into a simple command strip.
- [ ] Promote the current job summary above the noisy details.
- [ ] Move import UI into a calmer secondary area and ensure it never reflects generation busy state.
- [ ] Reduce task list items to essential status, time, and output count.

### Task 3: Visual Refresh

**Files:**
- Modify: `frontend/src/pages/App.tsx`
- Modify: `frontend/src/styles.css`

- [ ] Replace the dark sci-fi theme with a lighter, restrained Google-like workspace style.
- [ ] Reduce decorative effects and emphasize whitespace, typography, and simple status chips.
- [ ] Make completion/download actions obvious at a glance.

### Task 4: Error Feedback

**Files:**
- Modify: `frontend/src/pages/App.tsx`
- Modify: `frontend/src/components/JobComposer.tsx`
- Modify: `frontend/src/components/ImportComposer.tsx`

- [ ] Surface create-job and import failures directly in the UI.
- [ ] Ensure “no response” scenarios become explicit error messages instead of silent failure.
