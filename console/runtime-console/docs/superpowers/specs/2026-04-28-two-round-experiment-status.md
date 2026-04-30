# Two-Round Experiment Status Note

**Conclusion Date:** `2026-04-28`  
**Scope:**  
- Round 1 formal experiment: `2026-04-27-cgs-baseline-freeze-v1`  
- Round 2 formal experiment: `2026-04-28-cgs-baseline-freeze-v2-10perlevel`

---

## Conclusion

As of this verification snapshot, **neither of the two experiments has fully completed**.

- **Round 1 (`v1`)** is still **in progress**
- **Round 2 (`v2-10perlevel`)** is **prepared and frozen**, but **has not started executing**

This document is a **status note**, not a final experiment conclusion.

---

## Round 1 Status

### Current state

Round 1 is still running through a resume runner:

- Active process:
  - `python3 /tmp/resume_formal_experiment.py`

This means Round 1 is not complete yet.

### Completed portions

The following formal rounds already have completed round summaries:

- `L1`
  - Evidence: `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/rounds/round-001-L1/summary.json`
  - Current summary:
    - `total = 5`
    - `passed = 5`
    - `failed = 0`
    - `pass_rate = 1.0`

- `L2`
  - Evidence: `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/rounds/round-002-L2/summary.json`
  - Current summary:
    - `total = 5`
    - `passed = 5`
    - `failed = 0`
    - `pass_rate = 1.0`

### Partial progress after resume

Round 1 has entered `L3`, but `L3-L8` are not complete.

Current index-derived progress:

- unique completed formal rows: `12`
- current round distribution:
  - `round-001-L1 = 5`
  - `round-002-L2 = 5`
  - `round-003-L3 = 2`

Evidence:

- `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/indexes/qa_index.jsonl`

### Observed interruption pattern

The resumed run has safely converted at least one blocking generator timeout into a per-sample failure artifact instead of crashing the whole experiment.

Example:

- Sample:
  - `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/rounds/round-003-L3/qa/exp_20260428_L3_02_249806`
- Current recorded state:
  - `failure_stage = generator_request_failed`
- Evidence files:
  - `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/rounds/round-003-L3/qa/exp_20260428_L3_02_249806/testing_result.json`
  - `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/rounds/round-003-L3/qa/exp_20260428_L3_02_249806/cgs_result.json`
  - `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/rounds/round-003-L3/qa/exp_20260428_L3_02_249806/timing.json`

This confirms the resume logic is advancing the experiment without letting a single `8000` timeout terminate the whole run.

---

## Round 2 Status

### Current state

Round 2 has been **prepared**, but **no formal execution has started yet**.

There are currently:

- no round summary files for `round-*`
- no formal result rows in the experiment index
- no top-level `experiment_summary.md`

Evidence:

- `/root/multi-agent/experiment_runs/2026-04-28-cgs-baseline-freeze-v2-10perlevel/indexes/qa_index.jsonl`
- `/root/multi-agent/experiment_runs/2026-04-28-cgs-baseline-freeze-v2-10perlevel/summaries/experiment_summary.md`

At the time of verification:

- `formal_rows = 0`
- `uniq_formal_rows = 0`

So Round 2 has not begun producing formal results.

### What is ready

Round 2’s formal sample set has already been frozen:

- Evidence:
  - `/root/multi-agent/experiment_runs/2026-04-28-cgs-baseline-freeze-v2-10perlevel/samples/final_sample_set_manifest.json`

Current manifest state:

- `status = frozen`
- `target_per_level = 10`
- `target_final_count = 80`
- `actual_final_count = 80`
- counts:
  - `L1-L8 = 10 each`

This means Round 2 is **ready to run**, but it has not yet produced experiment output.

---

## Runtime Queueing State

Round 2 is currently staged behind Round 1 through a queue launcher:

- queue script process:
  - `bash /tmp/queue_v2_after_v1.sh`

This means Round 2 is waiting for Round 1 to finish before starting.

Because the queue is only a launcher and Round 2 has not yet created formal results, Round 2 must still be classified as **not completed**.

---

## Supporting Evidence

### Round 1

- Final sample set manifest:
  - `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/samples/final_sample_set_manifest.json`
- Round summaries:
  - `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/rounds/round-001-L1/summary.json`
  - `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/rounds/round-002-L2/summary.json`
- Current formal index:
  - `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/indexes/qa_index.jsonl`
- Resume-run runtime log:
  - `/root/multi-agent/experiment_runs/2026-04-27-cgs-baseline-freeze-v1/runtime/resume_run.log`

### Round 2

- Final sample set manifest:
  - `/root/multi-agent/experiment_runs/2026-04-28-cgs-baseline-freeze-v2-10perlevel/samples/final_sample_set_manifest.json`
- Queue launcher log:
  - `/root/multi-agent/experiment_runs/2026-04-28-cgs-baseline-freeze-v2-10perlevel/runtime/queue_launcher.log`

---

## Final Statement

The current verified status is:

- **Round 1:** not completed, currently running
- **Round 2:** not completed, frozen and queued, but not yet executing

Therefore, **the two-round experiment sequence as a whole is not complete yet**.
