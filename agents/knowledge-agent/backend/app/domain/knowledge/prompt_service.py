from __future__ import annotations

from app.domain.knowledge.retriever import KnowledgeRetriever
from app.storage.knowledge_store import KnowledgeStore


class PromptService:
    def __init__(self, store: KnowledgeStore) -> None:
        self.retriever = KnowledgeRetriever(store)

    def build_prompt(self, question: str) -> str:
        bundle = self.retriever.retrieve(question)
        return (
            f"{bundle['system_prompt']}\n\n"
            "【Schema】\n"
            f"{bundle['schema_context']}\n\n"
            "【TuGraph Cypher 语法约束】\n"
            f"{bundle['syntax_context']}\n\n"
            "【业务知识】\n"
            f"{bundle['business_context']}\n\n"
            "【参考示例】\n"
            f"{bundle['few_shot_examples']}\n\n"
            "【生成要求】\n"
            "- 输出必须是单条 Cypher\n"
            "- 不要输出解释\n"
            "- 不要输出 Markdown\n"
            "- 确保方向、属性、过滤条件、聚合语义正确\n\n"
            "【用户问题】\n"
            f"{bundle['question']}"
        )
