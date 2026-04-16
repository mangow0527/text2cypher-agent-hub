import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.knowledge.repair_service import RepairService
from app.integrations.openai.model_gateway import ModelGateway
from app.storage.knowledge_store import KnowledgeStore


class FakeGateway(ModelGateway):
    def __init__(self) -> None:
        pass

    def generate_text(self, prompt_name: str, model_config: dict, **kwargs) -> str:
        return """{
  "doc_type": "business_knowledge",
  "section": "Terminology Mapping",
  "action": "add_section_item",
  "target_key": "protocol_version_mapping_2",
  "new_content": "- “协议版本”优先映射到 `Protocol.version`。",
  "reason": "补充业务语义映射"
}"""


class RepairServiceTest(unittest.TestCase):
    def test_apply_repair_updates_target_document(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            service = RepairService(store, FakeGateway())

            changes = service.apply("补充协议版本映射", ["business_knowledge"])

            content = store.read_text("business_knowledge.md")
            self.assertIn("protocol_version_mapping_2", content)
            self.assertEqual(changes[0]["doc_type"], "business_knowledge")
            self.assertEqual(changes[0]["section"], "Terminology Mapping")
            self.assertIn("protocol_version_mapping_2", changes[0]["after"])
