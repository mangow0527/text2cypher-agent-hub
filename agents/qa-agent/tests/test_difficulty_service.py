from __future__ import annotations

import unittest

from app.domain.difficulty.service import DifficultyService


class DifficultyServiceTest(unittest.TestCase):
    def test_classifies_l1_to_l8_examples(self) -> None:
        service = DifficultyService()

        cases = [
            ("MATCH (n:A) RETURN n LIMIT 5", "L1"),
            ("MATCH (n:A) WHERE n.id = 'x' RETURN n LIMIT 10", "L2"),
            ("MATCH (a:A)-[:R]->(b:B) RETURN a, b LIMIT 5", "L3"),
            ("MATCH (n:A) RETURN count(n) AS total", "L4"),
            ("MATCH (a:A)-[:R1]->(:B)-[:R2]->(c:C) RETURN c LIMIT 5", "L5"),
            (
                "MATCH (a:A)-[:R1]->(:B)-[:R2]->(c:C) WHERE a.status = 'up' RETURN c.id, count(*) AS total ORDER BY total DESC LIMIT 5",
                "L6",
            ),
            ("MATCH (a:A)-[:R1]->(:B)-[:R2]->(:C)-[:R3]->(d:D) RETURN d", "L7"),
            (
                "MATCH (a:A)-[:R1]->(b:B) WITH a, count(b) AS cnt MATCH (a)-[:R2]->(c:C) WHERE cnt > 3 RETURN a.id, count(c)",
                "L8",
            ),
        ]

        for cypher, expected_level in cases:
            with self.subTest(cypher=cypher, expected_level=expected_level):
                self.assertEqual(service.classify(cypher), expected_level)

    def test_ignores_keywords_inside_literals_and_comments(self) -> None:
        service = DifficultyService()

        cypher = "MATCH (n:A) RETURN 'MATCH WHERE ORDER BY' AS label // MATCH WHERE ORDER BY"

        self.assertEqual(service.classify(cypher), "L1")

    def test_classifies_bare_star_variable_length_as_l5(self) -> None:
        service = DifficultyService()

        cypher = "MATCH (a:A)-[:R*]->(b:B) RETURN b LIMIT 5"

        self.assertEqual(service.classify(cypher), "L5")

    def test_classifies_open_ended_lower_bound_variable_length_as_l5(self) -> None:
        service = DifficultyService()

        cypher = "MATCH (a:A)-[:R*2..]->(b:B) RETURN b LIMIT 5"

        self.assertEqual(service.classify(cypher), "L5")

    def test_classifies_open_ended_upper_bound_variable_length_as_l5(self) -> None:
        service = DifficultyService()

        cypher = "MATCH (a:A)-[:R*..5]->(b:B) RETURN b LIMIT 5"

        self.assertEqual(service.classify(cypher), "L5")

    def test_classifies_brace_syntax_variable_length_as_l5(self) -> None:
        service = DifficultyService()

        cypher = "MATCH (a:A)-[:R]->{1,3}(b:B) RETURN b LIMIT 5"

        self.assertEqual(service.classify(cypher), "L5")

    def test_classifies_multi_stage_match_as_l7(self) -> None:
        service = DifficultyService()

        cypher = "MATCH (a:A) WITH a MATCH (b:B) RETURN a, b"

        self.assertEqual(service.classify(cypher), "L7")

    def test_does_not_treat_count_star_as_variable_length(self) -> None:
        service = DifficultyService()

        cypher = "MATCH (n:A) RETURN count(*) AS total"

        self.assertEqual(service.classify(cypher), "L4")
