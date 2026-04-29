# Knowledge Tree Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a simple editable knowledge tree for Knowledge Agent, replacing the fixed document-tab editor with a business-object-centered tree and a restrained intelligent-data-console visual style.

**Architecture:** Add a backend `KnowledgeTreeService` that projects existing knowledge files into a logical tree, supports node detail/update/create/delete, and writes changes through the existing versioned store. The frontend calls the new tree API and renders a two-pane tree manager: searchable tree on the left, selected node metadata/editor on the right. Storage remains file-based; schema-derived nodes remain read-only.

**Tech Stack:** Python 3, FastAPI, Pydantic, `unittest`, React 19, TypeScript, Vite, plain CSS. No graph visualization library and no new database.

---

## Product And Style Notes

Use a simple data-workbench style inspired by intelligent analytics tools:

- Power BI Copilot emphasizes semantic model grounding and shows how answers were derived from selected fields/measures.
- Tableau Pulse organizes AI exploration around metrics, insight summaries, and suggested follow-up questions.
- Metabase keeps query work step-based and inspectable, with clear source/query controls.
- ThoughtSpot search-led analytics centers the user on searchable business data rather than decorative UI.

Apply those cues conservatively:

- Use a light or neutral workspace, not the current dark gradient hero.
- Prefer dense, operational layout over marketing hero copy.
- Left navigation tree should feel like a semantic model/object browser.
- Right editor should make provenance obvious: source file, section id, editability, and save state.
- Buttons should be plain and predictable; avoid decorative or oversized cards.

References:

- Microsoft Power BI Copilot: `https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-ask-data-question`
- Tableau Pulse Q&A: `https://help.tableau.com/current/online/en-us/pulse_ask_discover_qa.htm`
- Metabase Query Builder: `https://www.metabase.com/docs/latest/questions/query-builder/editor`
- ThoughtSpot Search: `https://www.thoughtspot.com/product/search/`

## Files

- Create: `backend/app/domain/knowledge/tree_service.py`
  - Owns markdown block parsing, tree projection, node detail, update, create, and delete.
- Modify: `backend/app/domain/models.py`
  - Adds Pydantic request/response models for tree endpoints.
- Modify: `backend/app/entrypoints/api/main.py`
  - Instantiates `KnowledgeTreeService` and exposes `/api/knowledge/tree` endpoints.
- Create: `backend/tests/test_knowledge_tree_service.py`
  - Unit tests for tree generation and mutations.
- Modify: `backend/tests/test_api_contracts.py`
  - API contract tests for list/detail/update/create/delete.
- Modify: `frontend/src/lib/api.ts`
  - Adds tree API types and fetch helpers.
- Modify: `frontend/src/App.tsx`
  - Replaces the document-tab editor with the tree manager UI while keeping prompt and repair workflows.
- Modify: `frontend/src/index.css`
  - Reworks the visual style into a restrained intelligent-data-console layout.
- Keep: `backend/app/storage/knowledge_store.py`
  - Reuse `read_text`, `read_schema`, and `write_versioned`; do not move storage responsibilities into the API layer.

---

### Task 1: Backend Tree Read Model

**Files:**
- Create: `backend/app/domain/knowledge/tree_service.py`
- Modify: `backend/app/domain/models.py`
- Test: `backend/tests/test_knowledge_tree_service.py`

- [ ] **Step 1: Write failing tests for tree generation**

Create `backend/tests/test_knowledge_tree_service.py` with:

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent/backend
python3 -m unittest tests.test_knowledge_tree_service -v
```

Expected: fail with `ModuleNotFoundError` or missing `KnowledgeTreeService`.

- [ ] **Step 3: Add tree models**

In `backend/app/domain/models.py`, add after `KnowledgeDocumentDetailResponse`:

```python
KnowledgeTreeNodeKind = Literal[
    "group",
    "concept",
    "schema_label",
    "business_semantic",
    "relation_path",
    "few_shot",
    "rule",
]


class KnowledgeTreeNode(BaseModel):
    id: str
    parent_id: Optional[str] = None
    title: str
    kind: KnowledgeTreeNodeKind
    concept: Optional[str] = None
    source_file: Optional[str] = None
    section_id: Optional[str] = None
    editable: bool
    content_preview: str = ""
    children: list["KnowledgeTreeNode"] = Field(default_factory=list)


class KnowledgeTreeResponse(StatusResponse):
    tree: list[KnowledgeTreeNode]


class KnowledgeTreeNodeDetail(KnowledgeTreeNode):
    content: str = ""
    warning: Optional[str] = None


class KnowledgeTreeNodeDetailResponse(StatusResponse):
    node: KnowledgeTreeNodeDetail


class UpdateKnowledgeTreeNodeRequest(BaseModel):
    content: str


class CreateKnowledgeTreeNodeRequest(BaseModel):
    parent_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    kind: KnowledgeTreeNodeKind
    content: str = ""
    concept: Optional[str] = None


class KnowledgeTreeMutationResponse(StatusResponse):
    node: KnowledgeTreeNodeDetail
    tree: list[KnowledgeTreeNode]
```

Because the file uses `from __future__ import annotations`, the self-referential `children` annotation is valid.

- [ ] **Step 4: Implement read-only tree projection**

Create `backend/app/domain/knowledge/tree_service.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.errors import AppError
from app.storage.knowledge_store import KnowledgeStore


ID_RE = re.compile(r"^\[id:\s*([A-Za-z0-9_\-:.]+)\]\s*$")
META_RE = re.compile(r"^\[([A-Za-z_]+):\s*(.+?)\]\s*$")


@dataclass
class MarkdownBlock:
    source_file: str
    section_id: str
    content: str
    metadata: dict[str, str]


def _preview(content: str, limit: int = 96) -> str:
    collapsed = " ".join(line.strip() for line in content.splitlines() if line.strip())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[:limit]}..."


