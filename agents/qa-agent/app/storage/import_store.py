from __future__ import annotations

from pathlib import Path
from typing import Iterable

from app.config import settings
from app.domain.models import ImportRecord


class ImportStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.artifacts_dir / "reports" / "imports"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, import_id: str) -> Path:
        return self.root / f"{import_id}.json"

    def save(self, record: ImportRecord) -> None:
        self.path_for(record.import_id).write_text(record.model_dump_json(indent=2), encoding="utf-8")

    def get(self, import_id: str) -> ImportRecord:
        return ImportRecord.model_validate_json(self.path_for(import_id).read_text(encoding="utf-8"))

    def list(self) -> Iterable[ImportRecord]:
        for path in sorted(self.root.glob("*.json")):
            yield ImportRecord.model_validate_json(path.read_text(encoding="utf-8"))
