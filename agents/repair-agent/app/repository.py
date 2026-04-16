from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .schemas import KRSSAnalysisRecord


class RepairRepository:
    def __init__(self, data_dir: str) -> None:
        self._analyses_dir = Path(data_dir) / "analyses"
        self._analyses_dir.mkdir(parents=True, exist_ok=True)

    def save_analysis(self, record: KRSSAnalysisRecord) -> None:
        path = self._analyses_dir / f"{record.analysis_id}.json"
        path.write_text(json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")

    def get_analysis(self, analysis_id: str) -> Optional[KRSSAnalysisRecord]:
        path = self._analyses_dir / f"{analysis_id}.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return KRSSAnalysisRecord.model_validate(payload)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
