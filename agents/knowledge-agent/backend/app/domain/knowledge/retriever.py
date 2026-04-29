from __future__ import annotations

import re

from app.domain.knowledge.schema_formatter import format_schema
from app.storage.knowledge_store import KnowledgeStore

GENERAL_QUERY_TYPE = "GENERAL"


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
        inferred_types = self._infer_query_types(question, lowered)
        scored_blocks: list[tuple[int, int, str]] = []

        for index, block in enumerate(example_blocks):
            block_types = self._block_query_types(block)
            score = self._type_score(inferred_types, block_types)
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
            if len(selected) >= 4:
                break

        if not selected and example_blocks:
            selected.append(example_blocks[-1])

        return "\n\n".join(selected) if selected else few_shot_text

    def _infer_query_types(self, question: str, lowered: str) -> set[str]:
        text = f"{question} {lowered}"
        query_types = {GENERAL_QUERY_TYPE, "MATCH_RETURN"}
        if re.search(r"统计|数量|多少|几个|总数|合计|求和|平均|最大|最小|count|sum|avg|min|max", text):
            query_types.add("AGGREGATION")
        if re.search(r"每个|各个|分别|按照|按|分组|group", text):
            query_types.add("GROUP_AGGREGATION")
        if re.search(r"前\s*\d*|top|排序|最高|最低|最近|最早|最大|最小|order|limit", text):
            query_types.add("ORDER_LIMIT")
        if re.search(r"过滤|条件|状态|版本|类型|等于|大于|小于|为|where|包含", text):
            query_types.add("WHERE_FILTER")
        if re.search(r"路径|经过|所属|连接|关联|连到|到达|上游|下游|path", text):
            query_types.add("PATH_TRAVERSAL")
        if re.search(r"两跳|多跳|三跳|经过.*再|multi[-_ ]?hop", text):
            query_types.add("MULTI_HOP")
        if re.search(r"去重|不同|唯一|distinct", text):
            query_types.add("DISTINCT_DEDUP")
        if re.search(r"with|中间结果|先.*再|分段|阶段", text):
            query_types.add("WITH_STAGE")
        if re.search(r"变长|任意跳|可达|variable", text):
            query_types.add("VARIABLE_LENGTH_PATH")
        return query_types

    def _block_query_types(self, block: str) -> set[str]:
        match = re.search(r"^\[types:\s*([^\]]+)\]\s*$", block, re.MULTILINE | re.IGNORECASE)
        if not match:
            return {GENERAL_QUERY_TYPE}
        parsed = {item.strip().upper() for item in match.group(1).split(",") if item.strip()}
        return parsed or {GENERAL_QUERY_TYPE}

    def _type_score(self, inferred_types: set[str], block_types: set[str]) -> int:
        overlap = inferred_types & block_types
        if overlap - {GENERAL_QUERY_TYPE}:
            return 10 + len(overlap)
        if GENERAL_QUERY_TYPE in block_types:
            return 2
        return 0
