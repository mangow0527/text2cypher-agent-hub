# Query Plan and Business Stages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a coverage-driven query-plan generation layer, simplify customer-facing progress into four business stages, and persist job-scoped logs/timings for the full pipeline.

**Architecture:** Keep the existing orchestrator and internal stage machine, but insert a deterministic `QueryPlan` layer before candidate generation and add a business-stage aggregation/reporting layer on top of internal stages. The backend remains source-of-truth for timings, logs, and coverage; the frontend becomes a thin view that renders only the four business stages plus progress and durations.

**Tech Stack:** Python, Pydantic, FastAPI, React, TypeScript, unittest

---

### Task 1: Add QueryPlan domain model and coverage scheduling tests

**Files:**
- Modify: `/Users/wangxinhao/muti-agent-offline-system/qa-agent/app/domain/models.py`
- Create: `/Users/wangxinhao/muti-agent-offline-system/qa-agent/tests/test_query_plan_service.py`

- [ ] **Step 1: Write the failing tests**

```python
import unittest

from app.domain.models import CanonicalSchemaSpec, GenerationLimits, OutputConfig
from app.domain.query_plan.service import QueryPlanService


class QueryPlanServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = CanonicalSchemaSpec(
            node_types=["NetworkElement", "Port", "Tunnel"],
            edge_types=["HAS_PORT", "FIBER_SRC"],
            node_properties={
                "NetworkElement": {"id": "string", "vendor": "string"},
                "Port": {"id": "string", "admin_status": "string"},
                "Tunnel": {"id": "string", "latency": "int"},
            },
            edge_constraints={
                "HAS_PORT": [["NetworkElement", "Port"]],
                "FIBER_SRC": [["Port", "Tunnel"]],
            },
            value_catalog={
                "NetworkElement.id": ["ne-1", "ne-2"],
                "Port.admin_status": ["UP", "DOWN"],
                "Tunnel.id": ["tn-1", "tn-2"],
            },
        )

    def test_build_plans_spreads_across_difficulty_and_query_types(self):
        service = QueryPlanService()
        plans = service.build_plans(
            schema=self.schema,
            limits=GenerationLimits(max_skeletons=24, max_candidates_per_skeleton=4, max_variants_per_question=5),
            target_qa_count=8,
            diversity_key="job_001",
        )

        self.assertGreaterEqual(len(plans), 8)
        self.assertGreaterEqual(len({plan.query_type for plan in plans}), 4)
        self.assertGreaterEqual(len({plan.difficulty for plan in plans}), 4)

    def test_build_plans_changes_with_diversity_key(self):
        service = QueryPlanService()
        first = service.build_plans(self.schema, GenerationLimits(), 8, "job_a")
        second = service.build_plans(self.schema, GenerationLimits(), 8, "job_b")

        self.assertNotEqual(
            [(plan.query_type, plan.structure_family, plan.bindings.get("node")) for plan in first[:8]],
            [(plan.query_type, plan.structure_family, plan.bindings.get("node")) for plan in second[:8]],
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/qa-agent && python3 -m unittest tests/test_query_plan_service.py -v`
Expected: FAIL because `app.domain.query_plan.service` and `QueryPlan` do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Add a `QueryPlan` model in `app/domain/models.py` and a `QueryPlanService` in `app/domain/query_plan/service.py` that:
- Builds plans from `QUERY_TYPE_REGISTRY`
- Rotates by `diversity_key`
- Spreads target count across query types and difficulty levels
- Emits schema-grounded bindings only

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/qa-agent && python3 -m unittest tests/test_query_plan_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/wangxinhao/muti-agent-offline-system/qa-agent
git add app/domain/models.py app/domain/query_plan/service.py tests/test_query_plan_service.py
git commit -m "feat: add coverage-driven query plans"
```

### Task 2: Add plan-consistency validation tests and implementation

**Files:**
- Modify: `/Users/wangxinhao/muti-agent-offline-system/qa-agent/app/domain/validation/service.py`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/qa-agent/app/domain/models.py`
- Create: `/Users/wangxinhao/muti-agent-offline-system/qa-agent/app/domain/validation/plan_validator.py`
- Create: `/Users/wangxinhao/muti-agent-offline-system/qa-agent/tests/test_plan_validator.py`

