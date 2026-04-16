from __future__ import annotations

import json
import re

from app.domain.knowledge.patcher import append_item_under_section


DOC_FILE_MAP = {
    "cypher_syntax": "cypher_syntax.md",
    "few_shot": "few_shot.md",
    "system_prompt": "system_prompt.md",
    "business_knowledge": "business_knowledge.md",
}


class RepairService:
    def __init__(self, store, model_gateway) -> None:
        self.store = store
        self.model_gateway = model_gateway

    def apply(self, suggestion: str, knowledge_types: list[str] | None) -> list[dict[str, str]]:
        target_types = knowledge_types or self._infer_types(suggestion)
        target_type = target_types[0]
        raw_response = ""
        try:
            raw_response = self.model_gateway.generate_text(
                "repair_patch",
                {"model": "glm-5", "temperature": 0.1, "max_output_tokens": 800},
                prompt=(
                    "请根据修复建议生成最小 patch JSON，字段必须包含 "
                    "doc_type, section, action, target_key, new_content, reason。\n"
                    f"建议：{suggestion}\n"
                    f"目标知识类型：{target_type}"
                ),
            )
        except Exception:
            raw_response = ""

        patch = self._parse_patch(raw_response, suggestion, target_type)
        filename = DOC_FILE_MAP[target_type]
        before = self.store.read_text(filename)
        item = f"[id: {patch['target_key']}]\n{patch['new_content']}"
        after = append_item_under_section(before, patch["section"], item)
        self.store.write_versioned(filename, before, after, suggestion, target_type)
        return [
            {
                "doc_type": target_type,
                "section": patch["section"],
                "before": before,
                "after": after,
            }
        ]

    def _infer_types(self, suggestion: str) -> list[str]:
        if "术语" in suggestion or "映射" in suggestion or "语义" in suggestion:
            return ["business_knowledge"]
        if "示例" in suggestion or "few-shot" in suggestion:
            return ["few_shot"]
        if "提示词" in suggestion or "规则" in suggestion:
            return ["system_prompt"]
        return ["cypher_syntax"]

    def _parse_patch(self, raw_response: str, suggestion: str, target_type: str) -> dict[str, str]:
        parsed = self._try_parse_json(raw_response)
        if parsed:
            return parsed
        return self._fallback_patch(suggestion, target_type)

    def _try_parse_json(self, raw_response: str) -> dict[str, str] | None:
        raw_response = raw_response.strip()
        if not raw_response:
            return None

        candidates = [raw_response]
        match = re.search(r"\{.*\}", raw_response, re.DOTALL)
        if match:
            candidates.append(match.group(0))

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                required = {"section", "target_key", "new_content"}
                if required.issubset(parsed.keys()):
                    return parsed
        return None

    def _fallback_patch(self, suggestion: str, target_type: str) -> dict[str, str]:
        normalized = (
            suggestion.replace("\n", " ")
            .replace("`", "")
            .strip()
        )
        target_key = re.sub(r"[^a-z0-9_]+", "_", normalized.lower())[:48].strip("_") or "auto_patch"

        if target_type == "few_shot":
            return {
                "doc_type": target_type,
                "section": "Reference Examples",
                "action": "append_example",
                "target_key": target_key,
                "new_content": (
                    f"Question: {suggestion}\n"
                    "Cypher: MATCH (n:NetworkElement) RETURN n.id LIMIT 5\n"
                    "Why: 自动兜底生成的示例，占位后可继续人工优化。"
                ),
                "reason": "fallback patch because model output was not valid JSON",
            }

        if target_type == "system_prompt":
            section = "Core Rules"
        elif target_type == "cypher_syntax":
            section = "Core Rules"
        else:
            section = "Terminology Mapping"

        return {
            "doc_type": target_type,
            "section": section,
            "action": "add_section_item",
            "target_key": target_key,
            "new_content": f"- {suggestion}",
            "reason": "fallback patch because model output was not valid JSON",
        }
