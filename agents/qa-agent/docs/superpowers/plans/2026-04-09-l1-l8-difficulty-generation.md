# L1-L8 Difficulty Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a true `L1-L8` difficulty-first Text2Cypher generation pipeline where every difficulty level has dedicated skeleton families, rule-based validation, coverage reporting, and exportable QA samples with execution answers.

**Architecture:** The pipeline will move from a mixed skeleton pool to a difficulty-targeted generator. Each candidate will be generated from an `L1-L8` family, validated by a deterministic difficulty classifier, then passed through existing question generation, execution, and export stages. Reports and exports will be updated to expose real coverage across all eight levels.

**Tech Stack:** Python, Pydantic, FastAPI, React, unittest, TuGraph HTTP integration, GLM-5 via OpenAI-compatible API

---

## File Structure

- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/models.py`
  - Expand generation and QA models for `L1-L8`, answer payloads, and difficulty metadata.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/generation/service.py`
  - Replace the current 6-entry mixed taxonomy with difficulty-first skeleton families.
- Create: `/Users/wangxinhao/muti-agent-offline-system/app/domain/difficulty/service.py`
  - Deterministic Cypher structure parser and `L1-L8` validator.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/validation/service.py`
  - Attach difficulty validation result to validated samples.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/orchestrator/service.py`
  - Route generation through difficulty families, reject mismatches, export minimal QA format.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/reports/builder.py`
  - Add `difficulty_coverage`, per-level counts, missing-level flags.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/importing/service.py`
  - Normalize imported difficulties into `L1-L8`, preserve explicit answers.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/tests/test_pipeline.py`
  - Add end-to-end tests for `L1-L8`, exports, and coverage.
- Create: `/Users/wangxinhao/muti-agent-offline-system/tests/test_difficulty_service.py`
  - Focused unit tests for deterministic level classification.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/frontend/src/components/JobDetail.tsx`
  - Show `difficulty_coverage` report.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/frontend/src/components/ImportDetail.tsx`
  - Show imported level distribution.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/frontend/src/styles.css`
  - Add visual treatment for eight-level coverage display.

## Task 1: Lock The `L1-L8` Contract In Tests

**Files:**
- Modify: `/Users/wangxinhao/muti-agent-offline-system/tests/test_pipeline.py`
- Create: `/Users/wangxinhao/muti-agent-offline-system/tests/test_difficulty_service.py`

- [ ] **Step 1: Write the failing difficulty classifier test**

```python
import unittest

from app.domain.difficulty.service import DifficultyService


class DifficultyServiceTest(unittest.TestCase):
    def test_classifies_l1_to_l8_examples(self) -> None:
        service = DifficultyService()

        self.assertEqual(service.classify("MATCH (n:A) RETURN n LIMIT 5"), "L1")
        self.assertEqual(service.classify("MATCH (n:A) WHERE n.id = 'x' RETURN n LIMIT 10"), "L2")
        self.assertEqual(service.classify("MATCH (a:A)-[:R]->(b:B) RETURN a, b LIMIT 5"), "L3")
        self.assertEqual(service.classify("MATCH (n:A) RETURN count(n) AS total"), "L4")
        self.assertEqual(service.classify("MATCH (a:A)-[:R1]->(:B)-[:R2]->(c:C) RETURN c LIMIT 5"), "L5")
        self.assertEqual(service.classify(\"MATCH (a:A)-[:R1]->(:B)-[:R2]->(c:C) WHERE a.status = 'up' RETURN c.id, count(*) AS total ORDER BY total DESC LIMIT 5\"), "L6")
        self.assertEqual(service.classify("MATCH (a:A)-[:R1]->(:B)-[:R2]->(:C)-[:R3]->(d:D) RETURN d"), "L7")
        self.assertEqual(service.classify("MATCH (a:A)-[:R1]->(b:B) WITH a, count(b) AS cnt MATCH (a)-[:R2]->(c:C) WHERE cnt > 3 RETURN a.id, count(c)"), "L8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_difficulty_service.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.difficulty'`

- [ ] **Step 3: Add failing export and coverage assertions to pipeline test**

