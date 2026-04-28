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
        payload = {
            "canonical_pass": self._read_bool(sample.provenance.get("canonical_pass")),
            "canonical_checks": self._read_json(sample.provenance.get("canonical_checks"), {}),
            "approved_styles": self._read_json(sample.provenance.get("approved_styles"), []),
        }
        canonical_ok, approved_variants, approved_styles = self._parse_bundle_result(
            json.dumps(payload, ensure_ascii=False),
            sample,
        )
        if not canonical_ok:
            return False, approved_variants, approved_styles
        consistency_text = self.model_gateway.generate_text(
            "question_cypher_consistency",
            model_config,
            question=sample.question_canonical_zh,
            cypher=sample.cypher,
        )
        return (
            self._parse_consistency_result(consistency_text),
            approved_variants,
            approved_styles,
        )

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

    def _read_json(self, payload: str | None, fallback):
        if not payload:
            return fallback
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return fallback

    def _read_bool(self, payload: str | None) -> bool:
        if not payload:
            return False
        try:
            return bool(json.loads(payload))
        except json.JSONDecodeError:
            return payload.strip().lower() == "true"

    def _parse_consistency_result(self, payload: str) -> bool:
        return payload.strip().upper().startswith("PASS")

    def _passes_rule_checks(self, question: str, cypher: str) -> bool:
        text = question.strip().lower()
        cypher_lower = cypher.lower()

        if not is_natural_language_question(question):
            return False

        limit_match = re.search(r"\blimit\s+(\d+)\b", cypher_lower)
        if limit_match and "order by" in cypher_lower:
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
