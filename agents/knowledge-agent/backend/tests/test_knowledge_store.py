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
