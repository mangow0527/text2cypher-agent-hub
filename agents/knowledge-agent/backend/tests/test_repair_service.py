import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.knowledge.repair_service import RepairService
from app.integrations.openai.model_gateway import ModelGateway
from app.storage.knowledge_store import KnowledgeStore


class FakeGateway(ModelGateway):
    def __init__(self) -> None:
        self.calls = []

    def generate_text(self, prompt_name: str, model_config: dict, **kwargs) -> str:
        self.calls.append({"prompt_name": prompt_name, "prompt": kwargs.get("prompt", "")})
        if prompt_name == "repair_analysis":
            return """{
  "intent_summary": "补充协议版本过滤的标准语义、路径和示例",
  "canonical_question_pattern": "查询协议版本为v2.0的隧道所属网元",
  "cypher_constraints": [
    "协议版本优先过滤 Protocol.version",
    "隧道所属网元优先使用 NetworkElement-[:FIBER_SRC]->Tunnel 路径",
    "查询需返回网元标识"
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
        raise AssertionError(f"unexpected prompt_name: {prompt_name}")


class GenericSuggestionGateway(ModelGateway):
    def __init__(self) -> None:
        self.calls = []

    def generate_text(self, prompt_name: str, model_config: dict, **kwargs) -> str:
        self.calls.append({"prompt_name": prompt_name, "prompt": kwargs.get("prompt", "")})
        return """{
  "intent_summary": "当问题询问资源所属对象时，返回归属主体而不是资源本身",
  "canonical_question_pattern": "查询某资源所属对象",
  "cypher_constraints": [
    "返回对象必须与问题询问的主体一致",
    "如果存在标准归属路径，优先使用标准归属路径"
  ],
  "business_mapping": [
    "“所属对象”应解释为资源的上游归属主体，而不是资源节点本身。"
  ],
  "positive_example": {
    "question": "查询某资源所属对象",
    "cypher": "MATCH (owner)-[:OWNS]->(resource) RETURN owner.id",
    "why": "展示返回归属主体的通用模式"
  },
  "negative_example": {
    "question": "查询某资源所属对象",
    "cypher": "MATCH (owner)-[:OWNS]->(resource) RETURN resource.id",
    "why_not": "错误地返回了资源本身"
  },
  "target_docs": ["business_knowledge", "few_shot", "cypher_syntax"]
}"""


class RepairServiceTest(unittest.TestCase):
    def test_apply_repair_updates_target_document(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            gateway = FakeGateway()
            service = RepairService(store, gateway)

            changes = service.apply("补充协议版本映射", ["business_knowledge"])

            content = store.read_text("business_knowledge.md")
            syntax_content = store.read_text("cypher_syntax.md")
            few_shot_content = store.read_text("few_shot.md")

            self.assertEqual([call["prompt_name"] for call in gateway.calls], ["repair_analysis"])
            self.assertEqual(
                {change["doc_type"] for change in changes},
                {"business_knowledge", "cypher_syntax", "few_shot"},
            )
            self.assertIn("Protocol.version", content)
            self.assertIn("FIBER_SRC", content)
            self.assertIn("Protocol.version", syntax_content)
            self.assertIn("NetworkElement-[:FIBER_SRC]->Tunnel", syntax_content)
            self.assertIn("Question: 查询协议版本为v2.0的隧道所属网元", few_shot_content)
            self.assertIn("Cypher: MATCH (n:NetworkElement)-[:FIBER_SRC]->(t:Tunnel)", few_shot_content)

    def test_apply_repair_logs_analysis_when_module_logs_are_enabled(self) -> None:
        class FakeModuleLogs:
            def __init__(self) -> None:
                self.records = []

            def append(self, **kwargs) -> None:
                self.records.append(kwargs)

        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            gateway = FakeGateway()
            module_logs = FakeModuleLogs()
            service = RepairService(store, gateway, module_logs=module_logs)

            service.apply("补充协议版本映射", ["business_knowledge"])

            operations = [record["operation"] for record in module_logs.records]
            self.assertIn("repair_analysis_requested", operations)
            self.assertIn("repair_analysis_generated", operations)
            self.assertIn("knowledge_document_updated", operations)

    def test_apply_repair_does_not_append_duplicate_knowledge_twice(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            gateway = FakeGateway()
            service = RepairService(store, gateway)

            service.apply("补充协议版本映射", ["business_knowledge"])
            business_after_first = store.read_text("business_knowledge.md")
            syntax_after_first = store.read_text("cypher_syntax.md")
            few_shot_after_first = store.read_text("few_shot.md")

            changes = service.apply("补充协议版本映射", ["business_knowledge"])

            self.assertEqual(store.read_text("business_knowledge.md"), business_after_first)
            self.assertEqual(store.read_text("cypher_syntax.md"), syntax_after_first)
            self.assertEqual(store.read_text("few_shot.md"), few_shot_after_first)
            self.assertEqual(changes, [])

    def test_apply_repair_accepts_generic_suggestion_without_schema_bindings(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            gateway = GenericSuggestionGateway()
            service = RepairService(store, gateway)

            changes = service.apply("补充资源所属对象的通用解释", ["business_knowledge"])

            business = store.read_text("business_knowledge.md")
            few_shot = store.read_text("few_shot.md")
            self.assertTrue(changes)
            self.assertIn("所属对象", business)
            self.assertIn("MATCH (owner)-[:OWNS]->(resource) RETURN owner.id", few_shot)
