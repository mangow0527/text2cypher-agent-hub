from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


DIFFICULTY_LEVELS = [f"L{level}" for level in range(1, 9)]

DIFFICULTY_DEFINITIONS = [
    {
        "level": "L1",
        "paper_band": "Easy",
        "title": "单实体直接检索",
        "definition": "只涉及一个节点类型，无边遍历、聚合或复杂过滤，直接返回实体或属性。",
    },
    {
        "level": "L2",
        "paper_band": "Easy",
        "title": "单实体过滤或单边简单返回",
        "definition": "单节点简单字段过滤，或单边关系模式返回，不包含路径推理、聚合或嵌套。",
    },
    {
        "level": "L3",
        "paper_band": "Medium",
        "title": "一跳关系查询",
        "definition": "存在明确的一跳遍历，用关系找到目标实体或属性，不包含复杂聚合或多阶段结构。",
    },
    {
        "level": "L4",
        "paper_band": "Medium",
        "title": "一跳 + 基础分析",
        "definition": "在一跳或单实体基础上加入简单聚合、排序、Top-K 或基础过滤，但仍无嵌套。",
    },
    {
        "level": "L5",
        "paper_band": "Hard",
        "title": "两跳或变长路径的基础推理",
        "definition": "涉及两跳路径或变长路径，逻辑条件有限，主要考察基础图拓扑推理。",
    },
    {
        "level": "L6",
        "paper_band": "Hard",
        "title": "两跳/变长 + 多条件组合",
        "definition": "在两跳或变长路径基础上加入多个过滤条件、多个返回目标、非嵌套聚合或排序限制。",
    },
    {
        "level": "L7",
        "paper_band": "Extra Hard",
        "title": "三跳以上或多阶段 MATCH",
        "definition": "出现三跳及以上路径、多阶段 MATCH，或需要多段图结构推理。",
    },
    {
        "level": "L8",
        "paper_band": "Extra Hard",
        "title": "高结构复杂度 + 深推理",
        "definition": "同时具备复杂路径、多阶段 MATCH、嵌套聚合、多子目标联合约束或中间结果全局推理。",
    },
]


class QAStatsService:
    def __init__(self, qa_root: Path | None = None) -> None:
        self.qa_root = qa_root or settings.artifacts_dir / "qa"

    def build(self) -> dict[str, Any]:
        distribution: Counter[str] = Counter({level: 0 for level in DIFFICULTY_LEVELS})
        source_counts: Counter[str] = Counter({"generated": 0, "imported": 0, "unknown": 0})
        files_processed = 0
        invalid_rows = 0
        latest_updated_at: str | None = None

        paths = []
        if self.qa_root.exists():
            paths = [path for path in sorted(self.qa_root.glob("*.jsonl")) if not path.name.startswith(".")]
        for path in paths:
            files_processed += 1
            latest_updated_at = self._latest_timestamp(latest_updated_at, path)
            source_type = self._source_type(path)

            try:
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            invalid_rows += 1
                            continue

                        difficulty = str(row.get("difficulty", "")).upper()
                        if difficulty not in distribution:
                            invalid_rows += 1
                            continue
                        distribution[difficulty] += 1
                        source_counts[source_type] += 1
            except (OSError, UnicodeDecodeError):
                invalid_rows += 1
                continue

        total = sum(distribution.values())
        return {
            "total_qa_pairs": total,
            "generated_qa_pairs": source_counts["generated"],
            "imported_qa_pairs": source_counts["imported"],
            "unknown_source_qa_pairs": source_counts["unknown"],
            "difficulty_distribution": dict(distribution),
            "difficulty_percentages": {
                level: round((count / total) * 100, 1) if total else 0
                for level, count in distribution.items()
            },
            "difficulty_definitions": DIFFICULTY_DEFINITIONS,
            "files_processed": files_processed,
            "invalid_rows": invalid_rows,
            "latest_updated_at": latest_updated_at,
        }

    def _source_type(self, path: Path) -> str:
        if path.name.startswith("job_"):
            return "generated"
        if path.name.startswith("imp_"):
            return "imported"
        return "unknown"

    def _latest_timestamp(self, current: str | None, path: Path) -> str | None:
        try:
            updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            return current
        if current is None or updated_at > current:
            return updated_at
        return current
