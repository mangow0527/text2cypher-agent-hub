import unittest

from app.domain.knowledge.retriever import KnowledgeRetriever
from app.storage.knowledge_store import KnowledgeStore


class KnowledgeRetrieverTest(unittest.TestCase):
    def test_retrieves_protocol_related_notes(self) -> None:
        retriever = KnowledgeRetriever(KnowledgeStore())

        bundle = retriever.retrieve("查询协议版本为v2.0的隧道所属网元")

        self.assertIn("Protocol.version", bundle["business_context"])
        self.assertIn("TUNNEL_PROTO", bundle["few_shot_examples"])
        self.assertGreaterEqual(bundle["few_shot_examples"].count("Question:"), 1)
