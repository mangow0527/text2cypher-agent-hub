import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.knowledge.retriever import KnowledgeRetriever
from app.storage.knowledge_store import KnowledgeStore


class KnowledgeRetrieverTest(unittest.TestCase):
    def test_retrieves_protocol_related_notes(self) -> None:
        retriever = KnowledgeRetriever(KnowledgeStore())

        bundle = retriever.retrieve("查询协议版本为v2.0的隧道所属网元")

        self.assertIn("Protocol.version", bundle["business_context"])
        self.assertIn("TUNNEL_PROTO", bundle["few_shot_examples"])
        self.assertGreaterEqual(bundle["few_shot_examples"].count("Question:"), 1)

    def test_schema_context_includes_full_property_metadata(self) -> None:
        retriever = KnowledgeRetriever(KnowledgeStore())

        bundle = retriever.retrieve("查询所有网元")

        self.assertIn("NetworkElement", bundle["schema_context"])
        self.assertIn("Network element (router/switch/firewall)", bundle["schema_context"])
        self.assertIn("primary: id", bundle["schema_context"])
        self.assertIn("id: STRING", bundle["schema_context"])
        self.assertIn("optional=false", bundle["schema_context"])
        self.assertIn("unique=true", bundle["schema_context"])
        self.assertIn("index=true", bundle["schema_context"])

    def test_retrieves_few_shots_by_inferred_cypher_query_type(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            (Path(tmp_dir) / "few_shot.md").write_text(
                "\n\n".join(
                    [
                        "## Reference Examples",
                        (
                            "[id: aggregate_ports_by_device]\n"
                            "[types: AGGREGATION, GROUP_AGGREGATION]\n"
                            "Question: 统计对象数量\n"
                            "Cypher: MATCH (n:NetworkElement)-[:HAS_PORT]->(p:Port) RETURN n.id, count(p) AS total\n"
                            "Why: 展示分组计数。"
                        ),
                        (
                            "[id: top_latency_tunnels]\n"
                            "[types: ORDER_LIMIT]\n"
                            "Question: 查询延迟最高的隧道前5个\n"
                            "Cypher: MATCH (t:Tunnel) RETURN t.id, t.latency ORDER BY t.latency DESC LIMIT 5\n"
                            "Why: 展示排序和限制。"
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            retriever = KnowledgeRetriever(store)

            bundle = retriever.retrieve("需要数量汇总")

            self.assertIn("aggregate_ports_by_device", bundle["few_shot_examples"])
            self.assertNotIn("top_latency_tunnels", bundle["few_shot_examples"])
