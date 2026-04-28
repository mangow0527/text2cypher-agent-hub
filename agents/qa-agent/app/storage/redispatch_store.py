from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


class RedispatchAttemptStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.artifacts_dir / "redispatch"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, qa_id: str) -> Path:
        return self.root / f"{qa_id}.jsonl"

    def count(self, qa_id: str) -> int:
        path = self.path_for(qa_id)
        if not path.exists():
            return 0
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())

    def append(self, qa_id: str, payload: dict[str, Any]) -> None:
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "qa_id": qa_id, **payload}
        with self.path_for(qa_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
