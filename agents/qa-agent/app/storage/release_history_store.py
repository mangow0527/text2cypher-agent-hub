from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.domain.questioning.service import normalize_cypher, normalize_question


class ReleaseHistoryStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.artifacts_dir / "releases"
        self.root.mkdir(parents=True, exist_ok=True)

    def load_signatures(self, exclude_paths: set[Path] | None = None) -> dict[str, set[str]]:
        exclude = {path.resolve() for path in (exclude_paths or set())}
        questions: set[str] = set()
        cyphers: set[str] = set()
        for path in sorted(self.root.glob("*.jsonl")):
            resolved = path.resolve()
            if resolved in exclude:
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                question = str(payload.get("question", "")).strip()
                cypher = str(payload.get("cypher", "")).strip()
                if question:
                    questions.add(normalize_question(question))
                if cypher:
                    cyphers.add(normalize_cypher(cypher))
        return {
            "questions": questions,
            "cyphers": cyphers,
        }
