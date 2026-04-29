import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.errors import AppError
from app.domain.knowledge.tree_service import KnowledgeTreeService
from app.storage.knowledge_store import KnowledgeStore


class KnowledgeTreeServiceTest(unittest.TestCase):
    def test_tree_groups_schema_and_markdown_by_business_object(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            store.write_versioned(
                "business_knowledge.md",
                store.read_text("business_knowledge.md"),
                """## Terminology Mapping

[id: network_element_alias]
[concept: NetworkElement]
[kind: business_semantic]
- “网元”对应 `NetworkElement`。

[id: tunnel_owner_path]
[concept: NetworkElement]
[kind: relation_path]
- “隧道所属网元”优先理解为 `(:NetworkElement)-[:FIBER_SRC]->(:Tunnel)`。
""",
                "seed test business blocks",
                "business_knowledge",
            )

            tree = KnowledgeTreeService(store).get_tree()

            object_root = next(node for node in tree if node["id"] == "group:business_objects")
            network_node = next(node for node in object_root["children"] if node["id"] == "concept:NetworkElement")
            child_ids = {child["id"] for child in network_node["children"]}
            self.assertIn("schema_label:NetworkElement", child_ids)
            self.assertIn("business_knowledge:network_element_alias", child_ids)
            self.assertIn("business_knowledge:tunnel_owner_path", child_ids)
            schema_child = next(child for child in network_node["children"] if child["id"] == "schema_label:NetworkElement")
            self.assertFalse(schema_child["editable"])

    def test_unclassified_blocks_are_visible(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            store.write_versioned(
                "business_knowledge.md",
                store.read_text("business_knowledge.md"),
                """## Terminology Mapping

[id: loose_rule]
- 没有 concept 元数据的知识仍然要能看到。
""",
                "seed loose block",
                "business_knowledge",
            )

            tree = KnowledgeTreeService(store).get_tree()

            unclassified = next(node for node in tree if node["id"] == "group:unclassified")
            self.assertTrue(any(child["id"] == "business_knowledge:loose_rule" for child in unclassified["children"]))

    def test_tree_supports_list_shaped_schema_file(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            (Path(tmp_dir) / "schema.json").write_text(
                json.dumps(
                    [
                        {
                            "label": "NetworkElement",
                            "type": "VERTEX",
                            "properties": [{"name": "id"}, {"name": "name"}],
                        },
                        {
                            "label": "FIBER_SRC",
                            "type": "EDGE",
                            "source": "NetworkElement",
                            "target": "Tunnel",
                        },
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            tree = KnowledgeTreeService(store).get_tree()

            object_root = next(node for node in tree if node["id"] == "group:business_objects")
            network_node = next(node for node in object_root["children"] if node["id"] == "concept:NetworkElement")
            schema_child = next(child for child in network_node["children"] if child["id"] == "schema_label:NetworkElement")
            self.assertIn("id", schema_child["content_preview"])

    def test_update_node_rewrites_markdown_block_and_history(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            original = """## Terminology Mapping

[id: network_element_alias]
[concept: NetworkElement]
[kind: business_semantic]
- old alias
"""
            store.write_versioned("business_knowledge.md", store.read_text("business_knowledge.md"), original, "seed", "business_knowledge")

            service = KnowledgeTreeService(store)
            node = service.update_node(
                "business_knowledge:network_element_alias",
                "[id: network_element_alias]\n[concept: NetworkElement]\n[kind: business_semantic]\n- new alias\n",
            )

            self.assertEqual(node["section_id"], "network_element_alias")
            self.assertIn("- new alias", store.read_text("business_knowledge.md"))
            self.assertTrue(list((Path(tmp_dir) / "_history").glob("*.json")))

    def test_create_relation_path_child_appends_block(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()

            node = KnowledgeTreeService(store).create_node(
                parent_id="concept:NetworkElement",
                title="网元 -> 隧道",
                kind="relation_path",
                content="- “隧道所属网元”优先理解为 NetworkElement -> Tunnel。\n",
                concept=None,
            )

            self.assertEqual(node["concept"], "NetworkElement")
            self.assertEqual(node["kind"], "relation_path")
            self.assertIn("[kind: relation_path]", store.read_text("business_knowledge.md"))

    def test_create_node_rejects_duplicate_knowledge_content(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            store.write_versioned(
                "business_knowledge.md",
                store.read_text("business_knowledge.md"),
                """## Terminology Mapping

[id: network_element_alias]
[concept: NetworkElement]
[kind: business_semantic]
- “网元”对应 `NetworkElement`。
""",
                "seed duplicate target",
                "business_knowledge",
            )

            with self.assertRaisesRegex(AppError, "duplicate"):
                KnowledgeTreeService(store).create_node(
                    parent_id="concept:NetworkElement",
                    title="重复网元定义",
                    kind="business_semantic",
                    content="-  “网元” 对应   `NetworkElement`。\n",
                    concept=None,
                )

    def test_update_node_rejects_duplicate_knowledge_content(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            store.write_versioned(
                "business_knowledge.md",
                store.read_text("business_knowledge.md"),
                """## Terminology Mapping

[id: network_element_alias]
[concept: NetworkElement]
[kind: business_semantic]
- “网元”对应 `NetworkElement`。

[id: other_alias]
[concept: NetworkElement]
[kind: business_semantic]
- other alias
""",
                "seed duplicate update target",
                "business_knowledge",
            )

            with self.assertRaisesRegex(AppError, "duplicate"):
                KnowledgeTreeService(store).update_node(
                    "business_knowledge:other_alias",
                    "[id: other_alias]\n[concept: NetworkElement]\n[kind: business_semantic]\n- “网元”对应 `NetworkElement`。\n",
                )

    def test_delete_node_removes_markdown_block(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            content = """## Terminology Mapping

[id: network_element_alias]
[concept: NetworkElement]
[kind: business_semantic]
- alias

[id: keep_me]
[concept: NetworkElement]
[kind: business_semantic]
- keep
"""
            store.write_versioned("business_knowledge.md", store.read_text("business_knowledge.md"), content, "seed", "business_knowledge")

            KnowledgeTreeService(store).delete_node("business_knowledge:network_element_alias")

            updated = store.read_text("business_knowledge.md")
            self.assertNotIn("network_element_alias", updated)
            self.assertIn("keep_me", updated)

    def test_schema_node_is_read_only(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()

            service = KnowledgeTreeService(store)
            with self.assertRaisesRegex(AppError, "read-only"):
                service.update_node("schema_label:NetworkElement", "Label: NetworkElement\n")
            with self.assertRaisesRegex(AppError, "read-only"):
                service.delete_node("schema_label:NetworkElement")
