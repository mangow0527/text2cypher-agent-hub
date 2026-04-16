from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config import settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.knowledge_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def bootstrap_defaults(self) -> None:
        defaults = {
            "schema.json": json.dumps(
                {
                    "vertex_labels": [
                        {"name": "NetworkElement", "properties": ["id", "name"]},
                        {"name": "Port", "properties": ["id", "name", "speed", "status"]},
                        {"name": "Tunnel", "properties": ["id", "name", "latency", "bandwidth"]},
                        {"name": "Protocol", "properties": ["version", "name"]},
                    ],
                    "edge_labels": [
                        {"name": "HAS_PORT", "from": "NetworkElement", "to": "Port"},
                        {"name": "FIBER_SRC", "from": "NetworkElement", "to": "Tunnel"},
                        {"name": "TUNNEL_PROTO", "from": "Tunnel", "to": "Protocol"},
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            "cypher_syntax.md": (
                "## Core Rules\n\n"
                "[id: syntax_direction_rule]\n"
                "- 优先使用 schema 中定义的显式方向，不要依赖双向匹配。\n\n"
                "[id: syntax_with_rule]\n"
                "- 聚合或多阶段过滤时，优先使用显式 `WITH` 分段，确保 TuGraph 可执行性。\n"
            ),
            "few_shot.md": (
                "## Reference Examples\n\n"
                "[id: tunnel_protocol_version]\n"
                "Question: 查询协议版本为v2.0的隧道\n"
                "Cypher: MATCH (t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol) WHERE p.version = 'v2.0' RETURN t.id, t.name\n"
                "Why: 展示协议版本过滤路径。\n\n"
                "[id: networkelement_tunnel_protocol_path]\n"
                "Question: 查询协议版本为v2.0的隧道所属网元\n"
                "Cypher: MATCH (n:NetworkElement)-[:FIBER_SRC]->(t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol) WHERE p.version = 'v2.0' RETURN n.id\n"
                "Why: 展示网元到隧道再到协议的标准路径。\n"
            ),
            "system_prompt.md": (
                "## Core Rules\n\n"
                "[id: role_definition]\n"
                "- 你是一个严格的 TuGraph Text2Cypher 生成器。\n\n"
                "[id: generation_rule_1]\n"
                "- 只能使用给定 Schema 中存在的节点、关系、属性。\n\n"
                "[id: generation_rule_2]\n"
                "- 不得虚构不存在的 schema 元素或业务含义。\n"
            ),
            "business_knowledge.md": (
                "## Terminology Mapping\n\n"
                "[id: protocol_version_mapping]\n"
                "- “协议版本”优先映射到 `Protocol.version`。\n\n"
                "[id: network_element_alias]\n"
                "- “网元”对应 `NetworkElement`。\n\n"
                "[id: tunnel_owner_path]\n"
                "- “隧道所属网元”优先理解为 `(:NetworkElement)-[:FIBER_SRC]->(:Tunnel)` 路径上的上游设备。\n"
            ),
        }
        for name, content in defaults.items():
            path = self.root / name
            if not path.exists():
                path.write_text(content, encoding="utf-8")

    def read_text(self, name: str) -> str:
        return (self.root / name).read_text(encoding="utf-8")

    def read_schema(self) -> dict:
        return json.loads((self.root / "schema.json").read_text(encoding="utf-8"))

    def write_versioned(self, filename: str, before: str, after: str, suggestion: str, target_type: str) -> None:
        history_dir = self.root / "_history"
        history_dir.mkdir(parents=True, exist_ok=True)
        (self.root / filename).write_text(after, encoding="utf-8")
        snapshot = {
            "id": f"chg_{uuid4().hex[:12]}",
            "filename": filename,
            "target_type": target_type,
            "suggestion": suggestion,
            "created_at": utc_now(),
            "before": before,
            "after": after,
        }
        (history_dir / f"{snapshot['id']}.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
