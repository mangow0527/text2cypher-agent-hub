import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.knowledge.prompt_service import PromptService
from app.domain.knowledge.repair_service import RepairService
from app.integrations.openai.model_gateway import ModelGateway
from app.storage.knowledge_store import KnowledgeStore


class FakeAnalysisGateway(ModelGateway):
    def __init__(self) -> None:
        pass

    def generate_text(self, prompt_name: str, model_config: dict, **kwargs) -> str:
        return """{
  "intent_summary": "补充协议版本过滤后返回所属网元的稳定语义",
  "canonical_question_pattern": "查询协议版本为v2.0的隧道所属网元",
  "cypher_constraints": [
    "协议版本必须过滤 Protocol.version",
    "必须沿 NetworkElement-[:FIBER_SRC]->Tunnel-[:TUNNEL_PROTO]->Protocol 路径生成",
    "返回对象应为所属网元，而不是隧道本身"
  ],
  "schema_bindings": [
    {"term": "协议版本", "schema": "Protocol.version"},
    {"term": "隧道所属网元", "schema": "(:NetworkElement)-[:FIBER_SRC]->(:Tunnel)"}
  ],
  "business_mapping": [
    "“协议版本”优先映射到 `Protocol.version`。",
    "“隧道所属网元”优先理解为 `(:NetworkElement)-[:FIBER_SRC]->(:Tunnel)` 路径上的上游设备。"
  ],
  "positive_example": {
    "question": "查询协议版本为v2.0的隧道所属网元",
    "cypher": "MATCH (n:NetworkElement)-[:FIBER_SRC]->(t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol) WHERE p.version = 'v2.0' RETURN n.id",
    "why": "展示协议过滤后回到所属网元的标准路径"
  },
  "negative_example": {
    "question": "查询协议版本为v2.0的隧道所属网元",
    "cypher": "MATCH (t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol) WHERE p.version = 'v2.0' RETURN t.id",
    "why_not": "只返回隧道，没有回到所属网元"
  },
  "target_docs": ["business_knowledge", "cypher_syntax", "few_shot"]
}"""


class PromptServiceTest(unittest.TestCase):
    def test_build_prompt_includes_required_sections(self) -> None:
        service = PromptService(KnowledgeStore())

        prompt = service.build_prompt("查询协议版本为v2.0的隧道所属网元")

        self.assertIn("你是一个严格的 TuGraph Text2Cypher 生成器。", prompt)
        self.assertIn("【Schema】", prompt)
        self.assertIn("【术语映射】", prompt)
        self.assertIn("【关键路径与过滤约束】", prompt)
        self.assertIn("【正例示例】", prompt)
        self.assertIn("【反例提醒】", prompt)
        self.assertIn("【生成要求】", prompt)
        self.assertTrue(prompt.rstrip().endswith("查询协议版本为v2.0的隧道所属网元"))

    def test_build_prompt_surfaces_positive_and_negative_examples_after_repair(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            RepairService(store, FakeAnalysisGateway()).apply("补充协议版本映射", ["business_knowledge"])
            service = PromptService(store)

            prompt = service.build_prompt("查询协议版本为v2.0的隧道所属网元")

            self.assertIn("返回对象应为所属网元，而不是隧道本身", prompt)
            self.assertIn("MATCH (n:NetworkElement)-[:FIBER_SRC]->(t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol)", prompt)
            self.assertIn("MATCH (t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol) WHERE p.version = 'v2.0' RETURN t.id", prompt)
            self.assertIn("只返回隧道，没有回到所属网元", prompt)
            self.assertIn("生成前先校对术语映射、路径方向、过滤条件、返回对象", prompt)

    def test_build_prompt_is_capped_under_80000_characters(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()

            huge_business = "## Terminology Mapping\n\n" + "\n".join(
                f"[id: item_{idx}]\n- 术语{idx} 映射为 规则{idx}。" for idx in range(4000)
            )
            huge_syntax = "## Core Rules\n\n" + "\n".join(
                f"[id: rule_{idx}]\n- 约束{idx}：生成时保持严格方向和过滤。" for idx in range(4000)
            )
            huge_few_shot = "## Reference Examples\n\n" + "\n\n".join(
                (
                    f"[id: example_{idx}]\n"
                    f"Question: 示例问题{idx}\n"
                    f"Cypher: MATCH (n:Node{idx}) RETURN n.id\n"
                    f"Why: 示例说明{idx}"
                )
                for idx in range(1200)
            )

            (Path(tmp_dir) / "business_knowledge.md").write_text(huge_business, encoding="utf-8")
            (Path(tmp_dir) / "cypher_syntax.md").write_text(huge_syntax, encoding="utf-8")
            (Path(tmp_dir) / "few_shot.md").write_text(huge_few_shot, encoding="utf-8")

            service = PromptService(store)
            prompt = service.build_prompt("查询协议版本为v2.0的隧道所属网元")

            self.assertLessEqual(len(prompt), 80000)
