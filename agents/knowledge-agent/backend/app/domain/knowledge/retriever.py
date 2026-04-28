from __future__ import annotations

from app.domain.knowledge.schema_formatter import format_schema
from app.storage.knowledge_store import KnowledgeStore


class KnowledgeRetriever:
    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    def retrieve(self, question: str) -> dict[str, str]:
        syntax_text = self.store.read_text("cypher_syntax.md").strip()
        business_text = self.store.read_text("business_knowledge.md").strip()
        few_shot_text = self.store.read_text("few_shot.md").strip()
        lowered = question.lower()

        return {
            "schema_context": format_schema(self.store.read_schema()),
            "syntax_context": syntax_text,
            "business_context": self._filter_business_context(question, business_text),
            "few_shot_examples": self._filter_few_shot(question, lowered, few_shot_text),
            "system_prompt": self.store.read_text("system_prompt.md").strip(),
            "question": question,
        }

    def _filter_business_context(self, question: str, business_text: str) -> str:
        if "协议" not in question and "网元" not in question:
            return business_text

        selected = []
        for line in business_text.splitlines():
            if "协议" in question and ("协议" in line or "Protocol" in line):
                selected.append(line)
            elif "网元" in question and ("网元" in line or "NetworkElement" in line):
                selected.append(line)

        return "\n".join(selected).strip() or business_text

    def _filter_few_shot(self, question: str, lowered: str, few_shot_text: str) -> str:
        blocks = [block.strip() for block in few_shot_text.split("\n\n") if block.strip()]
        example_blocks = [block for block in blocks if block.startswith("[id:")]
        scored_blocks: list[tuple[int, int, str]] = []

        for index, block in enumerate(example_blocks):
            score = 0
            if "协议" in question or "protocol" in lowered:
                if "协议" in block or "Protocol" in block or "TUNNEL_PROTO" in block:
                    score += 3
            if "所属网元" in question and ("所属网元" in block or "FIBER_SRC" in block):
                score += 4
            if question in block:
                score += 5
            if "Anti-Pattern:" in block:
                score += 3
            if "Why Not:" in block:
                score += 2
            if score > 0:
                scored_blocks.append((score, index, block))

        selected: list[str] = []
        for _, _, block in sorted(scored_blocks, key=lambda item: (item[0], item[1]), reverse=True):
            if block not in selected:
                selected.append(block)
            if len(selected) >= 2:
                break

        if not selected and example_blocks:
            selected.append(example_blocks[-1])

        return "\n\n".join(selected) if selected else few_shot_text
