# Structural Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the Text2Cypher generator from a small difficulty-first family set into a structure-complete primary query-type generator with family-aware validation, coverage reporting, and UI surfacing.

**Architecture:** The generator will move to `query_type -> structure_family -> difficulty band -> candidate -> validation -> language variants`. A registry-driven structure layer will become the source of truth for generation and reporting, while validation will reject candidates whose query type or family contract does not match the produced Cypher.

**Tech Stack:** Python, Pydantic, FastAPI, React, unittest, TuGraph HTTP integration, GLM-5 via OpenAI-compatible API

---

## File Structure

- Create: `/Users/wangxinhao/muti-agent-offline-system/app/domain/generation/registry.py`
  - Own the primary query-type registry, structure family metadata, family predicates, and allowed difficulty bands.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/generation/service.py`
  - Build skeletons from the registry instead of the hard-coded `DIFFICULTY_FAMILIES` list.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/models.py`
  - Expand skeleton/candidate/QA metadata with `query_type`, `structure_family`, and family-aware fields.
- Create: `/Users/wangxinhao/muti-agent-offline-system/app/domain/validation/structure_rules.py`
  - Implement rule-based checks for query-type and structure-family validity.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/validation/service.py`
  - Attach query-type and family validation to validated samples.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/orchestrator/service.py`
  - Enforce family-valid candidates and preserve structural metadata through reporting.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/reports/builder.py`
  - Add `query_type_coverage` and `structure_family_coverage`.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/importing/service.py`
  - Normalize imported samples with optional query-type and structure-family metadata.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/frontend/src/components/JobDetail.tsx`
  - Show query-type and structure-family coverage.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/frontend/src/components/ImportDetail.tsx`
  - Show imported query-type and structure-family coverage.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/frontend/src/styles.css`
  - Add visual treatment for structural coverage chips.
- Modify: `/Users/wangxinhao/muti-agent-offline-system/tests/test_pipeline.py`
  - Add end-to-end assertions for query-type and family coverage.
- Create: `/Users/wangxinhao/muti-agent-offline-system/tests/test_generation_registry.py`
  - Unit tests for registry completeness and family difficulty bands.
- Create: `/Users/wangxinhao/muti-agent-offline-system/tests/test_structure_rules.py`
  - Unit tests for query-type and family rule validation.

## Task 1: Lock Structural Coverage Contracts In Tests

**Files:**
- Modify: `/Users/wangxinhao/muti-agent-offline-system/tests/test_pipeline.py`
- Create: `/Users/wangxinhao/muti-agent-offline-system/tests/test_generation_registry.py`
- Create: `/Users/wangxinhao/muti-agent-offline-system/tests/test_structure_rules.py`

- [ ] **Step 1: Write the failing registry completeness test**

```python
import unittest

from app.domain.generation.registry import QUERY_TYPE_REGISTRY