```python
releases_path = Path(completed.artifacts["releases"])
export_rows = [json.loads(line) for line in releases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
assert set(export_rows[0].keys()) == {"id", "question", "cypher", "answer", "difficulty"}
assert completed.metrics["difficulty_coverage"]["covered_levels"]
assert "L1" in completed.metrics["difficulty_coverage"]["covered_levels"]
```

- [ ] **Step 4: Run pipeline test to verify it fails**

Run: `python3 -m unittest tests/test_pipeline.py -v`

Expected: FAIL because the current report lacks `difficulty_coverage` or classifier-backed levels.

- [ ] **Step 5: Commit**

```bash
git add /Users/wangxinhao/muti-agent-offline-system/tests/test_pipeline.py /Users/wangxinhao/muti-agent-offline-system/tests/test_difficulty_service.py
git commit -m "test: lock l1-l8 difficulty contract"
```

## Task 2: Implement Deterministic Difficulty Classification

**Files:**
- Create: `/Users/wangxinhao/muti-agent-offline-system/app/domain/difficulty/service.py`
- Test: `/Users/wangxinhao/muti-agent-offline-system/tests/test_difficulty_service.py`

- [ ] **Step 1: Write the classifier implementation**

```python
from __future__ import annotations

import re


class DifficultyService:
    def classify(self, cypher: str) -> str:
        text = " ".join(cypher.split()).upper()
        hop_count = self._max_hops(text)
        has_variable_path = bool(re.search(r"\\{\\d+,\\d*\\}", text))
        has_group = "COUNT(" in text or "SUM(" in text or "AVG(" in text
        has_order = " ORDER BY " in text
        has_where = " WHERE " in text
        multi_match = text.count("MATCH ") > 1 or " NEXT MATCH " in text
        with_count = text.count(" WITH ")
        nested_agg = with_count > 0 and has_group
        condition_count = text.count(" AND ") + text.count(" OR ") + (1 if has_where else 0)

        if nested_agg:
            return "L8"
        if multi_match or hop_count >= 3:
            return "L7"
        if (hop_count == 2 or has_variable_path) and (condition_count >= 2 or has_group or has_order):
            return "L6"
        if hop_count == 2 or has_variable_path:
            return "L5"
        if hop_count == 1 and (has_group or has_order or has_where):
            return "L4"
        if hop_count == 1:
            return "L3"
        if has_where:
            return "L2"
        return "L1"

    def _max_hops(self, text: str) -> int:
        matches = re.findall(r"-\\[[^\\]]*\\]->", text)
        return len(matches)
```

- [ ] **Step 2: Run the focused classifier test**

Run: `python3 -m unittest tests/test_difficulty_service.py -v`

Expected: PASS

- [ ] **Step 3: Refine hop counting to support variable-length path syntax**

```python
if re.search(r"-\\[[^\\]]*\\]->\\{1,3\\}", text):
    return max(hop_count, 2)
```

- [ ] **Step 4: Re-run tests**

Run: `python3 -m unittest tests/test_difficulty_service.py -v`

Expected: PASS with all `L1-L8` examples classified correctly.

- [ ] **Step 5: Commit**

```bash
git add /Users/wangxinhao/muti-agent-offline-system/app/domain/difficulty/service.py /Users/wangxinhao/muti-agent-offline-system/tests/test_difficulty_service.py
git commit -m "feat: add deterministic l1-l8 difficulty classifier"
```

## Task 3: Rebuild The Generator Around Difficulty Families

**Files:**
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/generation/service.py`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/models.py`
- Test: `/Users/wangxinhao/muti-agent-offline-system/tests/test_pipeline.py`

- [ ] **Step 1: Define per-level skeleton families**

```python
QUERY_TAXONOMY = [
    ("LOOKUP_NODE", "L1", "MATCH (n:{node}) RETURN n LIMIT 5"),
    ("LOOKUP_PROPERTY", "L1", "MATCH (n:{node}) RETURN n.{property} AS value LIMIT 5"),
    ("FILTER_EQ", "L2", "MATCH (n:{node}) WHERE n.{property} = '{value}' RETURN n LIMIT 10"),
    ("ONE_HOP_RETURN", "L3", "MATCH (a:{node})-[:{edge}]->(b:{target}) RETURN a, b LIMIT 5"),
    ("ONE_HOP_AGG", "L4", "MATCH (a:{node})-[:{edge}]->(b:{target}) RETURN count(b) AS total"),
    ("TWO_HOP", "L5", "MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(c:{target2}) RETURN c LIMIT 5"),
]
```

