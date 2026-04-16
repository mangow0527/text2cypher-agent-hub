from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.domain.models import ImportRecord, ImportStatus, QASample
from app.domain.questioning.service import QUESTION_VARIANT_STYLES, normalize_cypher
from app.errors import AppError
from app.integrations.qa_dispatcher import QADispatcher
from app.reports.builder import ReportBuilder
from app.storage.artifact_store import ArtifactStore
from app.storage.import_store import ImportStore


class QAImportService:
    def __init__(
        self,
        artifact_store: ArtifactStore | None = None,
        import_store: ImportStore | None = None,
        report_builder: ReportBuilder | None = None,
        qa_dispatcher: QADispatcher | None = None,
    ) -> None:
        self.artifact_store = artifact_store or ArtifactStore()
        self.import_store = import_store or ImportStore()
        self.report_builder = report_builder or ReportBuilder()
        self.qa_dispatcher = qa_dispatcher or QADispatcher()

    def import_payload(self, payload_text: str, source_type: str = "inline") -> ImportRecord:
        record = ImportRecord(source_type=source_type)
        self.import_store.save(record)
        paths = self.artifact_store.ensure_import_dirs(record.import_id)

        try:
            samples = self._parse_payload(payload_text)
            report = self.report_builder.build(samples)
            report["dispatch"] = self.qa_dispatcher.dispatch_samples(samples)

            self.artifact_store.write_jsonl(paths["qa"], [sample.model_dump() for sample in samples])
            self.artifact_store.write_json(paths["report"], report)

            record.status = ImportStatus.COMPLETED
            record.updated_at = self._now()
            record.sample_count = len(samples)
            record.artifacts = {
                "qa": str(paths["qa"]),
                "report": str(paths["report"]),
            }
            record.report = report
            self.import_store.save(record)
            return record
        except Exception as exc:  # noqa: BLE001
            record.status = ImportStatus.FAILED
            record.updated_at = self._now()
            record.errors.append({"code": getattr(exc, "code", "QA_IMPORT_ERROR"), "message": str(exc)})
            self.import_store.save(record)
            return record

    def import_file(self, file_path: str) -> ImportRecord:
        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            raise AppError("QA_IMPORT_ERROR", f"QA import file does not exist: {path}")
        return self.import_payload(path.read_text(encoding="utf-8"), source_type="file")

    def list_imports(self) -> list[ImportRecord]:
        return list(self.import_store.list())

    def get_import(self, import_id: str) -> ImportRecord:
        return self.import_store.get(import_id)

    def _parse_payload(self, payload_text: str) -> list[QASample]:
        raw = payload_text.strip()
        if not raw:
            raise AppError("QA_IMPORT_ERROR", "QA import content is empty.")

        rows = []
        if raw.startswith("["):
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise AppError("QA_IMPORT_ERROR", "QA import JSON must be an array.")
            rows = parsed
        else:
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]

        samples = [self._normalize_sample(row) for row in rows]
        if not samples:
            raise AppError("QA_IMPORT_ERROR", "No QA samples found in import payload.")
        return samples

    def _normalize_sample(self, row: dict) -> QASample:
        payload = dict(row)
        if "cypher_normalized" not in payload and payload.get("cypher"):
            payload["cypher_normalized"] = normalize_cypher(payload["cypher"])
        if "answer" not in payload:
            payload["answer"] = payload.get("result_signature", {}).get("result_preview", [])
        if "split" not in payload:
            payload["split"] = "silver"
        if "provenance" not in payload:
            payload["provenance"] = {"generation_mode": "manual_import", "structure_family": "manual_import"}
        elif "structure_family" not in payload["provenance"]:
            payload["provenance"]["structure_family"] = "manual_import"
        if "question_variant_styles" not in payload:
            payload["question_variant_styles"] = QUESTION_VARIANT_STYLES[: len(payload.get("question_variants_zh", []))]
        if payload.get("difficulty") in {"easy", "medium", "hard"}:
            payload["difficulty"] = {"easy": "L2", "medium": "L4", "hard": "L6"}[payload["difficulty"]]
        if payload.get("difficulty") not in {f"L{level}" for level in range(1, 9)}:
            payload["difficulty"] = "L1"
        return QASample.model_validate(payload)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
