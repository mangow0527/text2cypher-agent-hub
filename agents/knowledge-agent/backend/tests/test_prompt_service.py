import unittest

from app.domain.knowledge.prompt_service import PromptService
from app.storage.knowledge_store import KnowledgeStore


class PromptServiceTest(unittest.TestCase):
    def test_build_prompt_includes_required_sections(self) -> None:
        service = PromptService(KnowledgeStore())

        prompt = service.build_prompt("查询协议版本为v2.0的隧道所属网元")

        self.assertIn("你是一个严格的 TuGraph Text2Cypher 生成器。", prompt)
        self.assertIn("【Schema】", prompt)
        self.assertIn("【TuGraph Cypher 语法约束】", prompt)
        self.assertIn("【业务知识】", prompt)
        self.assertIn("【参考示例】", prompt)
        self.assertIn("【生成要求】", prompt)
        self.assertTrue(prompt.rstrip().endswith("查询协议版本为v2.0的隧道所属网元"))
