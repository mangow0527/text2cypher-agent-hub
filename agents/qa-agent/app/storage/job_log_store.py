from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


class JobLogStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.artifacts_dir / "logs"
        self.jobs_root = self.root / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)

    def path_for(self, job_id: str) -> Path:
        return self.jobs_root / f"{job_id}.log"

    def append(self, job_id: str, stage: str, level: str, message: str, extra: dict[str, Any] | None = None) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "stage": stage,
            "level": level,
            "message": message,
        }
        if extra is not None:
            entry["extra"] = extra
        with self.path_for(job_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def delete(self, job_id: str) -> None:
        path = self.path_for(job_id)
        if path.exists():
            path.unlink()
