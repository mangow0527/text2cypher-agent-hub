from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from app.config import settings
from app.domain.models import JobRecord


class JobStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.artifacts_dir / "reports" / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def save(self, job: JobRecord) -> None:
        self.path_for(job.job_id).write_text(job.model_dump_json(indent=2), encoding="utf-8")

    def get(self, job_id: str) -> JobRecord:
        return JobRecord.model_validate_json(self.path_for(job_id).read_text(encoding="utf-8"))

    def list(self) -> Iterable[JobRecord]:
        for path in sorted(self.root.glob("*.json")):
            yield JobRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def delete(self, job_id: str) -> None:
        path = self.path_for(job_id)
        if path.exists():
            path.unlink()
