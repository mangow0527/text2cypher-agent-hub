from __future__ import annotations

import re

from app.domain.models import QueryPlan


class PlanValidator:
    def validate(self, plan: QueryPlan | None, cypher: str) -> dict[str, object]:
        if plan is None:
            return {"ok": True, "reasons": []}

        text = " ".join(cypher.split()).lower()
        reasons: list[str] = []
        semantics = plan.required_semantics or {}

        limit = semantics.get("limit")
        if limit is not None:
            match = re.search(r"\blimit\s+(\d+)\b", text)
            if not match:
                reasons.append("missing required limit")
            elif isinstance(limit, int) and int(match.group(1)) < limit:
                reasons.append("limit below required minimum")
            elif not isinstance(limit, int) and f"limit {limit}" not in text:
                reasons.append("missing required limit")

        if semantics.get("ordering") and "order by" not in text:
            reasons.append("missing required ordering")

        if semantics.get("aggregation") and not any(token in text for token in ["count(", "sum(", "avg(", "min(", "max("]):
            reasons.append("missing required aggregation")

        if semantics.get("grouping") and " as group_key" not in text and "group_key" not in text:
            reasons.append("missing grouping projection")

        if semantics.get("variable_length") and "*1.." not in text and "*" not in text:
            reasons.append("missing variable-length path")

        min_hops = semantics.get("min_hops")
        if isinstance(min_hops, int) and min_hops >= 2 and text.count("]-") < min_hops:
            reasons.append("missing minimum hop traversal")

        if semantics.get("with_stage") and " with " not in f" {text} ":
            reasons.append("missing with-stage structure")

        for construct in plan.disallowed_constructs:
            if construct.lower() in text:
                reasons.append(f"contains disallowed construct: {construct}")

        return {"ok": not reasons, "reasons": reasons}
