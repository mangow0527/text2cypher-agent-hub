from __future__ import annotations

import re

from app.domain.knowledge.retriever import KnowledgeRetriever
from app.storage.knowledge_store import KnowledgeStore

MAX_PROMPT_CHARS = 80000


class PromptService:
    def __init__(self, store: KnowledgeStore) -> None:
        self.retriever = KnowledgeRetriever(store)

    def build_prompt(self, question: str) -> str:
        bundle = self.retriever.retrieve(question)
        sections = [
            ("system", bundle["system_prompt"]),
            ("Schema", bundle["schema_context"]),
            ("术语映射", self._extract_bullets(bundle["business_context"])),
            ("关键路径与过滤约束", self._extract_bullets(bundle["syntax_context"])),
        ]
        positive_examples, negative_examples = self._split_examples(bundle["few_shot_examples"])
        sections.extend(
            [
                ("正例示例", positive_examples),
                ("反例提醒", negative_examples),
                (
                    "生成要求",
                    "\n".join(
                        [
                            "- 输出必须是单条 Cypher",
                            "- 不要输出解释",
                            "- 不要输出 Markdown",
                            "- 确保方向、属性、过滤条件、聚合语义正确",
                            "- 生成前先校对术语映射、路径方向、过滤条件、返回对象",
                            "- 如果正例与当前问题高度一致，优先复用其路径骨架，再根据过滤条件做最小改写",
                            "- 严格避开反例提醒中的错误返回对象、错误路径或过宽查询",
                        ]
                    ),
                ),
                ("用户问题", bundle["question"]),
            ]
        )
        return self._compose_prompt_with_budget(sections)

    def _extract_bullets(self, text: str) -> str:
        bullets = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("## ") or stripped.startswith("[id:"):
                continue
            if stripped.startswith("- "):
                bullets.append(stripped)
        if bullets:
            return "\n".join(bullets)
        collapsed = text.strip()
        return collapsed or "- 暂无额外约束。"

    def _split_examples(self, text: str) -> tuple[str, str]:
        blocks = [block.strip() for block in re.split(r"\n\s*\n", text.strip()) if block.strip()]
        positive_blocks: list[str] = []
        negative_lines: list[str] = []

        for block in blocks:
            if "Question:" in block and "Cypher:" in block:
                positive_blocks.append(block)
                anti_pattern = re.search(r"Anti-Pattern:\s*(.+)", block)
                why_not = re.search(r"Why Not:\s*(.+)", block)
                if anti_pattern:
                    negative_lines.append(f"- 错误示例：{anti_pattern.group(1).strip()}")
                if why_not:
                    negative_lines.append(f"- 原因：{why_not.group(1).strip()}")

        positive = "\n\n".join(positive_blocks) if positive_blocks else text.strip() or "暂无正例。"
        negative = "\n".join(negative_lines) if negative_lines else "- 暂无明确反例，但仍需避免错误路径、错误返回对象和过宽查询。"
        return positive, negative

    def _compose_prompt_with_budget(self, sections: list[tuple[str, str]]) -> str:
        section_budgets = {
            "system": 12000,
            "Schema": 22000,
            "术语映射": 10000,
            "关键路径与过滤约束": 12000,
            "正例示例": 15000,
            "反例提醒": 3000,
            "生成要求": 3000,
            "用户问题": 3000,
        }
        rendered: list[str] = []
        for name, content in sections:
            budget = section_budgets.get(name, 6000)
            clipped = self._clip_text(content, budget)
            if name == "system":
                rendered.append(clipped)
            else:
                rendered.append(f"【{name}】\n{clipped}")
        prompt = "\n\n".join(rendered).strip()
        if len(prompt) <= MAX_PROMPT_CHARS:
            return prompt
        overflow = len(prompt) - MAX_PROMPT_CHARS
        fallback_budget = max(2000, len(rendered[4]) - overflow - 200)
        rendered[4] = f"【正例示例】\n{self._clip_text(sections[4][1], fallback_budget)}"
        prompt = "\n\n".join(rendered).strip()
        return prompt[:MAX_PROMPT_CHARS]

    def _clip_text(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        lines = [line for line in text.splitlines() if line.strip()]
        kept: list[str] = []
        total = 0
        for line in lines:
            extra = len(line) + (1 if kept else 0)
            if total + extra > limit - 20:
                break
            kept.append(line)
            total += extra
        clipped = "\n".join(kept).strip()
        return f"{clipped}\n..." if clipped else text[: max(0, limit - 3)] + "..."
