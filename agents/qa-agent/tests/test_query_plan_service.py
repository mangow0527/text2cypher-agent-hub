from __future__ import annotations

import unittest

from app.domain.models import CanonicalSchemaSpec, GenerationLimits
from app.domain.query_plan.service import QueryPlanService


class QueryPlanServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = CanonicalSchemaSpec(
            node_types=["NetworkElement", "Port", "Tunnel"],
            edge_types=["HAS_PORT", "FIBER_SRC"],
            node_properties={
                "NetworkElement": {"id": "string", "vendor": "string", "created_at": "int"},
                "Port": {"id": "string", "admin_status": "string"},
                "Tunnel": {"id": "string", "latency": "int"},
            },
            edge_constraints={
                "HAS_PORT": [["NetworkElement", "Port"]],
                "FIBER_SRC": [["Port", "Tunnel"]],
            },
            value_catalog={
                "NetworkElement.id": ["ne-1", "ne-2"],
                "NetworkElement.vendor": ["VendorA", "VendorB"],
                "Port.admin_status": ["UP", "DOWN"],
                "Tunnel.id": ["tn-1", "tn-2"],
                "Tunnel.latency": [12, 20],
            },
        )

    def test_build_plans_spreads_across_difficulty_and_query_types(self):
        service = QueryPlanService()
        plans = service.build_plans(
            schema=self.schema,
            limits=GenerationLimits(max_skeletons=32, max_candidates_per_skeleton=4, max_variants_per_question=5),
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

    def test_build_plans_uses_bounded_budget_for_large_target(self):
        service = QueryPlanService()
        plans = service.build_plans(
            self.schema,
            GenerationLimits(max_skeletons=64, max_candidates_per_skeleton=4, max_variants_per_question=5),
            20,
            "job_large",
        )

        self.assertGreaterEqual(len(plans), 20)
        self.assertLessEqual(len(plans), 28)

    def test_small_target_prefers_high_yield_families(self):
        service = QueryPlanService()
        plans = service.build_plans(
            self.schema,
            GenerationLimits(max_skeletons=32, max_candidates_per_skeleton=4, max_variants_per_question=5),
            1,
            "job_small",
        )

        self.assertEqual(len(plans), 1)
        markers = plans[0].bindings["markers"]
        self.assertEqual(markers.get("min_hops", 0), 0)
        self.assertFalse(markers.get("with_stage"))
        self.assertFalse(markers.get("subquery"))
        self.assertFalse(markers.get("comparison"))
