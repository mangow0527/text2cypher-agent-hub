from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from app.config import settings


class ArtifactStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.artifacts_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def ensure_job_dirs(self, job_id: str) -> dict[str, Path]:
        mapping = {}
        for name in [
            "schema",
            "taxonomy",
            "skeletons",
            "instantiated",
            "validated",
            "qa",
            "releases",
            "reports",
        ]:
            path = self.root / name
            path.mkdir(parents=True, exist_ok=True)
            mapping[name] = path / f"{job_id}.{ 'json' if name in {'schema','taxonomy','reports'} else 'jsonl'}"
        return mapping

    def ensure_import_dirs(self, import_id: str) -> dict[str, Path]:
        mapping = {}
        for name in ["qa", "reports"]:
            path = self.root / name
            path.mkdir(parents=True, exist_ok=True)
            mapping["qa" if name == "qa" else "report"] = path / f"{import_id}.{ 'jsonl' if name == 'qa' else 'json'}"
        return mapping

    def write_json(self, path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_jsonl(self, path: Path, rows: Iterable[dict]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def delete_paths(self, paths: Iterable[str]) -> None:
        for raw_path in paths:
            path = Path(raw_path)
            if path.exists():
                path.unlink()