- [ ] **Step 1: Write the failing tests**

```python
import unittest

from app.domain.models import QueryPlan
from app.domain.validation.plan_validator import PlanValidator


class PlanValidatorTests(unittest.TestCase):
    def test_rejects_cypher_missing_limit_required_by_plan(self):
        validator = PlanValidator()
        plan = QueryPlan(
            plan_id="plan_1",
            query_type="SORT_TOPK",
            structure_family="topk_entities",
            difficulty="L4",
            bindings={"node": "NetworkElement", "property": "vendor"},
            required_semantics={"limit": 5, "ordering": True},
            disallowed_constructs=[],
        )
        result = validator.validate(plan, "MATCH (n:NetworkElement) RETURN n ORDER BY n.vendor DESC")
        self.assertFalse(result["ok"])
        self.assertIn("limit", "".join(result["reasons"]).lower())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/qa-agent && python3 -m unittest tests/test_plan_validator.py -v`
Expected: FAIL because `PlanValidator` does not exist.

- [ ] **Step 3: Write minimal implementation**

Implement `PlanValidator` to check:
- Required `LIMIT`
- Required ordering
- Required aggregation / grouping
- Required hop or variable-length hints
- Disallowed constructs

Expose `plan_valid` and `plan_reasons` in validation results.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/qa-agent && python3 -m unittest tests/test_plan_validator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/wangxinhao/muti-agent-offline-system/qa-agent
git add app/domain/models.py app/domain/validation/plan_validator.py app/domain/validation/service.py tests/test_plan_validator.py
git commit -m "feat: validate cypher against query plan"
```

### Task 3: Add business-stage aggregation and job log tests

**Files:**
- Modify: `/Users/wangxinhao/muti-agent-offline-system/qa-agent/app/orchestrator/service.py`
- Create: `/Users/wangxinhao/muti-agent-offline-system/qa-agent/app/storage/job_log_store.py`
- Create: `/Users/wangxinhao/muti-agent-offline-system/qa-agent/tests/test_business_stages.py`

- [ ] **Step 1: Write the failing tests**

```python
import json
import tempfile
import unittest
from pathlib import Path

from app.orchestrator.service import Orchestrator
from app.storage.artifact_store import ArtifactStore
from app.storage.job_log_store import JobLogStore
from app.storage.job_store import JobStore


