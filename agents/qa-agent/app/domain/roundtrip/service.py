from __future__ import annotations

import json
import re

from app.domain.models import ModelConfig, QASample
from app.domain.questioning.service import is_natural_language_question
from app.integrations.openai.model_gateway import ModelGateway


class RoundtripService:
    def __init__(self, model_gateway: ModelGateway | None = None) -> None:
        self.model_gateway = model_gateway or ModelGateway()

    def check(self, sample: QASample, model_config: ModelConfig) -> tuple[bool, list[str], list[str]]:
        payload = self.model_gateway.generate_text(
            "question_bundle_consistency",
            model_config,
            canonical_question=sample.question_canonical_zh,
            cypher=sample.cypher,
            schema_summary=sample.provenance.get("schema_summary", "无摘要"),
            return_semantics=sample.provenance.get("return_semantics", ",".join(sample.result_signature.columns or ["result"])),
            result_summary=sample.provenance.get("result_summary", json.dumps({
                "columns": sample.result_signature.columns,
                "row_count": sample.result_signature.row_count,
                "preview": sample.result_signature.result_preview[:2],
            }, ensure_ascii=False)),
            variants_json=json.dumps(
                [
                    {"style": style, "question": question}
                    for style, question in zip(sample.question_variant_styles, sample.question_variants_zh)
                ],
                ensure_ascii=False,
            ),
        )
        return self._parse_bundle_result(payload, sample)

    def _parse_bundle_result(self, payload: str, sample: QASample) -> tuple[bool, list[str], list[str]]:
        try:
            data = json.loads(payload)
            canonical_ok = bool(data.get("canonical_pass", False))
            canonical_checks = data.get("canonical_checks", {})
            if isinstance(canonical_checks, dict) and canonical_checks:
                canonical_ok = canonical_ok and all(bool(value) for value in canonical_checks.values())
            canonical_ok = canonical_ok and self._passes_rule_checks(sample.question_canonical_zh, sample.cypher)
            approved_styles = data.get("approved_styles", [])
            if not isinstance(approved_styles, list):
                approved_styles = []
            style_set = {str(item).strip() for item in approved_styles if str(item).strip()}
            approved_variants: list[str] = []
            approved_variant_styles: list[str] = []
            for style, question in zip(sample.question_variant_styles, sample.question_variants_zh):
                if style in style_set:
                    approved_variants.append(question)
                    approved_variant_styles.append(style)
            return canonical_ok, approved_variants, approved_variant_styles
        except json.JSONDecodeError:
            result = payload.strip().upper()
            canonical_ok = result.startswith("PASS") and self._passes_rule_checks(sample.question_canonical_zh, sample.cypher)
            return canonical_ok, sample.question_variants_zh, sample.question_variant_styles

    def _passes_rule_checks(self, question: str, cypher: str) -> bool:
        text = question.strip().lower()
        cypher_lower = cypher.lower()

        if not is_natural_language_question(question):
            return False

        limit_match = re.search(r"\blimit\s+(\d+)\b", cypher_lower)
        if limit_match:
            limit_value = limit_match.group(1)
            if not self._mentions_limit(text, limit_value):
                return False

        if any(token in cypher_lower for token in ["count(", "sum(", "avg(", "min(", "max("]):
            if not re.search(r"多少|几个|数量|总数|统计|平均|最大|最小|总计|合计", question):
                return False

        return True

    def _mentions_limit(self, text: str, limit_value: str) -> bool:
        patterns = [
            f"前{limit_value}",
            f"{limit_value}个",
            f"{limit_value}条",
            f"{limit_value}项",
            f"{limit_value}名",
            f"最多{limit_value}",
            f"top {limit_value}",
            f"top{limit_value}",
        ]
        if any(pattern in text for pattern in patterns):
            return True
        return bool(re.search(rf"\b{re.escape(limit_value)}\b", text))
