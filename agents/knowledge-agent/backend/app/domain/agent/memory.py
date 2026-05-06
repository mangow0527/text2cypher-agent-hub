from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings


class MemoryManager:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.artifacts_dir / "agent_memory"
        self.root.mkdir(parents=True, exist_ok=True)
        self.repair_memory_path = self.root / "repair_memory.jsonl"

    def search_repair_memory(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not self.repair_memory_path.exists():
            return []
        terms = {term.lower() for term in query.split() if term.strip()}
        scored: list[tuple[int, dict[str, Any]]] = []
        for line in self.repair_memory_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            haystack = json.dumps(item, ensure_ascii=False).lower()
            score = sum(1 for term in terms if term in haystack)
            if score > 0:
                scored.append((score, item))
        return [item for score, item in sorted(scored, key=lambda row: row[0], reverse=True)[:limit]]

    def write_repair_memory(self, entry: dict[str, Any]) -> dict[str, Any]:
        payload = {"memory_id": f"mem_{uuid4().hex[:12]}", "created_at": datetime.now(timezone.utc).isoformat(), **entry}
        with self.repair_memory_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return payload