class BusinessStageTests(unittest.TestCase):
    def test_business_stage_report_contains_four_stages(self):
        # use fake dependencies / stubs to exercise stage aggregation helper
        ...

    def test_job_log_is_written_by_job_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobLogStore(Path(tmp))
            store.append("job_123", "生成Cypher", "info", "started")
            path = Path(tmp) / "jobs" / "job_123.log"
            self.assertTrue(path.exists())
            self.assertIn("生成Cypher", path.read_text(encoding="utf-8"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/qa-agent && python3 -m unittest tests/test_business_stages.py -v`
Expected: FAIL because `JobLogStore` and business-stage aggregation are missing.

- [ ] **Step 3: Write minimal implementation**

Add:
- `JobLogStore.append(job_id, stage, level, message, extra=None)`
- `business_stages` aggregation in job metrics/report:
  - `generate_cypher`
  - `tugraph_validate`
  - `generate_qa`
  - `dispatch_downstream`
- stage durations derived from internal stages and dispatch timings

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/qa-agent && python3 -m unittest tests/test_business_stages.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/wangxinhao/muti-agent-offline-system/qa-agent
git add app/orchestrator/service.py app/storage/job_log_store.py tests/test_business_stages.py
git commit -m "feat: add customer-facing business stages and job logs"
```

### Task 4: Wire QueryPlan into generation pipeline with TDD

**Files:**
- Modify: `/Users/wangxinhao/muti-agent-offline-system/qa-agent/app/domain/generation/service.py`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/qa-agent/app/orchestrator/service.py`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/qa-agent/tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

```python
def test_pipeline_emits_query_plan_backed_candidates(self):
    record = orchestrator.run_job(job_id)
    validated_path = Path(record.artifacts["validated"])
    rows = [json.loads(line) for line in validated_path.read_text().splitlines() if line.strip()]
    assert rows
    assert all("query_plan" in row["candidate"] for row in rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/qa-agent && python3 -m unittest tests/test_pipeline.py -v`
Expected: FAIL because candidates do not yet carry query-plan metadata.

- [ ] **Step 3: Write minimal implementation**

Update generation/orchestrator flow to:
- Build `QueryPlan` list before skeleton creation
- Attach query plan metadata to candidates
- Use query plans to choose bindings and enforce coverage

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/qa-agent && python3 -m unittest tests/test_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/wangxinhao/muti-agent-offline-system/qa-agent
git add app/domain/generation/service.py app/orchestrator/service.py tests/test_pipeline.py
git commit -m "feat: drive candidate generation from query plans"
```

### Task 5: Update frontend to show only four business steps

**Files:**
- Modify: `/Users/wangxinhao/muti-agent-offline-system/qa-agent/frontend/src/components/JobDetail.tsx`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/qa-agent/frontend/src/styles.css`
- Test: Frontend build only

- [ ] **Step 1: Write the failing UI contract assumption**

Document and code against a backend payload shaped like:

```ts
type BusinessStage = {
  key: "generate_cypher" | "tugraph_validate" | "generate_qa" | "dispatch_downstream";
  label: string;
  status: "pending" | "running" | "completed" | "failed" | "skipped";
  duration_ms: number | null;
  message: string;
};
```

- [ ] **Step 2: Run frontend build to capture current incompatibility risk**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/qa-agent/frontend && npm run build`
Expected: either PASS or FAIL; if PASS, proceed because the failing behavior is visual/contractual, not compile-time.

- [ ] **Step 3: Write minimal implementation**

Render:
- One overall progress bar
- Four steps only
- Per-step duration and concise message
- No internal state names

- [ ] **Step 4: Run frontend build**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/qa-agent/frontend && npm run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/wangxinhao/muti-agent-offline-system/qa-agent
git add frontend/src/components/JobDetail.tsx frontend/src/styles.css
git commit -m "feat: simplify job detail to four business stages"
```

### Task 6: Full verification and performance sanity checks

**Files:**
- No code changes required unless verification finds issues

- [ ] **Step 1: Run backend test suite**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/qa-agent && python3 -m unittest tests/test_pipeline.py tests/test_generation_registry.py tests/test_structure_rules.py tests/test_difficulty_service.py tests/test_query_plan_service.py tests/test_plan_validator.py tests/test_business_stages.py -v
```

Expected: PASS

- [ ] **Step 2: Run frontend build**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/qa-agent/frontend && npm run build
```

Expected: PASS

- [ ] **Step 3: Run a targeted performance sanity check**

Measure non-LLM stages by recording business-stage durations on a small job:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/qa-agent && python3 - <<'PY'
from app.orchestrator.service import Orchestrator
from app.domain.models import JobRequest, SchemaSourceConfig, TuGraphSourceConfig, OutputConfig

orc = Orchestrator()
job = orc.create_and_run_job(JobRequest(
    mode="online",
    schema_source=SchemaSourceConfig(type="file", file_path="/Users/wangxinhao/muti-agent-offline-system/qa-agent/schema.json"),
    tugraph_source=TuGraphSourceConfig(type="env"),
    output_config=OutputConfig(target_qa_count=1),
))
print(job.metrics.get("business_stages"))
PY
```

Expected: Sum of non-LLM segments per QA stays within the target or produces evidence for follow-up optimization.

- [ ] **Step 4: Commit any final fixes**

```bash
cd /Users/wangxinhao/muti-agent-offline-system/qa-agent
git add -A
git commit -m "test: verify query-plan pipeline and business stage reporting"
```