class GenerationRegistryTest(unittest.TestCase):
    def test_registry_covers_all_primary_query_types(self) -> None:
        expected = {
            "LOOKUP",
            "FILTER",
            "SORT_TOPK",
            "AGGREGATION",
            "GROUP_AGG",
            "MULTI_HOP",
            "COMPARISON",
            "TEMPORAL",
            "PATH",
            "SET_OP",
            "SUBQUERY",
            "HYBRID",
        }

        self.assertEqual(set(QUERY_TYPE_REGISTRY.keys()), expected)
        for query_type, families in QUERY_TYPE_REGISTRY.items():
            with self.subTest(query_type=query_type):
                self.assertGreaterEqual(len(families), 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_generation_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.generation.registry'`

- [ ] **Step 3: Add failing structure-validation test**

```python
import unittest

from app.domain.validation.structure_rules import StructureRuleValidator


class StructureRuleValidatorTest(unittest.TestCase):
    def test_detects_family_contract_mismatch(self) -> None:
        validator = StructureRuleValidator()

        result = validator.validate(
            query_type="PATH",
            structure_family="variable_length_path",
            cypher="MATCH (a:A)-[:R]->(b:B) RETURN b LIMIT 5",
        )

        self.assertFalse(result["family_valid"])
        self.assertEqual(result["error_code"], "STRUCTURE_FAMILY_MISMATCH")
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python3 -m unittest tests/test_structure_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.validation.structure_rules'`

- [ ] **Step 5: Add failing pipeline assertions for structural coverage**

```python
self.assertIn("query_type_coverage", completed.metrics)
self.assertIn("structure_family_coverage", completed.metrics)
self.assertIn("LOOKUP", completed.metrics["query_type_coverage"]["covered_query_types"])
self.assertTrue(completed.metrics["structure_family_coverage"]["covered_families"])
```

- [ ] **Step 6: Run pipeline test to verify it fails**

Run: `python3 -m unittest tests/test_pipeline.py -v`
Expected: FAIL because the report does not yet expose query-type or structure-family coverage.

## Task 2: Create The Query-Type Registry

**Files:**
- Create: `/Users/wangxinhao/muti-agent-offline-system/app/domain/generation/registry.py`
- Test: `/Users/wangxinhao/muti-agent-offline-system/tests/test_generation_registry.py`

- [ ] **Step 1: Write the registry**

```python
from __future__ import annotations

QUERY_TYPE_REGISTRY = {
    "LOOKUP": [
        {"family": "lookup_node_return", "difficulty_band": ["L1"]},
        {"family": "lookup_property_projection", "difficulty_band": ["L1"]},
        {"family": "lookup_entity_detail", "difficulty_band": ["L1", "L2"]},
    ],
    "FILTER": [
        {"family": "filter_single_condition", "difficulty_band": ["L2"]},
        {"family": "filter_boolean_combo", "difficulty_band": ["L2", "L3", "L4"]},
        {"family": "filter_range_condition", "difficulty_band": ["L2", "L3"]},
    ],
    "SORT_TOPK": [
        {"family": "sort_single_metric", "difficulty_band": ["L3", "L4"]},
        {"family": "topk_entities", "difficulty_band": ["L4"]},
        {"family": "sorted_filtered_projection", "difficulty_band": ["L4"]},
    ],
}
```

- [ ] **Step 2: Fill the remaining primary query types**

```python
QUERY_TYPE_REGISTRY.update(
    {
        "AGGREGATION": [
            {"family": "aggregate_global_count", "difficulty_band": ["L4"]},
            {"family": "aggregate_filtered_count", "difficulty_band": ["L4"]},
            {"family": "aggregate_scalar_metric", "difficulty_band": ["L4"]},
        ],
        "GROUP_AGG": [
            {"family": "group_count", "difficulty_band": ["L4"]},
            {"family": "group_ranked_count", "difficulty_band": ["L4", "L5"]},
            {"family": "group_filtered_aggregate", "difficulty_band": ["L5", "L6"]},
        ],
        "MULTI_HOP": [
            {"family": "two_hop_return", "difficulty_band": ["L5"]},
            {"family": "two_hop_filtered", "difficulty_band": ["L5", "L6"]},
            {"family": "multi_hop_projection", "difficulty_band": ["L6", "L7"]},
        ],
        "COMPARISON": [
            {"family": "attribute_comparison", "difficulty_band": ["L3", "L4"]},
            {"family": "aggregate_comparison", "difficulty_band": ["L4", "L5"]},
            {"family": "rank_position_comparison", "difficulty_band": ["L4", "L5"]},
        ],
        "TEMPORAL": [
            {"family": "time_range_filter", "difficulty_band": ["L2", "L3"]},
            {"family": "recent_or_earliest", "difficulty_band": ["L3", "L4"]},
            {"family": "temporal_ordering", "difficulty_band": ["L4"]},
        ],
        "PATH": [
            {"family": "path_existence", "difficulty_band": ["L4", "L5"]},
            {"family": "variable_length_path", "difficulty_band": ["L5", "L6"]},
            {"family": "path_constrained_target", "difficulty_band": ["L6", "L7"]},
        ],
        "SET_OP": [
            {"family": "distinct_projection", "difficulty_band": ["L2", "L3"]},
            {"family": "set_like_union_projection", "difficulty_band": ["L4", "L5"]},
            {"family": "membership_intersection_style", "difficulty_band": ["L4", "L5"]},
        ],
        "SUBQUERY": [
            {"family": "with_stage_filter", "difficulty_band": ["L7"]},
            {"family": "with_stage_aggregate", "difficulty_band": ["L7", "L8"]},
            {"family": "two_stage_refine", "difficulty_band": ["L7"]},
        ],
        "HYBRID": [
            {"family": "temporal_aggregate_hybrid", "difficulty_band": ["L6", "L7"]},
            {"family": "path_aggregate_hybrid", "difficulty_band": ["L7", "L8"]},
            {"family": "comparison_subquery_hybrid", "difficulty_band": ["L7", "L8"]},
        ],
    }
)
```

- [ ] **Step 3: Run the registry test**

Run: `python3 -m unittest tests/test_generation_registry.py -v`
Expected: PASS

## Task 3: Rebuild Generation Around Query Types And Families

**Files:**
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/generation/service.py`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/models.py`
- Test: `/Users/wangxinhao/muti-agent-offline-system/tests/test_pipeline.py`

- [ ] **Step 1: Extend models with structural metadata**

```python
class CypherSkeleton(BaseModel):
    skeleton_id: str
    query_types: List[str]
    structure_family: str
    pattern_template: str
    slots: Dict[str, List[str]]
    difficulty_floor: Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"] = "L1"


class CypherCandidate(BaseModel):
    candidate_id: str = Field(default_factory=lambda: f"cand_{uuid4().hex[:12]}")
    skeleton_id: str
    cypher: str
    query_types: List[str]
    structure_family: str
    bound_schema_items: Dict[str, List[str]] = Field(default_factory=dict)
    bound_values: Dict[str, Any] = Field(default_factory=dict)
    difficulty: Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"] = "L1"
```

- [ ] **Step 2: Replace the hard-coded family list with registry-backed skeleton builders**

```python
for query_type, families in QUERY_TYPE_REGISTRY.items():
    for family in families:
        skeletons.append(
            CypherSkeleton(
                skeleton_id=f"{family['family']}_{index:02d}",
                query_types=[query_type],
                structure_family=family["family"],
                pattern_template=self._template_for_family(query_type, family["family"]),
                slots=self._slots_for_template(template),
                difficulty_floor=family["difficulty_band"][0],
            )
        )
```

- [ ] **Step 3: Preserve structure family on generated candidates**

```python
candidates.append(
    CypherCandidate(
        skeleton_id=skeleton.skeleton_id,
        cypher=cypher,
        query_types=skeleton.query_types,
        structure_family=skeleton.structure_family,
        bound_schema_items=bound_schema_items,
        bound_values=bound_values,
        difficulty=skeleton.difficulty_floor,
    )
)
```

- [ ] **Step 4: Expand the pipeline test to assert structural coverage is produced**

```python
self.assertTrue(completed.metrics["query_type_coverage"]["covered_query_types"])
self.assertTrue(completed.metrics["structure_family_coverage"]["covered_families"])
```

- [ ] **Step 5: Run pipeline tests**

Run: `python3 -m unittest tests/test_pipeline.py -v`
Expected: FAIL only where structure validation and report wiring are still missing.

## Task 4: Implement Structure Rule Validation

**Files:**
- Create: `/Users/wangxinhao/muti-agent-offline-system/app/domain/validation/structure_rules.py`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/validation/service.py`
- Test: `/Users/wangxinhao/muti-agent-offline-system/tests/test_structure_rules.py`

- [ ] **Step 1: Write the structure validator**

```python
from __future__ import annotations


class StructureRuleValidator:
    def validate(self, query_type: str, structure_family: str, cypher: str) -> dict:
        text = " ".join(cypher.split()).upper()
        family_valid = self._matches_family(structure_family, text)
        query_type_valid = self._matches_query_type(query_type, text)
        if not family_valid:
            return {"query_type_valid": query_type_valid, "family_valid": False, "error_code": "STRUCTURE_FAMILY_MISMATCH"}
        if not query_type_valid:
            return {"query_type_valid": False, "family_valid": family_valid, "error_code": "QUERY_TYPE_MISMATCH"}
        return {"query_type_valid": True, "family_valid": True, "error_code": None}
```

- [ ] **Step 2: Add family rules for the primary structural markers**

```python
def _matches_family(self, structure_family: str, text: str) -> bool:
    if structure_family == "variable_length_path":
        return "*" in text or "->{" in text
    if structure_family == "with_stage_aggregate":
        return " WITH " in text and "COUNT(" in text and text.count("MATCH ") > 1
    if structure_family == "distinct_projection":
        return "RETURN DISTINCT" in text
    return True
```

- [ ] **Step 3: Wire structure validation into `ValidationService`**

```python
structure_check = self.structure_rule_validator.validate(
    query_type=candidate.query_types[0],
    structure_family=candidate.structure_family,
    cypher=candidate.cypher,
)
result.query_type_valid = structure_check["query_type_valid"]
result.family_valid = structure_check["family_valid"]
```

- [ ] **Step 4: Run structure tests**

Run: `python3 -m unittest tests/test_structure_rules.py -v`
Expected: PASS

## Task 5: Report Structural Coverage

**Files:**
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/reports/builder.py`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/orchestrator/service.py`
- Test: `/Users/wangxinhao/muti-agent-offline-system/tests/test_pipeline.py`

- [ ] **Step 1: Add query-type coverage**

```python
query_type_counts = Counter()
for sample in samples:
    query_type_counts.update(sample.query_types)

query_type_coverage = {
    "all_query_types": list(QUERY_TYPE_REGISTRY.keys()),
    "covered_query_types": [item for item in QUERY_TYPE_REGISTRY if query_type_counts[item] > 0],
    "missing_query_types": [item for item in QUERY_TYPE_REGISTRY if query_type_counts[item] == 0],
    "distribution": dict(query_type_counts),
}
```

- [ ] **Step 2: Add structure-family coverage**

```python
family_counts = Counter(sample.provenance.get("structure_family", "") for sample in samples)
all_families = [family["family"] for families in QUERY_TYPE_REGISTRY.values() for family in families]
structure_family_coverage = {
    "all_families": all_families,
    "covered_families": [item for item in all_families if family_counts[item] > 0],
    "missing_families": [item for item in all_families if family_counts[item] == 0],
    "distribution": dict(family_counts),
}
```

- [ ] **Step 3: Ensure provenance preserves family metadata**

```python
provenance={
    "skeleton_id": sample.candidate.skeleton_id,
    "structure_family": sample.candidate.structure_family,
    "generation_mode": "cypher_first",
}
```

- [ ] **Step 4: Run pipeline tests**

Run: `python3 -m unittest tests/test_pipeline.py -v`
Expected: PASS

## Task 6: Normalize Imports And Surface Structural Coverage In The UI

**Files:**
- Modify: `/Users/wangxinhao/muti-agent-offline-system/app/domain/importing/service.py`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/frontend/src/components/JobDetail.tsx`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/frontend/src/components/ImportDetail.tsx`
- Modify: `/Users/wangxinhao/muti-agent-offline-system/frontend/src/styles.css`

- [ ] **Step 1: Preserve or default structural metadata on imported samples**

```python
if "provenance" not in payload:
    payload["provenance"] = {"generation_mode": "manual_import", "structure_family": "manual_import"}
elif "structure_family" not in payload["provenance"]:
    payload["provenance"]["structure_family"] = "manual_import"
```

- [ ] **Step 2: Add query-type coverage block to `JobDetail.tsx`**

```tsx
<div className="detail-section-block">
  <div className="detail-label">查询类型覆盖</div>
  <div className="coverage-strip">
    {(queryTypeCoverage.all_query_types || []).map((item) => {
      const covered = (queryTypeCoverage.covered_query_types || []).includes(item);
      return <span key={item} className={`coverage-pill ${covered ? "covered" : "missing"}`}>{item}</span>;
    })}
  </div>
</div>
```

- [ ] **Step 3: Add structure-family coverage block to `ImportDetail.tsx`**

```tsx
<div className="detail-section-block">
  <div className="detail-label">结构族覆盖</div>
  <div className="coverage-strip">
    {(familyCoverage.all_families || []).map((item) => {
      const covered = (familyCoverage.covered_families || []).includes(item);
      return <span key={item} className={`coverage-pill ${covered ? "covered" : "missing"}`}>{item}</span>;
    })}
  </div>
</div>
```

- [ ] **Step 4: Add CSS treatment for long structural labels**

```css
.coverage-pill {
  max-width: 220px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
```

- [ ] **Step 5: Run frontend build**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/frontend && npm run build`
Expected: PASS

## Task 7: End-To-End Verification

**Files:**
- Test only

- [ ] **Step 1: Run focused backend tests**

Run: `python3 -m unittest tests/test_generation_registry.py tests/test_structure_rules.py tests/test_pipeline.py -v`
Expected: PASS

- [ ] **Step 2: Run frontend build**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/frontend && npm run build`
Expected: PASS

- [ ] **Step 3: Run one local generation pass**

Run: `python3 -m app.entrypoints.cli.main create-job --schema-file tests/fixtures/schema.json --mode online`
Expected: returns a `job_id`

- [ ] **Step 4: Execute the job**

Run: `python3 -m app.entrypoints.cli.main run-job <job_id>`
Expected: job completes and the generated report contains `query_type_coverage`, `structure_family_coverage`, `difficulty_coverage`, and `language_coverage`

- [ ] **Step 5: Check the report**

Run: `python3 -m app.entrypoints.cli.main show-job <job_id>`
Expected: the job metrics show non-empty query-type and structure-family coverage sections
