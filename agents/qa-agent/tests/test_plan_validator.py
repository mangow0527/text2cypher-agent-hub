from __future__ import annotations

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

    def test_accepts_cypher_matching_required_semantics(self):
        validator = PlanValidator()
        plan = QueryPlan(
            plan_id="plan_2",
            query_type="AGGREGATION",
            structure_family="aggregate_global_count",
            difficulty="L4",
            bindings={"node": "NetworkElement"},
            required_semantics={"aggregation": True},
            disallowed_constructs=["optional match"],
        )

        result = validator.validate(plan, "MATCH (n:NetworkElement) RETURN count(n) AS total")

        self.assertTrue(result["ok"])