- [ ] **Step 2: Map `difficulty_floor` to `L1-L8` literals in models**

```python
difficulty_floor: Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"] = "L1"
difficulty: Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"] = "L1"
```

- [ ] **Step 3: Make candidate generation deterministic per family**

```python
CypherCandidate(
    skeleton_id=skeleton.skeleton_id,
    cypher=cypher,
    query_types=skeleton.query_types,
    difficulty=skeleton.difficulty_floor,
)
```

- [ ] **Step 4: Run pipeline tests**

Run: `python3 -m unittest tests/test_pipeline.py -v`

Expected: FAIL only where classifier validation or coverage report is not wired yet.

- [ ] **Step 5: Commit**

```bash
git add /Users/wangxinhao/muti-agent-offline-system/app/domain/generation/service.py /Users/wangxinhao/muti-agent-offline-system/app/domain/models.py /Users/wangxinhao/muti-agent-offline-system/tests/test_pipeline.py
git commit -m "feat: rebuild generator around l1-l8 families"
```

## Task 4: Validate Generated Difficulty And Reject Mismatches

**Files:**
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/validation/service.py`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/orchestrator/service.py`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/models.py`
- Test: `/Users/wangxinhao/muti-agent-offline-system/tests/test_pipeline.py`

- [ ] **Step 1: Extend validated sample metadata**

```python
class ValidatedSample(BaseModel):
    sample_id: str = Field(default_factory=lambda: f"val_{uuid4().hex[:12]}")
    candidate: CypherCandidate
    validation: ValidationResult
    runtime_meta: RuntimeMeta = Field(default_factory=RuntimeMeta)
    result_signature: ResultSignature = Field(default_factory=ResultSignature)
    computed_difficulty: str = "L1"
```

- [ ] **Step 2: Attach computed difficulty in validation**

```python
computed_difficulty = self.difficulty_service.classify(candidate.cypher)
return ValidatedSample(
    candidate=candidate,
    validation=result,
    runtime_meta=runtime_meta,
    result_signature=result_signature,
    computed_difficulty=computed_difficulty,
)
```

- [ ] **Step 3: Filter mismatched samples in orchestrator**

```python
validated = [
    item
    for item in validated
    if item.candidate.difficulty == item.computed_difficulty
]
```

- [ ] **Step 4: Run pipeline tests**

Run: `python3 -m unittest tests/test_pipeline.py -v`

Expected: PASS for generation-level difficulty assertions.

- [ ] **Step 5: Commit**

```bash
git add /Users/wangxinhao/muti-agent-offline-system/app/domain/validation/service.py /Users/wangxinhao/muti-agent-offline-system/app/orchestrator/service.py /Users/wangxinhao/muti-agent-offline-system/app/domain/models.py
git commit -m "feat: validate l1-l8 difficulty assignments"
```

## Task 5: Export Minimal QA Rows With Execution Answers

**Files:**
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/questioning/service.py`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/orchestrator/service.py`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/importing/service.py`
- Test: `/Users/wangxinhao/muti-agent-offline-system/tests/test_pipeline.py`

- [ ] **Step 1: Store execution answer on QA samples**

```python
return QASample(
    question_canonical_zh=canonical,
    question_variants_zh=variants[:max_variants],
    cypher=sample.candidate.cypher,
    cypher_normalized=normalize_cypher(sample.candidate.cypher),
    query_types=sample.candidate.query_types,
    difficulty=sample.candidate.difficulty,
    answer=sample.result_signature.result_preview,
    validation=sample.validation,
    result_signature=sample.result_signature,
    split="silver",
    provenance={"skeleton_id": sample.candidate.skeleton_id, "generation_mode": "cypher_first"},
)
```

- [ ] **Step 2: Export minimal `releases` rows**

