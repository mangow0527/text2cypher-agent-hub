from __future__ import annotations

from collections import Counter

from app.domain.generation.registry import QUERY_TYPE_REGISTRY
from app.domain.models import QASample


ALL_DIFFICULTY_LEVELS = [f"L{level}" for level in range(1, 9)]
ALL_LANGUAGE_STYLES = [
    "natural_short",
    "spoken_query",
    "business_term",
    "ellipsis_query",
    "task_oriented",
]


class ReportBuilder:
    def build(self, samples: list[QASample]) -> dict:
        query_types = Counter()
        structure_families = Counter()
        difficulties = Counter()
        language_styles = Counter()
        for sample in samples:
            query_types.update(sample.query_types)
            structure_family = sample.provenance.get("structure_family", "")
            if structure_family:
                structure_families.update([structure_family])
            difficulties.update([sample.difficulty])
            language_styles.update(sample.question_variant_styles)
        covered_levels = [level for level in ALL_DIFFICULTY_LEVELS if difficulties[level] > 0]
        missing_levels = [level for level in ALL_DIFFICULTY_LEVELS if difficulties[level] == 0]
        covered_styles = [style for style in ALL_LANGUAGE_STYLES if language_styles[style] > 0]
        missing_styles = [style for style in ALL_LANGUAGE_STYLES if language_styles[style] == 0]
        all_query_types = list(QUERY_TYPE_REGISTRY.keys())
        covered_query_types = [query_type for query_type in all_query_types if query_types[query_type] > 0]
        missing_query_types = [query_type for query_type in all_query_types if query_types[query_type] == 0]
        all_families = [
            family["family"]
            for families in QUERY_TYPE_REGISTRY.values()
            for family in families
        ]
        covered_families = [family for family in all_families if structure_families[family] > 0]
        missing_families = [family for family in all_families if structure_families[family] == 0]
        return {
            "sample_count": len(samples),
            "query_type_distribution": dict(query_types),
            "query_type_coverage": {
                "all_query_types": all_query_types,
                "covered_query_types": covered_query_types,
                "missing_query_types": missing_query_types,
                "distribution": dict(query_types),
                "is_complete": not missing_query_types,
            },
            "structure_family_coverage": {
                "all_families": all_families,
                "covered_families": covered_families,
                "missing_families": missing_families,
                "distribution": dict(structure_families),
                "is_complete": not missing_families,
            },
            "difficulty_distribution": dict(difficulties),
            "difficulty_coverage": {
                "all_levels": ALL_DIFFICULTY_LEVELS,
                "covered_levels": covered_levels,
                "missing_levels": missing_levels,
                "is_complete": not missing_levels,
            },
            "language_coverage": {
                "all_styles": ALL_LANGUAGE_STYLES,
                "covered_styles": covered_styles,
                "missing_styles": missing_styles,
                "style_distribution": dict(language_styles),
                "is_complete": not missing_styles,
            },
        }
