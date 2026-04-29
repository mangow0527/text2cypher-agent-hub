import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.storage.knowledge_store import KnowledgeStore


class KnowledgeStoreTest(unittest.TestCase):
    def test_write_versioned_saves_snapshot_record(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            before = store.read_text("business_knowledge.md")
            after = before + "\n[id: extra]\n- extra\n"

            store.write_versioned("business_knowledge.md", before, after, "补充一条规则", "business_knowledge")

            history = list((Path(tmp_dir) / "_history").glob("*.json"))
            self.assertTrue(history)

    def test_lists_editable_and_read_only_documents(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()

            documents = store.list_documents()

            by_type = {item["doc_type"]: item for item in documents}
            self.assertTrue(by_type["business_knowledge"]["editable"])
            self.assertTrue(by_type["few_shot"]["editable"])
            self.assertFalse(by_type["schema"]["editable"])
            self.assertEqual(by_type["schema"]["filename"], "schema.json")

    def test_save_document_writes_history_for_editable_document(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()

            result = store.save_document("business_knowledge", "## Terminology Mapping\n\n[id: x]\n- x\n")

            self.assertEqual(result["doc_type"], "business_knowledge")
            self.assertIn("[id: x]", store.read_text("business_knowledge.md"))
            self.assertTrue(list((Path(tmp_dir) / "_history").glob("*.json")))

    def test_save_document_rejects_schema(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()

            with self.assertRaisesRegex(ValueError, "read-only"):
                store.save_document("schema", "{}")
