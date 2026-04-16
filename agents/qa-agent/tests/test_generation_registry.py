from __future__ import annotations

import unittest

from app.domain.generation.registry import ALLOWED_DIFFICULTY_LEVELS, QUERY_TYPE_REGISTRY


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
        seen_families = set()
        for query_type, families in QUERY_TYPE_REGISTRY.items():
            with self.subTest(query_type=query_type):
                self.assertGreaterEqual(len(families), 1)
                for family in families:
                    self.assertIn("family", family)
                    self.assertIn("difficulty_band", family)
                    self.assertIn("structural_markers", family)
                    self.assertTrue(family["family"])
                    self.assertTrue(family["difficulty_band"])
                    self.assertTrue(family["structural_markers"])
                    self.assertTrue(set(family["difficulty_band"]).issubset(ALLOWED_DIFFICULTY_LEVELS))
                    self.assertIn("min_hops", family["structural_markers"])
                    self.assertIn("max_hops", family["structural_markers"])
                    self.assertIn("aggregation", family["structural_markers"])
                    self.assertIn("ordering", family["structural_markers"])
                    self.assertIn("with_stage", family["structural_markers"])
                    self.assertIn("variable_length", family["structural_markers"])
                    self.assertIn("temporal", family["structural_markers"])
                    self.assertIn("distinct", family["structural_markers"])
                    self.assertNotIn(family["family"], seen_families)
                    seen_families.add(family["family"])

        self.assertEqual(len(seen_families), sum(len(families) for families in QUERY_TYPE_REGISTRY.values()))
