from __future__ import annotations

import json
import re
from typing import Any

from app.domain.knowledge.patcher import append_item_under_section


DOC_FILE_MAP = {
    "cypher_syntax": "cypher_syntax.md",
    "few_shot": "few_shot.md",
    "system_prompt": "system_prompt.md",
    "business_knowledge": "business_knowledge.md",
}


class RepairService:
    def __init__(self, store, model_gateway, module_logs=None) -> None:
        self.store = store
        self.model_gateway = model_gateway
        self.module_logs = module_logs

    def apply(self, suggestion: str, knowledge_types: list[str] | None) -> list[dict[str, str]]:
        patches = self.propose(suggestion, knowledge_types)
        return self.apply_candidates(patches, suggestion)

    def propose(self, suggestion: str, knowledge_types: list[str] | None) -> list[dict[str, str]]:
        target_types = knowledge_types or self._infer_types(suggestion)
        if self.module_logs is not None:
            self.module_logs.append(
                module="repair",
                level="info",
                operation="repair_analysis_requested",
                status="started",
                request_body={"suggestion": suggestion, "knowledge_types": target_types},
            )
        raw_response = self.model_gateway.generate_text(
            "repair_analysis",
            {"model": "glm-5", "temperature": 0.1, "max_output_tokens": 800},
            prompt=self._repair_analysis_prompt(suggestion, target_types),
        )
        if self.module_logs is not None:
            self.module_logs.append(
                module="repair",
                level="info",
                operation="repair_analysis_generated",
                status="success",
                response_body={"requested_types": target_types, "raw_response": raw_response},
            )
        analysis = self._parse_analysis(raw_response, suggestion, target_types)
        patches = self._build_patches_from_analysis(analysis, target_types)
        return patches

    def apply_candidates(self, patches: list[dict[str, str]], suggestion: str) -> list[dict[str, str]]:
        changes: list[dict[str, str]] = []
        for patch in patches:
            target_type = patch["doc_type"]
            filename = DOC_FILE_MAP[target_type]
            before = self.store.read_text(filename)
            if self._patch_already_present(before, patch["section"], patch["new_content"]):
                if self.module_logs is not None:
                    self.module_logs.append(
                        module="repair",
                        level="info",
                        operation="knowledge_document_skipped_duplicate",
                        status="skipped",
                        request_body={
                            "filename": filename,
                            "target_type": target_type,
                            "section": patch["section"],
                            "target_key": patch["target_key"],
                        },
                    )
                continue
            item = f"[id: {patch['target_key']}]\n{patch['new_content']}"
            after = append_item_under_section(before, patch["section"], item)
            self.store.write_versioned(filename, before, after, suggestion, target_type)
            if self.module_logs is not None:
                self.module_logs.append(
                    module="repair",
                    level="info",
                    operation="knowledge_document_updated",
                    status="success",
                    request_body={
                        "filename": filename,
                        "target_type": target_type,
                        "section": patch["section"],
                        "target_key": patch["target_key"],
                    },
                    response_body={"before": before, "after": after},
                )
            changes.append(
                {
                    "doc_type": target_type,
                    "section": patch["section"],
                    "before": before,
                    "after": after,
                }
            )
        return changes

    def _repair_analysis_prompt(self, suggestion: str, target_types: list[str]) -> str:
        return (
            "你是 TuGraph Text2Cypher 知识增强器。"
            "请把修复建议转成结构化知识分析 JSON，用于让后续同类问题更稳定地产出正确 Cypher。"
            "必须输出合法 JSON，对象字段必须包含："
            "intent_summary, canonical_question_pattern, cypher_constraints, schema_bindings, "
            "business_mapping, positive_example, negative_example, target_docs。\n"
            "要求：\n"
            "1. business_mapping 是字符串数组，写术语到 schema 的稳定映射。\n"
            "2. cypher_constraints 是字符串数组，写方向、过滤、路径、返回语义等硬约束。\n"
            "3. positive_example 必须包含 question, cypher, why。\n"
            "4. negative_example 必须包含 question, cypher, why_not。\n"
            "5. target_docs 只能从 cypher_syntax, few_shot, system_prompt, business_knowledge 中选择。\n"
            f"修复建议：{suggestion}\n"
            f"优先目标知识类型：{', '.join(target_types)}"
        )

    def _infer_types(self, suggestion: str) -> list[str]:
        if "术语" in suggestion or "映射" in suggestion or "语义" in suggestion:
            return ["business_knowledge"]
        if "示例" in suggestion or "few-shot" in suggestion:
            return ["few_shot"]
        if "提示词" in suggestion or "规则" in suggestion:
            return ["system_prompt"]
        return ["cypher_syntax"]

    def _parse_analysis(self, raw_response: str, suggestion: str, target_types: list[str]) -> dict[str, Any]:
        parsed = self._try_parse_json(raw_response)
        if parsed:
            if self.module_logs is not None:
                self.module_logs.append(
                    module="repair",
                    level="info",
                    operation="repair_analysis_parsed",
                    status="success",
                    response_body={"target_types": target_types, "analysis": parsed},
                )
            return parsed
        patch = self._fallback_analysis(suggestion, target_types)
        if self.module_logs is not None:
            self.module_logs.append(
                module="repair",
                level="warning",
                operation="repair_analysis_fallback_used",
                status="fallback",
                response_body={"target_types": target_types, "analysis": patch},
            )
        return patch

    def _try_parse_json(self, raw_response: str) -> dict[str, Any] | None:
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
                required = {"intent_summary", "cypher_constraints", "business_mapping", "positive_example", "target_docs"}
                if required.issubset(parsed.keys()):
                    return parsed
        return None

    def _fallback_analysis(self, suggestion: str, target_types: list[str]) -> dict[str, Any]:
        normalized = (
            suggestion.replace("\n", " ")
            .replace("`", "")
            .strip()
        )
        return {
            "intent_summary": normalized,
            "canonical_question_pattern": suggestion,
            "cypher_constraints": [normalized],
            "schema_bindings": [],
            "business_mapping": [suggestion],
            "positive_example": {
                "question": suggestion,
                "cypher": "MATCH (n:NetworkElement) RETURN n.id LIMIT 5",
                "why": "自动兜底示例，提醒后续人工审阅。",
            },
            "negative_example": {
                "question": suggestion,
                "cypher": "MATCH (n) RETURN n",
                "why_not": "过于宽泛，无法稳定约束 Cypher 生成。",
            },
            "target_docs": target_types or ["cypher_syntax"],
        }

    def _build_patches_from_analysis(self, analysis: dict[str, Any], requested_types: list[str]) -> list[dict[str, str]]:
        target_docs = analysis.get("target_docs") or requested_types or ["cypher_syntax"]
        target_docs = [doc for doc in target_docs if doc in DOC_FILE_MAP]
        if not target_docs:
            target_docs = requested_types or ["cypher_syntax"]

        key_seed = re.sub(
            r"[^a-z0-9_]+",
            "_",
            str(analysis.get("canonical_question_pattern") or analysis.get("intent_summary") or "auto_patch").lower(),
        )[:48].strip("_") or "auto_patch"

        patches: list[dict[str, str]] = []
        if "business_knowledge" in target_docs:
            lines = [f"- {line}" if not str(line).lstrip().startswith("-") else str(line) for line in analysis.get("business_mapping", [])]
            if lines:
                patches.append(
                    {
                        "doc_type": "business_knowledge",
                        "section": "Terminology Mapping",
                        "target_key": f"{key_seed}_business",
                        "new_content": "\n".join(lines),
                    }
                )
        if "cypher_syntax" in target_docs:
            constraint_lines = [f"- {line}" if not str(line).lstrip().startswith("-") else str(line) for line in analysis.get("cypher_constraints", [])]
            binding_lines = []
            for binding in analysis.get("schema_bindings", []):
                if isinstance(binding, dict) and binding.get("term") and binding.get("schema"):
                    binding_lines.append(f"- 术语 `{binding['term']}` 生成时优先映射为 `{binding['schema']}`。")
            lines = constraint_lines + binding_lines
            if lines:
                patches.append(
                    {
                        "doc_type": "cypher_syntax",
                        "section": "Core Rules",
                        "target_key": f"{key_seed}_syntax",
                        "new_content": "\n".join(lines),
                    }
                )
        if "few_shot" in target_docs:
            positive = analysis.get("positive_example") or {}
            negative = analysis.get("negative_example") or {}
            if positive.get("question") and positive.get("cypher"):
                few_shot_content = (
                    f"Question: {positive['question']}\n"
                    f"Cypher: {positive['cypher']}\n"
                    f"Why: {positive.get('why', analysis.get('intent_summary', ''))}"
                )
                if negative.get("cypher"):
                    few_shot_content += (
                        f"\nAnti-Pattern: {negative['cypher']}\n"
                        f"Why Not: {negative.get('why_not', '该写法会误导后续生成。')}"
                    )
                patches.append(
                    {
                        "doc_type": "few_shot",
                        "section": "Reference Examples",
                        "target_key": f"{key_seed}_few_shot",
                        "new_content": few_shot_content,
                    }
                )
        if "system_prompt" in target_docs and analysis.get("intent_summary"):
            patches.append(
                {
                    "doc_type": "system_prompt",
                    "section": "Core Rules",
                    "target_key": f"{key_seed}_system",
                    "new_content": f"- 对于“{analysis['intent_summary']}”相关问题，严格遵循新增术语映射、路径约束与 few-shot 示例。",
                }
            )
        return patches

    def _patch_already_present(self, document: str, section: str, new_content: str) -> bool:
        section_text = self._extract_section_text(document, section)
        normalized_target = self._normalize_text(new_content)
        if not normalized_target:
            return True
        return normalized_target in self._normalize_text(section_text)

    def _extract_section_text(self, document: str, section: str) -> str:
        marker = f"## {section}"
        if marker not in document:
            return ""
        _, after = document.split(marker, 1)
        lines = after.splitlines()
        collected: list[str] = []
        for line in lines[1:]:
            if line.startswith("## "):
                break
            collected.append(line)
        return "\n".join(collected)

    def _normalize_text(self, value: str) -> str:
        lines = []
        for raw in value.splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("[id:"):
                continue
            if stripped.startswith("- "):
                stripped = stripped[2:].strip()
            lines.append(stripped)
        return "\n".join(lines).strip().lower()