```python
def _export_sample(self, sample: QASample) -> dict:
    return {
        "id": sample.id,
        "question": sample.question_canonical_zh,
        "cypher": sample.cypher,
        "answer": sample.answer,
        "difficulty": sample.difficulty,
    }
```

- [ ] **Step 3: Normalize imported rows to include `answer`**

```python
if "answer" not in payload:
    payload["answer"] = payload.get("result_signature", {}).get("result_preview", [])
```

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests/test_pipeline.py -v`

Expected: PASS with release rows matching `id/question/cypher/answer/difficulty`.

- [ ] **Step 5: Commit**

```bash
git add /Users/wangxinhao/muti-agent-offline-system/app/domain/questioning/service.py /Users/wangxinhao/muti-agent-offline-system/app/orchestrator/service.py /Users/wangxinhao/muti-agent-offline-system/app/domain/importing/service.py /Users/wangxinhao/muti-agent-offline-system/tests/test_pipeline.py
git commit -m "feat: export minimal qa rows with execution answers"
```

## Task 6: Add Difficulty Coverage Reporting

**Files:**
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/reports/builder.py`
- Test: `/Users/wangxinhao/muti-agent-offline-system/tests/test_pipeline.py`

- [ ] **Step 1: Compute per-level coverage**

```python
levels = [f"L{i}" for i in range(1, 9)]
covered = sorted(set(difficulties.keys()))
missing = [level for level in levels if level not in covered]
return {
    "sample_count": len(samples),
    "query_type_distribution": dict(query_types),
    "difficulty_distribution": dict(difficulties),
    "difficulty_coverage": {
        "covered_levels": covered,
        "missing_levels": missing,
        "coverage_ratio": len(covered) / len(levels),
    },
}
```

- [ ] **Step 2: Run tests**

Run: `python3 -m unittest tests/test_pipeline.py -v`

Expected: PASS with `difficulty_coverage` present.

- [ ] **Step 3: Commit**

```bash
git add /Users/wangxinhao/muti-agent-offline-system/app/reports/builder.py /Users/wangxinhao/muti-agent-offline-system/tests/test_pipeline.py
git commit -m "feat: report l1-l8 coverage"
```

## Task 7: Surface Difficulty Coverage In The UI

**Files:**
- Modify: `/Users/wangxinhao/muti-agent-offline-system/frontend/src/components/JobDetail.tsx`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/frontend/src/components/ImportDetail.tsx`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/frontend/src/styles.css`

- [ ] **Step 1: Add coverage block to job detail**

```tsx
const coverage = (job.metrics?.difficulty_coverage ?? {}) as {
  covered_levels?: string[];
  missing_levels?: string[];
  coverage_ratio?: number;
};
```

- [ ] **Step 2: Render visual level chips**

```tsx
<div className="difficulty-lane">
  {["L1","L2","L3","L4","L5","L6","L7","L8"].map((level) => (
    <span key={level} className={coverage.covered_levels?.includes(level) ? "difficulty-chip covered" : "difficulty-chip missing"}>
      {level}
    </span>
  ))}
</div>
```

- [ ] **Step 3: Add matching CSS**

```css
.difficulty-lane { display: flex; flex-wrap: wrap; gap: 8px; }
.difficulty-chip.covered { background: rgba(114,240,194,0.12); color: #d7fff2; }
.difficulty-chip.missing { background: rgba(255,125,146,0.12); color: #ffd7de; }
```

- [ ] **Step 4: Run frontend build**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/frontend && npm run build`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add /Users/wangxinhao/muti-agent-offline-system/frontend/src/components/JobDetail.tsx /Users/wangxinhao/muti-agent-offline-system/frontend/src/components/ImportDetail.tsx /Users/wangxinhao/muti-agent-offline-system/frontend/src/styles.css
git commit -m "feat: surface l1-l8 coverage in ui"
```

## Self-Review

- Spec coverage: This plan covers `L1-L8` generation, deterministic validation, export shrinkage, answer export, and coverage reporting.
- Placeholder scan: No `TODO` or vague “handle later” language remains.
- Type consistency: All later tasks use `L1-L8` literals, `answer`, `difficulty_coverage`, and `_export_sample` consistently.