def _title_from_block(block: MarkdownBlock) -> str:
    lines = [line.strip() for line in block.content.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("["):
            continue
        return line.lstrip("- ").strip("`")[:64] or block.section_id
    return block.section_id


def _node(
    *,
    node_id: str,
    parent_id: str | None,
    title: str,
    kind: str,
    editable: bool,
    concept: str | None = None,
    source_file: str | None = None,
    section_id: str | None = None,
    content_preview: str = "",
    children: list[dict] | None = None,
) -> dict:
    return {
        "id": node_id,
        "parent_id": parent_id,
        "title": title,
        "kind": kind,
        "concept": concept,
        "source_file": source_file,
        "section_id": section_id,
        "editable": editable,
        "content_preview": content_preview,
        "children": children or [],
    }


class KnowledgeTreeService:
    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    def get_tree(self) -> list[dict]:
        schema = self.store.read_schema()
        business_blocks = self._parse_markdown_blocks("business_knowledge.md")
        few_shot_blocks = self._parse_markdown_blocks("few_shot.md")
        rule_blocks = self._parse_markdown_blocks("cypher_syntax.md") + self._parse_markdown_blocks("system_prompt.md")

        concepts = self._schema_concepts(schema)
        concepts.update(block.metadata["concept"] for block in business_blocks + few_shot_blocks if "concept" in block.metadata)

        business_root = _node(
            node_id="group:business_objects",
            parent_id=None,
            title="业务对象",
            kind="group",
            editable=False,
        )
        unclassified_root = _node(
            node_id="group:unclassified",
            parent_id=None,
            title="未分类",
            kind="group",
            editable=False,
        )
        rules_root = _node(
            node_id="group:rules",
            parent_id=None,
            title="通用规则",
            kind="group",
            editable=False,
        )

        for concept in sorted(concepts):
            concept_node = _node(
                node_id=f"concept:{concept}",
                parent_id=business_root["id"],
                title=concept,
                kind="concept",
                concept=concept,
                editable=True,
            )
            schema_node = self._schema_node(schema, concept, concept_node["id"])
            if schema_node:
                concept_node["children"].append(schema_node)
            for block in business_blocks + few_shot_blocks:
                if block.metadata.get("concept") == concept:
                    concept_node["children"].append(self._block_node(block, concept_node["id"]))
            business_root["children"].append(concept_node)

        known_attached = {
            child["id"]
            for concept_node in business_root["children"]
            for child in concept_node["children"]
        }
        for block in business_blocks + few_shot_blocks:
            block_node = self._block_node(block, unclassified_root["id"])
            if block_node["id"] not in known_attached:
                unclassified_root["children"].append(block_node)

        for block in rule_blocks:
            rules_root["children"].append(self._block_node(block, rules_root["id"], fallback_kind="rule"))

        return [business_root, rules_root, unclassified_root]

    def _schema_concepts(self, schema: dict) -> set[str]:
        return {item["name"] for item in schema.get("vertex_labels", []) if item.get("name")}

    def _schema_node(self, schema: dict, concept: str, parent_id: str) -> dict | None:
        label = next((item for item in schema.get("vertex_labels", []) if item.get("name") == concept), None)
        if not label:
            return None
        properties = label.get("properties", [])
        content = f"Label: {concept}\\nProperties: {', '.join(properties)}"
        return _node(
            node_id=f"schema_label:{concept}",
            parent_id=parent_id,
            title="Label / 属性",
            kind="schema_label",
            concept=concept,
            source_file="schema.json",
            editable=False,
            content_preview=_preview(content),
        )

    def _block_node(self, block: MarkdownBlock, parent_id: str, fallback_kind: str | None = None) -> dict:
        kind = block.metadata.get("kind") or fallback_kind or self._kind_from_source(block.source_file)
        return _node(
            node_id=f"{Path(block.source_file).stem}:{block.section_id}",
            parent_id=parent_id,
            title=_title_from_block(block),
            kind=kind,
            concept=block.metadata.get("concept"),
            source_file=block.source_file,
            section_id=block.section_id,
            editable=True,
            content_preview=_preview(block.content),
        )

    def _kind_from_source(self, source_file: str) -> str:
        if source_file == "few_shot.md":
            return "few_shot"
        return "business_semantic"

    def _parse_markdown_blocks(self, filename: str) -> list[MarkdownBlock]:
        text = self.store.read_text(filename)
        blocks: list[MarkdownBlock] = []
        current: list[str] = []
        current_id: str | None = None

        def flush() -> None:
            nonlocal current, current_id
            if current_id and current:
                content = "\\n".join(current).strip() + "\\n"
                metadata: dict[str, str] = {}
                for line in current:
                    match = META_RE.match(line.strip())
                    if match and match.group(1) != "id":
                        metadata[match.group(1)] = match.group(2)
                blocks.append(MarkdownBlock(filename, current_id, content, metadata))
            current = []
            current_id = None

        for line in text.splitlines():
            id_match = ID_RE.match(line.strip())
            if id_match:
                flush()
                current_id = id_match.group(1)
                current = [line]
            elif current_id:
                current.append(line)
        flush()
        return blocks
```

- [ ] **Step 5: Run tree tests**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent/backend
python3 -m unittest tests.test_knowledge_tree_service -v
```

Expected: both tests pass.

- [ ] **Step 6: Commit**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent
git add backend/app/domain/models.py backend/app/domain/knowledge/tree_service.py backend/tests/test_knowledge_tree_service.py
git commit -m "feat: add knowledge tree read model"
```

---

### Task 2: Backend Tree Mutations

**Files:**
- Modify: `backend/app/domain/knowledge/tree_service.py`
- Test: `backend/tests/test_knowledge_tree_service.py`

- [ ] **Step 1: Add failing mutation tests**

Append to `KnowledgeTreeServiceTest`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent/backend
python3 -m unittest tests.test_knowledge_tree_service -v
```

Expected: fail because `update_node`, `create_node`, and `delete_node` do not exist.

- [ ] **Step 3: Add mutation methods**

In `KnowledgeTreeService`, add these public methods:

```python
    def get_node_detail(self, node_id: str) -> dict:
        for node in self._flatten(self.get_tree()):
            if node["id"] == node_id:
                content = self._content_for_node(node)
                return {**node, "content": content, "warning": None}
        raise AppError("KNOWLEDGE_TREE_NODE_NOT_FOUND", f"Knowledge tree node not found: {node_id}")

    def update_node(self, node_id: str, content: str) -> dict:
        node = self.get_node_detail(node_id)
        if not node["editable"]:
            raise AppError("KNOWLEDGE_TREE_NODE_READ_ONLY", f"Knowledge tree node is read-only: {node_id}")
        source_file = node["source_file"]
        section_id = node["section_id"]
        if not source_file or not section_id:
            raise AppError("KNOWLEDGE_TREE_NODE_READ_ONLY", f"Knowledge tree node cannot be edited: {node_id}")
        before = self.store.read_text(source_file)
        after = self._replace_block(before, section_id, content)
        self.store.write_versioned(source_file, before, after, "manual knowledge tree node update", node["kind"])
        return self.get_node_detail(node_id)

    def create_node(self, parent_id: str, title: str, kind: str, content: str, concept: str | None = None) -> dict:
        parent = self.get_node_detail(parent_id)
        target_concept = concept or parent.get("concept")
        if parent["kind"] not in {"concept", "business_semantic", "relation_path", "few_shot", "rule", "group"}:
            raise AppError("KNOWLEDGE_TREE_INVALID_PARENT", f"Cannot add child node under: {parent_id}")
        if kind in {"business_semantic", "relation_path"}:
            source_file = "business_knowledge.md"
        elif kind == "few_shot":
            source_file = "few_shot.md"
        elif kind == "rule":
            source_file = "cypher_syntax.md"
        else:
            raise AppError("KNOWLEDGE_TREE_INVALID_PARENT", f"Cannot create node kind: {kind}")
        section_id = self._new_section_id(title)
        block = self._format_new_block(section_id, kind, target_concept, content)
        before = self.store.read_text(source_file)
        separator = "" if before.endswith("\\n\\n") else "\\n"
        after = f"{before}{separator}{block}"
        self.store.write_versioned(source_file, before, after, "manual knowledge tree node create", kind)
        return self.get_node_detail(f"{Path(source_file).stem}:{section_id}")

    def delete_node(self, node_id: str) -> None:
        node = self.get_node_detail(node_id)
        if not node["editable"]:
            raise AppError("KNOWLEDGE_TREE_NODE_READ_ONLY", f"Knowledge tree node is read-only: {node_id}")
        source_file = node["source_file"]
        section_id = node["section_id"]
        if not source_file or not section_id:
            raise AppError("KNOWLEDGE_TREE_NODE_READ_ONLY", f"Knowledge tree node cannot be deleted: {node_id}")
        before = self.store.read_text(source_file)
        after = self._remove_block(before, section_id)
        self.store.write_versioned(source_file, before, after, "manual knowledge tree node delete", node["kind"])
```

Add helper methods in the same class:

```python
    def _flatten(self, nodes: list[dict]) -> list[dict]:
        result: list[dict] = []
        for node in nodes:
            result.append(node)
            result.extend(self._flatten(node["children"]))
        return result

    def _content_for_node(self, node: dict) -> str:
        if node["kind"] == "schema_label" and node["concept"]:
            schema_node = self._schema_node(self.store.read_schema(), node["concept"], node["parent_id"] or "")
            return schema_node["content_preview"] if schema_node else ""
        source_file = node.get("source_file")
        section_id = node.get("section_id")
        if source_file and section_id:
            block = self._find_block(source_file, section_id)
            return block.content
        return node.get("content_preview", "")

    def _find_block(self, source_file: str, section_id: str) -> MarkdownBlock:
        for block in self._parse_markdown_blocks(source_file):
            if block.section_id == section_id:
                return block
        raise AppError("KNOWLEDGE_TREE_NODE_NOT_FOUND", f"Knowledge block not found: {source_file}#{section_id}")

    def _replace_block(self, text: str, section_id: str, replacement: str) -> str:
        return self._rewrite_block(text, section_id, replacement.strip() + "\\n", remove=False)

    def _remove_block(self, text: str, section_id: str) -> str:
        return self._rewrite_block(text, section_id, "", remove=True)

    def _rewrite_block(self, text: str, section_id: str, replacement: str, remove: bool) -> str:
        lines = text.splitlines()
        ranges: list[tuple[int, int]] = []
        start: int | None = None
        current_id: str | None = None
        for index, line in enumerate(lines):
            match = ID_RE.match(line.strip())
            if match:
                if current_id:
                    ranges.append((start or 0, index))
                current_id = match.group(1)
                start = index
        if current_id:
            ranges.append((start or 0, len(lines)))
        for start_index, end_index in ranges:
            if ID_RE.match(lines[start_index].strip()).group(1) == section_id:
                replacement_lines = [] if remove else replacement.rstrip("\\n").splitlines()
                new_lines = lines[:start_index] + replacement_lines + lines[end_index:]
                return "\\n".join(new_lines).strip() + "\\n"
        raise AppError("KNOWLEDGE_TREE_NODE_NOT_FOUND", f"Knowledge block not found: {section_id}")

    def _new_section_id(self, title: str) -> str:
        base = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_").lower() or "knowledge_node"
        existing = {block.section_id for filename in ("business_knowledge.md", "few_shot.md", "cypher_syntax.md") for block in self._parse_markdown_blocks(filename)}
        candidate = base
        index = 2
        while candidate in existing:
            candidate = f"{base}_{index}"
            index += 1
        return candidate

    def _format_new_block(self, section_id: str, kind: str, concept: str | None, content: str) -> str:
        lines = [f"[id: {section_id}]"]
        if concept:
            lines.append(f"[concept: {concept}]")
        lines.append(f"[kind: {kind}]")
        lines.append(content.strip())
        return "\\n".join(lines).strip() + "\\n"
```

- [ ] **Step 4: Run mutation tests**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent/backend
python3 -m unittest tests.test_knowledge_tree_service -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent
git add backend/app/domain/knowledge/tree_service.py backend/tests/test_knowledge_tree_service.py
git commit -m "feat: support knowledge tree mutations"
```

---

### Task 3: Tree API Contracts

**Files:**
- Modify: `backend/app/entrypoints/api/main.py`
- Modify: `backend/tests/test_api_contracts.py`

- [ ] **Step 1: Add failing API tests**

Append to `ApiContractTest` in `backend/tests/test_api_contracts.py`:

```python
    def test_knowledge_tree_contracts(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            with patch("app.entrypoints.api.main.knowledge_store", store):
                from app.domain.knowledge.tree_service import KnowledgeTreeService

                with patch("app.entrypoints.api.main.knowledge_tree_service", KnowledgeTreeService(store)):
                    tree_response = self.client.get("/api/knowledge/tree")
                    detail_response = self.client.get("/api/knowledge/tree/nodes/schema_label:NetworkElement")
                    create_response = self.client.post(
                        "/api/knowledge/tree/nodes",
                        json={
                            "parent_id": "concept:NetworkElement",
                            "title": "网元别名",
                            "kind": "business_semantic",
                            "content": "- “设备”可以指网元。\n",
                        },
                    )
                    created_id = create_response.json()["node"]["id"]
                    update_response = self.client.put(
                        f"/api/knowledge/tree/nodes/{created_id}",
                        json={
                            "content": "[id: 网元别名]\n[concept: NetworkElement]\n[kind: business_semantic]\n- updated\n"
                        },
                    )
                    delete_response = self.client.delete(f"/api/knowledge/tree/nodes/{created_id}")

            self.assertEqual(tree_response.status_code, 200)
            self.assertEqual(tree_response.json()["status"], "ok")
            self.assertTrue(tree_response.json()["tree"])
            self.assertEqual(detail_response.status_code, 200)
            self.assertFalse(detail_response.json()["node"]["editable"])
            self.assertEqual(create_response.status_code, 200)
            self.assertEqual(create_response.json()["node"]["kind"], "business_semantic")
            self.assertEqual(update_response.status_code, 200)
            self.assertIn("updated", update_response.json()["node"]["content"])
            self.assertEqual(delete_response.status_code, 200)
            self.assertEqual(delete_response.json()["status"], "ok")

    def test_knowledge_tree_rejects_schema_edit_contract(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            with patch("app.entrypoints.api.main.knowledge_store", store):
                from app.domain.knowledge.tree_service import KnowledgeTreeService

                with patch("app.entrypoints.api.main.knowledge_tree_service", KnowledgeTreeService(store)):
                    response = self.client.put(
                        "/api/knowledge/tree/nodes/schema_label:NetworkElement",
                        json={"content": "not allowed"},
                    )

            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.json()["status"], "error")
            self.assertEqual(response.json()["code"], "KNOWLEDGE_TREE_NODE_READ_ONLY")
```

- [ ] **Step 2: Run API tests to verify they fail**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent/backend
python3 -m unittest tests.test_api_contracts.ApiContractTest.test_knowledge_tree_contracts tests.test_api_contracts.ApiContractTest.test_knowledge_tree_rejects_schema_edit_contract -v
```

Expected: fail with 404 for missing endpoints or missing `knowledge_tree_service`.

- [ ] **Step 3: Wire service and routes**

In `backend/app/entrypoints/api/main.py`, add import:

```python
from app.domain.knowledge.tree_service import KnowledgeTreeService
```

Extend the models import list with:

```python
    CreateKnowledgeTreeNodeRequest,
    KnowledgeTreeMutationResponse,
    KnowledgeTreeNodeDetailResponse,
    KnowledgeTreeResponse,
    UpdateKnowledgeTreeNodeRequest,
```

After `knowledge_store = KnowledgeStore()`, add:

```python
knowledge_tree_service = KnowledgeTreeService(knowledge_store)
```

Add routes after document endpoints:

```python
@app.get("/api/knowledge/tree", response_model=KnowledgeTreeResponse)
def get_knowledge_tree() -> KnowledgeTreeResponse:
    return KnowledgeTreeResponse(status="ok", tree=knowledge_tree_service.get_tree())


@app.get("/api/knowledge/tree/nodes/{node_id:path}", response_model=KnowledgeTreeNodeDetailResponse)
def get_knowledge_tree_node(node_id: str) -> KnowledgeTreeNodeDetailResponse:
    node = knowledge_tree_service.get_node_detail(node_id)
    return KnowledgeTreeNodeDetailResponse(status="ok", node=node)


@app.put("/api/knowledge/tree/nodes/{node_id:path}", response_model=KnowledgeTreeMutationResponse)
def update_knowledge_tree_node(node_id: str, request: UpdateKnowledgeTreeNodeRequest) -> KnowledgeTreeMutationResponse:
    node = knowledge_tree_service.update_node(node_id, request.content)
    return KnowledgeTreeMutationResponse(status="ok", node=node, tree=knowledge_tree_service.get_tree())


@app.post("/api/knowledge/tree/nodes", response_model=KnowledgeTreeMutationResponse)
def create_knowledge_tree_node(request: CreateKnowledgeTreeNodeRequest) -> KnowledgeTreeMutationResponse:
    node = knowledge_tree_service.create_node(
        parent_id=request.parent_id,
        title=request.title,
        kind=request.kind,
        content=request.content,
        concept=request.concept,
    )
    return KnowledgeTreeMutationResponse(status="ok", node=node, tree=knowledge_tree_service.get_tree())


@app.delete("/api/knowledge/tree/nodes/{node_id:path}", response_model=StatusResponse)
def delete_knowledge_tree_node(node_id: str) -> StatusResponse:
    knowledge_tree_service.delete_node(node_id)
    return StatusResponse(status="ok")
```

Also import `StatusResponse` from `app.domain.models` if it is not already imported.

- [ ] **Step 4: Run API tests**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent/backend
python3 -m unittest tests.test_api_contracts -v
```

Expected: all API contract tests pass.

- [ ] **Step 5: Commit**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent
git add backend/app/entrypoints/api/main.py backend/tests/test_api_contracts.py
git commit -m "feat: expose knowledge tree api"
```

---

### Task 4: Frontend Tree API Client

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Add TypeScript tree types**

In `frontend/src/lib/api.ts`, add after `KnowledgeDocumentDetail`:

```ts
export type KnowledgeTreeNodeKind =
  | "group"
  | "concept"
  | "schema_label"
  | "business_semantic"
  | "relation_path"
  | "few_shot"
  | "rule";

export interface KnowledgeTreeNode {
  id: string;
  parent_id: string | null;
  title: string;
  kind: KnowledgeTreeNodeKind;
  concept: string | null;
  source_file: string | null;
  section_id: string | null;
  editable: boolean;
  content_preview: string;
  children: KnowledgeTreeNode[];
}

export interface KnowledgeTreeNodeDetail extends KnowledgeTreeNode {
  content: string;
  warning: string | null;
}

export interface KnowledgeTreeResponse {
  status: "ok";
  tree: KnowledgeTreeNode[];
}

export interface KnowledgeTreeNodeDetailResponse {
  status: "ok";
  node: KnowledgeTreeNodeDetail;
}

export interface KnowledgeTreeMutationResponse {
  status: "ok";
  node: KnowledgeTreeNodeDetail;
  tree: KnowledgeTreeNode[];
}
```

- [ ] **Step 2: Add API helpers**

Append these functions to `frontend/src/lib/api.ts`:

```ts
export async function fetchKnowledgeTree(): Promise<KnowledgeTreeResponse> {
  const response = await fetch(`${API_BASE}/api/knowledge/tree`);
  return parseJsonOrThrow(response);
}

export async function fetchKnowledgeTreeNode(nodeId: string): Promise<KnowledgeTreeNodeDetailResponse> {
  const response = await fetch(`${API_BASE}/api/knowledge/tree/nodes/${encodeURIComponent(nodeId)}`);
  return parseJsonOrThrow(response);
}

export async function updateKnowledgeTreeNode(
  nodeId: string,
  content: string,
): Promise<KnowledgeTreeMutationResponse> {
  const response = await fetch(`${API_BASE}/api/knowledge/tree/nodes/${encodeURIComponent(nodeId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  return parseJsonOrThrow(response);
}

export async function createKnowledgeTreeNode(payload: {
  parent_id: string;
  title: string;
  kind: KnowledgeTreeNodeKind;
  content: string;
  concept?: string | null;
}): Promise<KnowledgeTreeMutationResponse> {
  const response = await fetch(`${API_BASE}/api/knowledge/tree/nodes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response);
}

export async function deleteKnowledgeTreeNode(nodeId: string): Promise<{ status: "ok" }> {
  const response = await fetch(`${API_BASE}/api/knowledge/tree/nodes/${encodeURIComponent(nodeId)}`, {
    method: "DELETE",
  });
  return parseJsonOrThrow(response);
}
```

- [ ] **Step 3: Run frontend type check**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent/frontend
npm run build
```

Expected: build passes.

- [ ] **Step 4: Commit**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent
git add frontend/src/lib/api.ts
git commit -m "feat: add knowledge tree api client"
```

---

### Task 5: Frontend Tree Manager UI

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Replace document-editor state with tree state**

In `frontend/src/App.tsx`, remove state tied to document list/detail/content/status:

```ts
const [documents, setDocuments] = useState<KnowledgeDocumentSummary[]>([]);
const [selectedDocType, setSelectedDocType] = useState<KnowledgeDocumentType>("business_knowledge");
const [documentDetail, setDocumentDetail] = useState<KnowledgeDocumentDetail | null>(null);
const [documentContent, setDocumentContent] = useState("");
const [documentBusy, setDocumentBusy] = useState(false);
const [documentSaveBusy, setDocumentSaveBusy] = useState(false);
const [documentError, setDocumentError] = useState("");
const [documentStatus, setDocumentStatus] = useState("");
```

Add tree state:

```ts
const [tree, setTree] = useState<KnowledgeTreeNode[]>([]);
const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(() => new Set(["group:business_objects"]));
const [selectedNodeId, setSelectedNodeId] = useState("");
const [selectedNode, setSelectedNode] = useState<KnowledgeTreeNodeDetail | null>(null);
const [treeContent, setTreeContent] = useState("");
const [treeBusy, setTreeBusy] = useState(false);
const [treeSaveBusy, setTreeSaveBusy] = useState(false);
const [treeError, setTreeError] = useState("");
const [treeStatus, setTreeStatus] = useState("");
const [treeSearch, setTreeSearch] = useState("");
```

Update imports from `./lib/api` to include:

```ts
  deleteKnowledgeTreeNode,
  fetchKnowledgeTree,
  fetchKnowledgeTreeNode,
  updateKnowledgeTreeNode,
```

Update type imports to include:

```ts
  KnowledgeTreeNode,
  KnowledgeTreeNodeDetail,
```

- [ ] **Step 2: Add tree loading effects**

Add these effects after repair handlers:

```ts
useEffect(() => {
  let active = true;

  async function loadTree() {
    setTreeBusy(true);
    setTreeError("");
    try {
      const response = await fetchKnowledgeTree();
      if (!active) {
        return;
      }
      setTree(response.tree);
      const firstEditable = findFirstNode(response.tree, (node) => node.editable) ?? findFirstNode(response.tree, () => true);
      if (firstEditable && !selectedNodeId) {
        setSelectedNodeId(firstEditable.id);
      }
    } catch (error) {
      if (active) {
        setTreeError(error instanceof Error ? error.message : "获取知识树失败");
      }
    } finally {
      if (active) {
        setTreeBusy(false);
      }
    }
  }

  loadTree();
  return () => {
    active = false;
  };
}, [selectedNodeId]);

useEffect(() => {
  if (!selectedNodeId) {
    return;
  }
  let active = true;

  async function loadNode() {
    setTreeBusy(true);
    setTreeError("");
    setTreeStatus("");
    try {
      const response = await fetchKnowledgeTreeNode(selectedNodeId);
      if (!active) {
        return;
      }
      setSelectedNode(response.node);
      setTreeContent(response.node.content);
    } catch (error) {
      if (active) {
        setTreeError(error instanceof Error ? error.message : "读取知识节点失败");
        setSelectedNode(null);
        setTreeContent("");
      }
    } finally {
      if (active) {
        setTreeBusy(false);
      }
    }
  }

  loadNode();
  return () => {
    active = false;
  };
}, [selectedNodeId]);
```

- [ ] **Step 3: Add tree handlers and helper functions**

Inside `App`, add:

```ts
function toggleExpanded(nodeId: string) {
  setExpandedNodeIds((current) => {
    const next = new Set(current);
    if (next.has(nodeId)) {
      next.delete(nodeId);
    } else {
      next.add(nodeId);
    }
    return next;
  });
}

async function handleTreeSave() {
  if (!selectedNode?.editable) {
    return;
  }
  setTreeSaveBusy(true);
  setTreeError("");
  setTreeStatus("");
  try {
    const response = await updateKnowledgeTreeNode(selectedNode.id, treeContent);
    setTree(response.tree);
    setSelectedNode(response.node);
    setTreeContent(response.node.content);
    setTreeStatus("节点已保存");
  } catch (error) {
    setTreeError(error instanceof Error ? error.message : "保存知识节点失败");
  } finally {
    setTreeSaveBusy(false);
  }
}

async function handleTreeDelete() {
  if (!selectedNode?.editable) {
    return;
  }
  setTreeSaveBusy(true);
  setTreeError("");
  setTreeStatus("");
  try {
    await deleteKnowledgeTreeNode(selectedNode.id);
    const response = await fetchKnowledgeTree();
    setTree(response.tree);
    const nextNode = findFirstNode(response.tree, (node) => node.editable) ?? findFirstNode(response.tree, () => true);
    setSelectedNodeId(nextNode?.id ?? "");
    setTreeStatus("节点已删除");
  } catch (error) {
    setTreeError(error instanceof Error ? error.message : "删除知识节点失败");
  } finally {
    setTreeSaveBusy(false);
  }
}
```

After `formatDateTime`, add:

```ts
function findFirstNode(
  nodes: KnowledgeTreeNode[],
  predicate: (node: KnowledgeTreeNode) => boolean,
): KnowledgeTreeNode | null {
  for (const node of nodes) {
    if (predicate(node)) {
      return node;
    }
    const child = findFirstNode(node.children, predicate);
    if (child) {
      return child;
    }
  }
  return null;
}

function filterTree(nodes: KnowledgeTreeNode[], search: string): KnowledgeTreeNode[] {
  const keyword = search.trim().toLowerCase();
  if (!keyword) {
    return nodes;
  }
  return nodes
    .map((node) => {
      const children = filterTree(node.children, search);
      const matches =
        node.title.toLowerCase().includes(keyword) ||
        node.content_preview.toLowerCase().includes(keyword) ||
        (node.concept ?? "").toLowerCase().includes(keyword);
      return matches || children.length ? { ...node, children } : null;
    })
    .filter((node): node is KnowledgeTreeNode => Boolean(node));
}
```

- [ ] **Step 4: Replace editor JSX with tree manager JSX**

Replace the entire `<section className="surface surface-editor">...</section>` block with:

```tsx
<section className="knowledge-console">
  <div className="console-toolbar">
    <div>
      <p className="surface-kicker">03 · 知识树管理</p>
      <h2>按业务对象维护知识关系</h2>
    </div>
    <div className="editor-status">
      {treeBusy ? "读取中" : selectedNode?.editable ? (treeContent !== selectedNode.content ? "有未保存修改" : "已同步") : "只读"}
    </div>
  </div>

  <div className="tree-manager">
    <aside className="tree-panel">
      <div className="tree-search-row">
        <input
          value={treeSearch}
          onChange={(event) => setTreeSearch(event.target.value)}
          placeholder="搜索对象、路径、示例"
        />
      </div>
      <div className="tree-list">
        {filterTree(tree, treeSearch).map((node) => (
          <TreeNodeView
            key={node.id}
            node={node}
            depth={0}
            expandedNodeIds={expandedNodeIds}
            selectedNodeId={selectedNodeId}
            onToggle={toggleExpanded}
            onSelect={setSelectedNodeId}
          />
        ))}
      </div>
    </aside>

    <section className="node-detail">
      <div className="node-detail-header">
        <div>
          <span className="node-kind">{selectedNode?.kind ?? "knowledge"}</span>
          <h3>{selectedNode?.title ?? "选择一个知识节点"}</h3>
          <p>
            {selectedNode
              ? `${selectedNode.source_file ?? "logical tree"}${selectedNode.section_id ? ` · ${selectedNode.section_id}` : ""}`
              : "从左侧知识树选择对象、路径或示例。"}
          </p>
        </div>
        {selectedNode ? (
          <span className={`node-state ${selectedNode.editable ? "node-state-editable" : "node-state-readonly"}`}>
            {selectedNode.editable ? "可编辑" : "只读"}
          </span>
        ) : null}
      </div>

      {treeError ? <p className="error-banner">{treeError}</p> : null}
      {treeStatus ? <p className="success-banner">{treeStatus}</p> : null}

      {selectedNode ? (
        <div className="node-meta-grid">
          <span>对象：{selectedNode.concept ?? "无"}</span>
          <span>类型：{selectedNode.kind}</span>
          <span>来源：{selectedNode.source_file ?? "派生节点"}</span>
          <span>写回：{selectedNode.section_id ?? "不写回"}</span>
        </div>
      ) : null}

      {selectedNode?.editable ? (
        <textarea
          className="node-editor"
          value={treeContent}
          onChange={(event) => setTreeContent(event.target.value)}
          spellCheck={false}
        />
      ) : (
        <pre className="node-viewer">{treeContent || "只读节点内容会显示在这里。"}</pre>
      )}

      <div className="node-actions">
        <button type="button" className="secondary-action" disabled={!selectedNode?.editable || treeSaveBusy} onClick={handleTreeDelete}>
          删除节点
        </button>
        <button type="button" className="primary-action document-save" disabled={!selectedNode?.editable || treeContent === selectedNode?.content || treeSaveBusy} onClick={handleTreeSave}>
          {treeSaveBusy ? "保存中..." : "保存节点"}
        </button>
      </div>
    </section>
  </div>
</section>
```

Before `formatDateTime`, add the recursive component:

```tsx
function TreeNodeView({
  node,
  depth,
  expandedNodeIds,
  selectedNodeId,
  onToggle,
  onSelect,
}: {
  node: KnowledgeTreeNode;
  depth: number;
  expandedNodeIds: Set<string>;
  selectedNodeId: string;
  onToggle: (nodeId: string) => void;
  onSelect: (nodeId: string) => void;
}) {
  const expanded = expandedNodeIds.has(node.id);
  const hasChildren = node.children.length > 0;
  return (
    <div>
      <div
        className={`tree-row ${selectedNodeId === node.id ? "tree-row-active" : ""}`}
        style={{ paddingLeft: `${12 + depth * 18}px` }}
      >
        <button
          type="button"
          className="tree-expander"
          onClick={() => (hasChildren ? onToggle(node.id) : onSelect(node.id))}
          aria-label={hasChildren ? "Toggle node" : "Select node"}
        >
          {hasChildren ? (expanded ? "▾" : "▸") : "•"}
        </button>
        <button type="button" className="tree-label" onClick={() => onSelect(node.id)}>
          <span>{node.title}</span>
          <small>{node.editable ? "编辑" : "只读"}</small>
        </button>
      </div>
      {hasChildren && expanded
        ? node.children.map((child) => (
            <TreeNodeView
              key={child.id}
              node={child}
              depth={depth + 1}
              expandedNodeIds={expandedNodeIds}
              selectedNodeId={selectedNodeId}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))
        : null}
    </div>
  );
}
```

- [ ] **Step 5: Remove unused document imports**

Remove these imports from `frontend/src/App.tsx`:

```ts
  fetchKnowledgeDocument,
  listKnowledgeDocuments,
  saveKnowledgeDocument,
```

Remove these type imports:

```ts
KnowledgeDocumentDetail, KnowledgeDocumentSummary, KnowledgeDocumentType
```

- [ ] **Step 6: Run frontend build**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent/frontend
npm run build
```

Expected: build passes.

- [ ] **Step 7: Commit**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent
git add frontend/src/App.tsx
git commit -m "feat: replace document editor with knowledge tree"
```

---

### Task 6: Frontend Visual Style Refresh

**Files:**
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Replace decorative app shell with data console foundation**

In `frontend/src/index.css`, replace the top-level visual foundation:

```css
:root {
  font-family: Inter, "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif;
  color: #172033;
  background: #f5f7fb;
  font-synthesis: none;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.app-shell {
  min-height: 100vh;
  padding: 24px;
  background: #f5f7fb;
}

.ambient,
.hero-aside {
  display: none;
}

.hero {
  display: block;
  max-width: 1440px;
  margin: 0 auto 16px;
  padding: 0;
}

.hero-copy h1 {
  margin: 0;
  max-width: 980px;
  color: #101828;
  font-family: inherit;
  font-size: 1.8rem;
  line-height: 1.2;
  letter-spacing: 0;
}

.hero-text {
  max-width: 940px;
  margin: 8px 0 0;
  color: #667085;
  font-size: 0.95rem;
  line-height: 1.6;
}
```

- [ ] **Step 2: Restyle surfaces as work panels**

Replace `.workspace`, `.surface`, form, and result styles with:

```css
.workspace {
  display: grid;
  grid-template-columns: minmax(360px, 0.9fr) minmax(420px, 1.1fr);
  gap: 16px;
  max-width: 1440px;
  margin: 0 auto;
}

.surface,
.knowledge-console {
  border: 1px solid #d9e2ef;
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.05);
}

.surface {
  padding: 18px;
}

.surface-header h2,
.console-toolbar h2 {
  margin: 0;
  color: #101828;
  font-size: 1rem;
  line-height: 1.35;
}

.eyebrow,
.surface-kicker,
.signal-label,
.diff-label {
  margin: 0 0 6px;
  color: #476582;
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.field > span {
  color: #344054;
  font-size: 0.86rem;
}

.field input,
.field textarea,
.tree-search-row input {
  border: 1px solid #cfd8e3;
  border-radius: 6px;
  background: #ffffff;
  color: #101828;
}

.primary-action,
.secondary-action {
  min-width: 0;
  border-radius: 6px;
  font-weight: 700;
  letter-spacing: 0;
}

.primary-action {
  border: 1px solid #2563eb;
  background: #2563eb;
  color: #ffffff;
}

.secondary-action {
  padding: 10px 14px;
  border: 1px solid #cfd8e3;
  background: #ffffff;
  color: #344054;
}
```

- [ ] **Step 3: Add tree manager styles**

Append:

```css
.knowledge-console {
  grid-column: 1 / -1;
  overflow: hidden;
}

.console-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 18px;
  border-bottom: 1px solid #d9e2ef;
}

.tree-manager {
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  min-height: 560px;
}

.tree-panel {
  border-right: 1px solid #d9e2ef;
  background: #fbfcfe;
}

.tree-search-row {
  padding: 14px;
  border-bottom: 1px solid #e4eaf2;
}

.tree-search-row input {
  width: 100%;
  padding: 10px 12px;
}

.tree-list {
  max-height: 640px;
  overflow: auto;
  padding: 8px;
}

.tree-row {
  display: flex;
  align-items: center;
  min-height: 34px;
  border-radius: 6px;
}

.tree-row:hover,
.tree-row-active {
  background: #eef4ff;
}

.tree-expander,
.tree-label {
  border: 0;
  background: transparent;
  color: #344054;
}

.tree-expander {
  width: 24px;
  padding: 0;
}

.tree-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  min-width: 0;
  padding: 7px 8px 7px 0;
  text-align: left;
}

.tree-label span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tree-label small {
  color: #667085;
  font-size: 0.72rem;
}

.node-detail {
  display: grid;
  grid-template-rows: auto auto auto minmax(260px, 1fr) auto;
  gap: 14px;
  padding: 18px;
}

.node-detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.node-detail-header h3 {
  margin: 4px 0 0;
  color: #101828;
  font-size: 1.1rem;
}

.node-detail-header p {
  margin: 6px 0 0;
  color: #667085;
  font-size: 0.86rem;
}

.node-kind,
.node-state {
  font-size: 0.74rem;
  font-weight: 800;
  text-transform: uppercase;
}

.node-kind {
  color: #476582;
}

.node-state {
  padding: 6px 8px;
  border-radius: 999px;
}

.node-state-editable {
  background: #eaf3ff;
  color: #1d4ed8;
}

.node-state-readonly {
  background: #f2f4f7;
  color: #667085;
}

.node-meta-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.node-meta-grid span {
  overflow: hidden;
  padding: 8px 10px;
  border: 1px solid #e4eaf2;
  border-radius: 6px;
  color: #475467;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-editor,
.node-viewer {
  width: 100%;
  min-height: 300px;
  border: 1px solid #cfd8e3;
  border-radius: 6px;
  background: #ffffff;
  color: #101828;
  padding: 14px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.88rem;
  line-height: 1.6;
  resize: vertical;
}

.node-viewer {
  overflow: auto;
  background: #f8fafc;
  white-space: pre-wrap;
}

.node-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
```

- [ ] **Step 4: Remove old editor styles that conflict**

Delete or neutralize styles for:

```css
.knowledge-editor-layout
.document-list
.document-tab
.document-tab-active
.document-editor
.document-toolbar
.knowledge-document-textarea
.knowledge-document-viewer
.readonly-badge
```

Keep `.editor-status`, `.error-banner`, `.success-banner`, `.result-shell`, `.prompt-output`, and `.diff-section`, but adjust colors to the light theme if they still use white-on-dark.

- [ ] **Step 5: Run frontend build**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent/frontend
npm run build
```

Expected: build passes.

- [ ] **Step 6: Commit**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent
git add frontend/src/index.css
git commit -m "style: refresh knowledge agent console"
```

---

### Task 7: End-To-End Verification

**Files:**
- No source changes unless verification exposes a bug.

- [ ] **Step 1: Run backend unit tests**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent/backend
python3 -m unittest discover tests -v
```

Expected: all backend tests pass.

- [ ] **Step 2: Run frontend build**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent/frontend
npm run build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 3: Start backend**

Run:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent/backend
python3 run_api.py
```

Expected: API starts on `http://127.0.0.1:8010`. Keep this session running until frontend smoke test is complete.

- [ ] **Step 4: Start frontend**

Run in a second terminal:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent/frontend
npm run dev -- --host 127.0.0.1
```

Expected: Vite prints a local URL, usually `http://127.0.0.1:5173`.

- [ ] **Step 5: Manual smoke test**

Open the frontend and verify:

- Prompt package panel still returns a prompt.
- Repair panel still submits and shows diffs when backend/model settings allow it.
- Knowledge tree loads.
- `网元 NetworkElement` appears under `业务对象`.
- `Label / 属性` opens read-only.
- Editable markdown-backed nodes show textarea and save button.
- Editing an editable node saves and updates `_history`.
- Deleting an editable test node removes it from the tree.
- Layout remains compact on desktop width and usable at narrow width.

- [ ] **Step 6: Commit fixes if needed**

If verification required fixes, commit only those files:

```bash
cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent
git add <changed-files>
git commit -m "fix: stabilize knowledge tree manager"
```

If no fixes are needed, skip this step.

---

## Self-Review

Spec coverage:

- Simple tree structure: Task 1, Task 5, Task 6.
- Business-object organization: Task 1 and Task 5.
- Read-only schema nodes: Task 1, Task 2, Task 3, Task 5.
- Add/edit/delete editable markdown nodes: Task 2, Task 3, Task 5.
- Preserve existing file storage and history: Task 2.
- Keep prompt/repair flows: Task 5 retains the existing prompt and repair panels; Task 7 verifies them.
- Avoid knowledge graph and graph database: Tasks use plain tree, forms, and existing markdown files.
- Improve frontend style with intelligent-data-console cues: Task 6.

Placeholder scan:

- No `TBD`, `TODO`, or open-ended "add appropriate handling" steps remain.
- Each implementation task names files, code, commands, and expected results.

Type consistency:

- Backend node fields match frontend type names: `parent_id`, `source_file`, `section_id`, `content_preview`.
- API response shapes match frontend helper return types.
- Mutation methods return a node detail and refreshed tree where the frontend expects them.
