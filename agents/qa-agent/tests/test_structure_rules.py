from __future__ import annotations

import unittest

from app.domain.validation.structure_rules import StructureRuleValidator


class StructureRuleValidatorTest(unittest.TestCase):
    def test_detects_path_family_mismatch(self) -> None:
        validator = StructureRuleValidator()

        result = validator.validate(
            query_type="PATH",
            structure_family="variable_length_path",
            cypher="MATCH (a:A)-[:R]->(b:B) RETURN b LIMIT 5",
        )

        self.assertFalse(result["family_valid"])
        self.assertEqual(result["error_code"], "STRUCTURE_FAMILY_MISMATCH")

    def test_accepts_path_family_match(self) -> None:
        validator = StructureRuleValidator()

        result = validator.validate(
            query_type="PATH",
            structure_family="variable_length_path",
            cypher="MATCH (a:A)-[:R*1..3]->(b:B) RETURN b LIMIT 5",
        )

        self.assertTrue(result["family_valid"])
        self.assertTrue(result["query_type_valid"])
        self.assertIsNone(result["error_code"])
