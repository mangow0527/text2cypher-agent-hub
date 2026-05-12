from __future__ import annotations

from pathlib import Path

import yaml

from services.cypher_generator_agent.app import resource_paths
from services.cypher_generator_agent.app.semantic_alignment import (
    validate_default_semantic_alignment,
    validate_semantic_alignment,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "services/testing_agent/docs/reference/schema.json"


def test_default_semantic_alignment_accepts_tugraph_schema_and_knowledge_context(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "knowledge"
    _write_knowledge_docs(
        knowledge_dir,
        schema_json=SCHEMA_PATH.read_text(encoding="utf-8"),
        business_knowledge=(
            "- “链路类型”映射为 `Link.elem_type`。\n"
            "- “端口类型”映射为 `Port.elem_type`。\n"
            "- “链路目的端口”表示 `(l:Link)-[:LINK_DST]->(p:Port)`。"
        ),
        few_shot=(
            "Question: 按类型统计隧道数量\n"
            "Cypher: MATCH (t:Tunnel) RETURN t.elem_type AS group_key, count(t) AS total"
        ),
    )

    report = validate_default_semantic_alignment(knowledge_dir=knowledge_dir)

    assert report.accepted is True
    assert report.diagnostics == []
    assert "network_graph_semantic_view.yaml" in report.checked_sources
    assert "schema.json" in report.checked_sources
    assert "knowledge/schema.json" in report.checked_sources


def test_semantic_alignment_rejects_knowledge_references_outside_semantic_view(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "knowledge"
    _write_knowledge_docs(
        knowledge_dir,
        schema_json=SCHEMA_PATH.read_text(encoding="utf-8"),
        business_knowledge="- “链路成本”映射为 `Link.cost`。",
        few_shot="Question: 查询链路成本\nCypher: MATCH (l:Link) RETURN l.cost AS cost",
    )

    report = validate_default_semantic_alignment(knowledge_dir=knowledge_dir)

    assert report.accepted is False
    assert any(diagnostic.code == "knowledge_reference_not_in_tugraph_schema" for diagnostic in report.diagnostics)


def test_semantic_alignment_rejects_knowledge_property_missing_only_from_semantic_view(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "knowledge"
    _write_knowledge_docs(
        knowledge_dir,
        schema_json=SCHEMA_PATH.read_text(encoding="utf-8"),
        business_knowledge="- “链路协议”映射为 `Link.protocol`。",
        few_shot="Question: 查询链路协议\nCypher: MATCH (l:Link) RETURN l.protocol AS link_protocol",
    )
    semantic_view_path = tmp_path / "network_graph_semantic_view.yaml"
    semantic_view_doc = yaml.safe_load(resource_paths.graph_semantic_view_path().read_text(encoding="utf-8"))
    semantic_view_doc["dimensions"].pop("link.protocol")
    semantic_view_path.write_text(yaml.safe_dump(semantic_view_doc, allow_unicode=True), encoding="utf-8")

    report = validate_semantic_alignment(
        semantic_view_path=semantic_view_path,
        tugraph_schema_path=SCHEMA_PATH,
        knowledge_dir=knowledge_dir,
    )

    assert report.accepted is False
    assert any(diagnostic.code == "knowledge_reference_not_in_semantic_view" for diagnostic in report.diagnostics)


def test_semantic_alignment_rejects_few_shot_alias_property_outside_tugraph_schema(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "knowledge"
    _write_knowledge_docs(
        knowledge_dir,
        schema_json=SCHEMA_PATH.read_text(encoding="utf-8"),
        business_knowledge="- “链路”映射为 `Link`。",
        few_shot="Question: 查询链路成本\nCypher: MATCH (l:Link) RETURN l.cost AS cost",
    )

    report = validate_default_semantic_alignment(knowledge_dir=knowledge_dir)

    assert report.accepted is False
    assert any(diagnostic.code == "knowledge_reference_not_in_tugraph_schema" for diagnostic in report.diagnostics)


def test_semantic_alignment_rejects_multiline_few_shot_alias_property_outside_tugraph_schema(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "knowledge"
    _write_knowledge_docs(
        knowledge_dir,
        schema_json=SCHEMA_PATH.read_text(encoding="utf-8"),
        business_knowledge="- “链路”映射为 `Link`。",
        few_shot=(
            "Question: 查询链路成本\n"
            "Cypher:\n"
            "MATCH (l:Link)\n"
            "RETURN l.cost AS cost"
        ),
    )

    report = validate_default_semantic_alignment(knowledge_dir=knowledge_dir)

    assert report.accepted is False
    assert any(diagnostic.code == "knowledge_reference_not_in_tugraph_schema" for diagnostic in report.diagnostics)


def test_semantic_alignment_ignores_negative_examples_when_checking_knowledge_refs(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "knowledge"
    _write_knowledge_docs(
        knowledge_dir,
        schema_json=SCHEMA_PATH.read_text(encoding="utf-8"),
        business_knowledge=(
            "- “链路类型”映射为 `Link.elem_type`。\n"
            "- Anti-Pattern: 不要生成 `MATCH (l:Link) RETURN l.type AS type`。"
        ),
        few_shot=(
            "Question: 按链路类型统计链路数量\n"
            "Cypher: MATCH (l:Link) RETURN l.elem_type AS link_type, count(l) AS link_count"
        ),
    )

    report = validate_default_semantic_alignment(knowledge_dir=knowledge_dir)

    assert report.accepted is True
    assert report.diagnostics == []


def _write_knowledge_docs(
    knowledge_dir: Path,
    *,
    schema_json: str,
    business_knowledge: str,
    few_shot: str,
) -> None:
    knowledge_dir.mkdir()
    (knowledge_dir / "system_prompt.md").write_text("只能使用 Schema 中存在的节点、关系、属性。", encoding="utf-8")
    (knowledge_dir / "schema.json").write_text(schema_json, encoding="utf-8")
    (knowledge_dir / "cypher_syntax.md").write_text("聚合使用 RETURN/WITH 隐式分组。", encoding="utf-8")
    (knowledge_dir / "business_knowledge.md").write_text(business_knowledge, encoding="utf-8")
    (knowledge_dir / "few_shot.md").write_text(few_shot, encoding="utf-8")
