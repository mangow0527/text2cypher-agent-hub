from __future__ import annotations

import re
from typing import Any, Dict, Optional

from app.domain.generation.registry import QUERY_TYPE_REGISTRY


class StructureRuleValidator:
    def validate(self, query_type: str, structure_family: str, cypher: str) -> dict[str, Any]:
        family_config = self._family_config(query_type, structure_family)
        if family_config is None:
            return {
                "query_type_valid": False,
                "family_valid": False,
                "error_code": "QUERY_TYPE_MISMATCH",
            }

        markers = family_config["structural_markers"]
        query_type_valid = query_type in QUERY_TYPE_REGISTRY
        family_valid = self._matches_markers(cypher, markers)

        if not family_valid:
            return {
                "query_type_valid": query_type_valid,
                "family_valid": False,
                "error_code": "STRUCTURE_FAMILY_MISMATCH",
            }

        return {
            "query_type_valid": query_type_valid,
            "family_valid": True,
            "error_code": None,
        }

    def _family_config(self, query_type: str, structure_family: str) -> Optional[dict[str, Any]]:
        for family in QUERY_TYPE_REGISTRY.get(query_type, []):
            if family["family"] == structure_family:
                return family
        return None

    def _matches_markers(self, cypher: str, markers: Dict[str, Any]) -> bool:
        text = " ".join(cypher.split()).upper()
        hop_count = self._hop_count(text)

        min_hops = markers.get("min_hops", 0)
        max_hops = markers.get("max_hops", 0)
        if hop_count < min_hops:
            return False
        if max_hops and hop_count > max_hops:
            return False
        if markers.get("variable_length") and not self._has_variable_length(text):
            return False
        if markers.get("with_stage") and " WITH " not in text:
            return False
        if markers.get("ordering") and " ORDER BY " not in text:
            return False
        if markers.get("aggregation") and "COUNT(" not in text:
            return False
        if markers.get("distinct") and "RETURN DISTINCT" not in text:
            return False
        return True

    def _hop_count(self, text: str) -> int:
        relationships = re.findall(r"-\s*\[[^\]]*\]\s*(?:->|<\-)", text)
        return len(relationships)

    def _has_variable_length(self, text: str) -> bool:
        return bool(re.search(r"\[[^\]]*\*[^\]]*\]", text) or re.search(r"->\s*\{\s*\d+\s*,\s*\d*\s*\}", text))
