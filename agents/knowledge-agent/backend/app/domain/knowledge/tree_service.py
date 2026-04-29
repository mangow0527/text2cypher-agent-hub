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
    occurrence: int
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
    block_index: int | None = None,
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
        "block_index": block_index,
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
        concept_aliases = self._concept_aliases(concepts)

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
                editable=False,
            )
            schema_node = self._schema_node(schema, concept, concept_node["id"])
            if schema_node:
                concept_node["children"].append(schema_node)
            for block in business_blocks + few_shot_blocks:
                if concept in self._block_concepts(block, concept_aliases):
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
        self._assert_unique_block(
            source_file=source_file,
            candidate=self._block_from_content(source_file, content, node.get("block_index", 1)),
            ignore_section_id=section_id,
            ignore_occurrence=node.get("block_index", 1),
        )
        before = self.store.read_text(source_file)
        after = self._replace_block(before, section_id, node.get("block_index", 1), content)
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
        self._assert_unique_block(
            source_file=source_file,
            candidate=self._block_from_content(source_file, block, 1),
        )
        before = self.store.read_text(source_file)
        separator = "" if before.endswith("\n\n") else "\n"
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
        after = self._remove_block(before, section_id, node.get("block_index", 1))
        self.store.write_versioned(source_file, before, after, "manual knowledge tree node delete", node["kind"])

    def _schema_concepts(self, schema: dict) -> set[str]:
        return {item["name"] for item in self._vertex_labels(schema) if item.get("name")}

    def _schema_node(self, schema: dict, concept: str, parent_id: str) -> dict | None:
        label = next((item for item in self._vertex_labels(schema) if item.get("name") == concept), None)
        if not label:
            return None
        properties = label.get("properties", [])
        property_names = [prop.get("name", "") if isinstance(prop, dict) else str(prop) for prop in properties]
        content = f"Label: {concept}\nProperties: {', '.join(name for name in property_names if name)}"
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

    def _vertex_labels(self, schema: dict | list) -> list[dict]:
        if isinstance(schema, dict):
            return [
                {"name": item.get("name"), "properties": item.get("properties", [])}
                for item in schema.get("vertex_labels", [])
                if isinstance(item, dict)
            ]
        if isinstance(schema, list):
            return [
                {"name": item.get("label"), "properties": item.get("properties", [])}
                for item in schema
                if isinstance(item, dict) and item.get("type") == "VERTEX"
            ]
        return []

    def _block_node(self, block: MarkdownBlock, parent_id: str, fallback_kind: str | None = None) -> dict:
        kind = block.metadata.get("kind") or fallback_kind or self._kind_from_source(block.source_file)
        suffix = "" if block.occurrence == 1 else f":{block.occurrence}"
        return _node(
            node_id=f"{Path(block.source_file).stem}:{block.section_id}{suffix}",
            parent_id=parent_id,
            title=_title_from_block(block),
            kind=kind,
            concept=block.metadata.get("concept"),
            source_file=block.source_file,
            section_id=block.section_id,
            block_index=block.occurrence,
            editable=True,
            content_preview=_preview(block.content),
        )

    def _kind_from_source(self, source_file: str) -> str:
        if source_file == "few_shot.md":
            return "few_shot"
        return "business_semantic"

    def _concept_aliases(self, concepts: set[str]) -> dict[str, list[str]]:
        aliases = {
            "NetworkElement": ["networkelement", "network element", "网元", "设备"],
            "Tunnel": ["tunnel", "隧道"],
            "Port": ["port", "端口"],
            "Protocol": ["protocol", "协议"],
            "Fiber": ["fiber", "光纤"],
            "Link": ["link", "链路"],
            "Service": ["service", "业务", "服务"],
        }
        return {
            concept: [concept.lower(), *aliases.get(concept, [])]
            for concept in concepts
        }

    def _block_concepts(self, block: MarkdownBlock, concept_aliases: dict[str, list[str]]) -> set[str]:
        if "concept" in block.metadata:
            return {block.metadata["concept"]}
        content = block.content.lower()
        return {
            concept
            for concept, aliases in concept_aliases.items()
            if any(alias.lower() in content for alias in aliases)
        }

    def _parse_markdown_blocks(self, filename: str) -> list[MarkdownBlock]:
        text = self.store.read_text(filename)
        blocks: list[MarkdownBlock] = []
        current: list[str] = []
        current_id: str | None = None
        occurrence_by_id: dict[str, int] = {}

        def flush() -> None:
            nonlocal current, current_id
            if current_id and current:
                occurrence_by_id[current_id] = occurrence_by_id.get(current_id, 0) + 1
                occurrence = occurrence_by_id[current_id]
                content = "\n".join(current).strip() + "\n"
                metadata: dict[str, str] = {}
                for line in current:
                    match = META_RE.match(line.strip())
                    if match and match.group(1) != "id":
                        metadata[match.group(1)] = match.group(2)
                blocks.append(MarkdownBlock(filename, current_id, occurrence, content, metadata))
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
            block = self._find_block(source_file, section_id, node.get("block_index", 1))
            return block.content
        return node.get("content_preview", "")

    def _find_block(self, source_file: str, section_id: str, occurrence: int = 1) -> MarkdownBlock:
        for block in self._parse_markdown_blocks(source_file):
            if block.section_id == section_id and block.occurrence == occurrence:
                return block
        raise AppError("KNOWLEDGE_TREE_NODE_NOT_FOUND", f"Knowledge block not found: {source_file}#{section_id}")

    def _block_from_content(self, source_file: str, content: str, occurrence: int) -> MarkdownBlock:
        current_id = ""
        metadata: dict[str, str] = {}
        for line in content.splitlines():
            id_match = ID_RE.match(line.strip())
            if id_match:
                current_id = id_match.group(1)
            meta_match = META_RE.match(line.strip())
            if meta_match and meta_match.group(1) != "id":
                metadata[meta_match.group(1)] = meta_match.group(2)
        return MarkdownBlock(source_file, current_id, occurrence, content.strip() + "\n", metadata)

    def _assert_unique_block(
        self,
        *,
        source_file: str,
        candidate: MarkdownBlock,
        ignore_section_id: str | None = None,
        ignore_occurrence: int | None = None,
    ) -> None:
        candidate_key = self._dedupe_key(candidate)
        for block in self._parse_markdown_blocks(source_file):
            if block.section_id == ignore_section_id and block.occurrence == ignore_occurrence:
                continue
            if self._dedupe_key(block) == candidate_key:
                raise AppError(
                    "KNOWLEDGE_TREE_DUPLICATE_NODE",
                    f"duplicate knowledge node: {source_file}#{block.section_id}",
                )

    def _dedupe_key(self, block: MarkdownBlock) -> tuple[str, str, str]:
        kind = block.metadata.get("kind") or self._kind_from_source(block.source_file)
        concept = block.metadata.get("concept", "")
        return (kind, concept, self._normalized_content(block.content))

    def _normalized_content(self, content: str) -> str:
        semantic_lines = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or ID_RE.match(stripped) or META_RE.match(stripped):
                continue
            semantic_lines.append(stripped)
        normalized = " ".join(semantic_lines).lower()
        normalized = normalized.replace("`", "")
        normalized = re.sub(r"\s+", "", normalized)
        return normalized

    def _replace_block(self, text: str, section_id: str, occurrence: int, replacement: str) -> str:
        return self._rewrite_block(text, section_id, occurrence, replacement.strip() + "\n", remove=False)

    def _remove_block(self, text: str, section_id: str, occurrence: int) -> str:
        return self._rewrite_block(text, section_id, occurrence, "", remove=True)

    def _rewrite_block(self, text: str, section_id: str, occurrence: int, replacement: str, remove: bool) -> str:
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
        seen = 0
        for start_index, end_index in ranges:
            id_match = ID_RE.match(lines[start_index].strip())
            if id_match and id_match.group(1) == section_id:
                seen += 1
            if id_match and id_match.group(1) == section_id and seen == occurrence:
                replacement_lines = [] if remove else replacement.rstrip("\n").splitlines()
                new_lines = lines[:start_index] + replacement_lines + lines[end_index:]
                return "\n".join(new_lines).strip() + "\n"
        raise AppError("KNOWLEDGE_TREE_NODE_NOT_FOUND", f"Knowledge block not found: {section_id}")

    def _new_section_id(self, title: str) -> str:
        base = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_").lower() or "knowledge_node"
        existing = {
            block.section_id
            for filename in ("business_knowledge.md", "few_shot.md", "cypher_syntax.md")
            for block in self._parse_markdown_blocks(filename)
        }
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
        return "\n".join(lines).strip() + "\n"
