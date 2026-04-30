# Cypher Generator Baseline Experiment Stage Conclusion

**Experiment ID:** `2026-04-27-cgs-baseline-freeze-v1`

**Conclusion Date:** `2026-04-28`

**Conclusion Type:** `Stage conclusion, not final experiment conclusion`

---

## Conclusion

The formal experiment has **started and is progressing**, but it has **not completed yet**. As of this conclusion snapshot:

- the experiment environment is frozen and healthy
- the candidate pool has been generated and the formal sample set has been frozen
- the formal run is still active
- `L1` and `L2` are fully completed and both currently show `5/5 passed`
- the experiment has started writing `L3`, but the later rounds `L3-L8` have not finished

Because the formal experiment has not finished, this document does **not** provide a final capability-boundary conclusion for `L1-L8`. It only provides the verified current state and the evidence supporting that state.

---

## Evidence Summary

### 1. Environment and readiness were completed before the formal run

- Health snapshot exists and all core services were healthy before the formal run:
  - Evidence: `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/environment/health.pre_formal_run.json`
- Repair apply was blocked earlier as part of experiment setup:
  - Evidence: `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/environment/repair_apply_block_verification.json`

### 2. Candidate pool generation completed

- Candidate pool manifest shows the pool is ready:
  - Evidence: `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/samples/sample_set_manifest.json`
- Current manifest state:
  - `status = candidate_pool_ready`
  - `target_candidate_count = 120`
  - `requested_per_level = 15`
  - actual counts are `15` for each of `L1-L8`

### 3. Formal sample set freezing completed

- Formal sample set manifest shows the final set is frozen:
  - Evidence: `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/samples/final_sample_set_manifest.json`
- Current manifest state:
  - `status = frozen`
  - `actual_final_count = 40`
  - counts are `5` for each of `L1-L8`
  - selection policy recorded as:
    - `difficulty -> query_type -> structure_family -> semantic_markers`

### 4. The formal experiment runner is still active

- A live formal-run process still exists:
  - Evidence: `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/summaries/status_snapshot_2026-04-28.json`
- Current recorded process:
  - `python3 /tmp/run_formal_experiment.py`

### 5. Completed formal rounds so far

- `L1` round summary exists and is complete:
  - Evidence: `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/rounds/round-001-L1/summary.json`
  - Result:
    - `total = 5`
    - `passed = 5`
    - `failed = 0`
    - `pass_rate = 1.0`

- `L2` round summary exists and is complete:
  - Evidence: `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/rounds/round-002-L2/summary.json`
  - Result:
    - `total = 5`
    - `passed = 5`
    - `failed = 0`
    - `pass_rate = 1.0`

### 6. Later rounds are not yet complete

- At the time of the snapshot, only two round summary files existed:
  - `round-001-L1/summary.json`
  - `round-002-L2/summary.json`
- Evidence:
  - `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/summaries/status_snapshot_2026-04-28.json`

### 7. The formal-run index confirms partial progress rather than completion

- Formal-run index counts at snapshot time:
  - `formal_rows = 16`
  - `uniq_formal_rows = 11`
  - unique round distribution:
    - `round-001-L1 = 5`
    - `round-002-L2 = 5`
    - `round-003-L3 = 1`
- Evidence:
  - `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/indexes/qa_index.jsonl`
  - `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/summaries/status_snapshot_2026-04-28.json`

This shows the run has entered `L3`, but the experiment is still mid-flight and not all `40` formal samples have reached stable completion yet.

---

## Verified Interpretation

The current experiment state supports the following conclusions:

1. The experiment setup phase was successful enough to launch the formal run.
2. The formal run is operational and has already completed `L1` and `L2`.
3. `L1` and `L2` currently appear stable in this run, with `5/5 passed` each.
4. The overall experiment is still incomplete because the runner is active and later rounds have not finished.
5. Any statement about the full `L1-L8` capability boundary would be premature at this stage.

---

## Not Yet Safe To Conclude

At this stage, the evidence is **not sufficient** to conclude:

- the final stable difficulty boundary of `cypher-generator-agent`
- aggregate pass rates across all `L1-L8`
- the first clearly unstable difficulty level
- final failure-stage distribution for the full experiment

Those conclusions require the formal run to finish and the final experiment index to be deduplicated and summarized.

---

## Recommended Next Action

Wait for the formal experiment runner to finish, then produce a **final experiment conclusion** based on:

- all `round-001-L1` through `round-008-L8` summary files
- the final deduplicated `qa_index`
- the final top-level experiment summary
- the archived per-sample evidence and timing files

At that point, the experiment can support a final conclusion about:

- stable levels
- partially stable levels
- unstable levels
- dominant failure stages
- major timing bottlenecks

---

## Primary Evidence Files

- `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/summaries/status_snapshot_2026-04-28.json`
- `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/environment/health.pre_formal_run.json`
- `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/environment/repair_apply_block_verification.json`
- `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/samples/sample_set_manifest.json`
- `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/samples/final_sample_set_manifest.json`
- `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/rounds/round-001-L1/summary.json`
- `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/rounds/round-002-L2/summary.json`
- `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/indexes/qa_index.jsonl`
