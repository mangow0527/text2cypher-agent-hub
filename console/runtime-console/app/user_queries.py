from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from .diagnostics import (
    CgaDiagnosticClient,
    build_cga_diagnostic_facts,
    make_failed_diagnostic,
    make_generated_diagnostic,
    make_not_required_diagnostic,
    make_pending_diagnostic,
)


USER_QUERY_SCHEMA_VERSION = "runtime_user_query_v1"


class UserQueryCreateRequest(BaseModel):
    question: str = Field(..., min_length=1)


class CgaGenerationClient(Protocol):
    async def generate(self, *, user_query_id: str, question: str, generation_run_id: str) -> dict[str, Any]:
        ...


class TuGraphQueryClient(Protocol):
    async def execute(self, *, user_query_id: str, cypher: str) -> dict[str, Any]:
        ...


class RuntimeCgaClient:
    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def generate(self, *, user_query_id: str, question: str, generation_run_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/semantic/parse",
                json={
                    "id": user_query_id,
                    "question": question,
                    "generation_run_id": generation_run_id,
                },
            )
            response.raise_for_status()
            return response.json()


class RuntimeTuGraphQueryClient:
    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def execute(self, *, user_query_id: str, cypher: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/tugraph/query",
                json={"user_query_id": user_query_id, "cypher": cypher},
            )
            response.raise_for_status()
            return response.json()


class RuntimeUserQueryService:
    def __init__(
        self,
        *,
        data_dir: str | Path,
        cga_client: CgaGenerationClient,
        tugraph_client: TuGraphQueryClient,
        diagnostic_client: CgaDiagnosticClient | None = None,
        history_limit: int = 20,
        preview_row_limit: int = 100,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.index_path = self.data_dir / "index.json"
        self.cga_client = cga_client
        self.tugraph_client = tugraph_client
        self.diagnostic_client = diagnostic_client
        self.history_limit = history_limit
        self.preview_row_limit = preview_row_limit

    async def create_user_query(self, *, question: str, defer_diagnostic: bool = False) -> dict[str, Any]:
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("question must not be empty")

        user_query_id = _new_user_query_id()
        generation_run_id = f"{user_query_id}-generation"
        created_at = _utc_now()
        cga_generation: dict[str, Any] | None = None
        cga_error: str | None = None
        tugraph_response: dict[str, Any] | None = None
        generated_cypher: str | None = None
        status = "service_failed"

        try:
            cga_generation = await self.cga_client.generate(
                user_query_id=user_query_id,
                question=normalized_question,
                generation_run_id=generation_run_id,
            )
            generation_status = str(cga_generation.get("status") or "service_failed")
            generated_cypher = _clean_string(cga_generation.get("cypher"))
            status = generation_status
            if generation_status == "generated" and generated_cypher:
                tugraph_response = await self._execute_tugraph(
                    user_query_id=user_query_id,
                    cypher=generated_cypher,
                )
                status = "query_failed" if _raw_tugraph_failed(tugraph_response) else "completed"
        except Exception as exc:
            cga_error = str(exc)
            status = "service_failed"

        if defer_diagnostic and status != "completed":
            cga_diagnostic = make_pending_diagnostic()
        else:
            cga_diagnostic = await self._build_cga_diagnostic(
                user_query_id=user_query_id,
                question=normalized_question,
                status=status,
                generation_status=None if cga_generation is None else cga_generation.get("status"),
                cga_generation=cga_generation,
                cga_error=cga_error,
                tugraph_response=tugraph_response,
                generated_cypher=generated_cypher,
            )

        record = {
            "schema_version": USER_QUERY_SCHEMA_VERSION,
            "user_query_id": user_query_id,
            "question": normalized_question,
            "generation_run_id": generation_run_id,
            "status": status,
            "generation_status": None if cga_generation is None else cga_generation.get("status"),
            "generated_cypher": generated_cypher,
            "cga_elapsed_ms": _cga_elapsed_ms(cga_generation),
            "cga_generation": cga_generation,
            "cga_error": cga_error,
            "cga_diagnostic": cga_diagnostic,
            "tugraph_response": tugraph_response,
            "result_preview": self._build_result_preview(tugraph_response),
            "created_at": created_at,
            "updated_at": _utc_now(),
        }
        self._save_record(record)
        return record

    def list_user_queries(self) -> dict[str, Any]:
        return {
            "schema_version": USER_QUERY_SCHEMA_VERSION,
            "history_limit": self.history_limit,
            "items": [
                self._summary_from_record(record)
                for record in self._records_from_index()
                if record is not None
            ],
        }

    def get_user_query(self, user_query_id: str) -> dict[str, Any] | None:
        return self._read_record(user_query_id)

    def get_tugraph_download_payload(self, user_query_id: str) -> tuple[dict[str, Any] | None, str | None]:
        record = self._read_record(user_query_id)
        if record is None:
            return None, "not_found"
        payload = _downloadable_tugraph_response(record)
        if payload is None:
            return None, "no_tugraph_response"
        return payload, None

    async def complete_cga_diagnostic(self, user_query_id: str) -> dict[str, Any] | None:
        record = self._read_record(user_query_id)
        if record is None:
            return None
        cga_diagnostic = await self._build_cga_diagnostic(
            user_query_id=str(record.get("user_query_id") or user_query_id),
            question=str(record.get("question") or ""),
            status=str(record.get("status") or ""),
            generation_status=record.get("generation_status"),
            cga_generation=record.get("cga_generation") if isinstance(record.get("cga_generation"), dict) else None,
            cga_error=record.get("cga_error") if isinstance(record.get("cga_error"), str) else None,
            tugraph_response=record.get("tugraph_response") if isinstance(record.get("tugraph_response"), dict) else None,
            generated_cypher=record.get("generated_cypher") if isinstance(record.get("generated_cypher"), str) else None,
        )
        record["cga_diagnostic"] = cga_diagnostic
        record["updated_at"] = _utc_now()
        self._write_json(self.data_dir / f"{user_query_id}.json", record)
        return record

    async def _execute_tugraph(self, *, user_query_id: str, cypher: str) -> dict[str, Any]:
        try:
            return await self.tugraph_client.execute(user_query_id=user_query_id, cypher=cypher)
        except Exception as exc:
            return {"error_message": f"TuGraph query service failed: {exc}"}

    async def _build_cga_diagnostic(
        self,
        *,
        user_query_id: str,
        question: str,
        status: str,
        generation_status: Any,
        cga_generation: dict[str, Any] | None,
        cga_error: str | None,
        tugraph_response: dict[str, Any] | None,
        generated_cypher: str | None,
    ) -> dict[str, Any]:
        if status == "completed":
            return make_not_required_diagnostic()
        if self.diagnostic_client is None:
            return make_failed_diagnostic("诊断 LLM 未配置")
        facts = build_cga_diagnostic_facts(
            user_query_id=user_query_id,
            question=question,
            status=status,
            generation_status=None if generation_status is None else str(generation_status),
            cga_generation=cga_generation,
            cga_error=cga_error,
            tugraph_response=tugraph_response,
            generated_cypher=generated_cypher,
        )
        try:
            payload = await self.diagnostic_client.generate(facts=facts)
        except Exception as exc:
            return make_failed_diagnostic(exc)
        return make_generated_diagnostic(payload)

    def _build_result_preview(self, tugraph_response: dict[str, Any] | None) -> dict[str, Any]:
        rows = _normalize_tugraph_rows(tugraph_response)
        preview_rows = rows[: self.preview_row_limit]
        return {
            "preview_row_limit": self.preview_row_limit,
            "rows": preview_rows,
            "displayed_row_count": len(preview_rows),
            "row_count": len(rows),
            "truncated": len(rows) > self.preview_row_limit,
        }

    def _save_record(self, record: dict[str, Any]) -> None:
        user_query_id = str(record["user_query_id"])
        self._write_json(self.data_dir / f"{user_query_id}.json", record)
        ordered_ids = [user_query_id]
        for existing_id in self._read_index_ids():
            if existing_id != user_query_id:
                ordered_ids.append(existing_id)
        kept_ids = ordered_ids[: self.history_limit]
        expired_ids = ordered_ids[self.history_limit :]
        self._write_json(self.index_path, {"schema_version": USER_QUERY_SCHEMA_VERSION, "items": kept_ids})
        for expired_id in expired_ids:
            try:
                (self.data_dir / f"{expired_id}.json").unlink()
            except FileNotFoundError:
                pass

    def _records_from_index(self) -> list[dict[str, Any] | None]:
        return [self._read_record(user_query_id) for user_query_id in self._read_index_ids()]

    def _read_record(self, user_query_id: str) -> dict[str, Any] | None:
        path = self.data_dir / f"{user_query_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_index_ids(self) -> list[str]:
        if not self.index_path.exists():
            return []
        payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        items = payload.get("items") or []
        return [str(item) for item in items]

    def _summary_from_record(self, record: dict[str, Any]) -> dict[str, Any]:
        tugraph_response = _downloadable_tugraph_response(record)
        preview = record.get("result_preview") or {}
        query_failed = _raw_tugraph_failed(tugraph_response or {})
        return {
            "user_query_id": record.get("user_query_id"),
            "question": record.get("question"),
            "status": record.get("status"),
            "generation_status": record.get("generation_status"),
            "generated_cypher": record.get("generated_cypher"),
            "cga_elapsed_ms": record.get("cga_elapsed_ms"),
            "cga_diagnostic_status": (record.get("cga_diagnostic") or {}).get("status"),
            "cga_diagnostic_title": (record.get("cga_diagnostic") or {}).get("title"),
            "query_success": None if tugraph_response is None else not query_failed,
            "has_tugraph_response": tugraph_response is not None,
            "row_count": preview.get("row_count"),
            "truncated": preview.get("truncated", False),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        }

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _new_user_query_id() -> str:
    return f"uq-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _downloadable_tugraph_response(record: dict[str, Any]) -> dict[str, Any] | None:
    payload = record.get("tugraph_response")
    if payload is None:
        payload = record.get("tugraph_execution")
    if not isinstance(payload, dict) or not payload:
        return None
    return payload


def _cga_elapsed_ms(cga_generation: dict[str, Any] | None) -> int | None:
    if not isinstance(cga_generation, dict):
        return None
    trace = cga_generation.get("trace")
    if isinstance(trace, dict):
        elapsed_ms = _elapsed_between(trace.get("started_at"), trace.get("finished_at"))
        if elapsed_ms is not None:
            return elapsed_ms
        stage_total = _stage_duration_total_ms(trace.get("stages"))
        if stage_total is not None:
            return stage_total
    for key in ("cga_elapsed_ms", "generation_elapsed_ms"):
        value = cga_generation.get(key)
        if isinstance(value, (int, float)):
            return max(0, int(round(float(value))))
    return None


def _elapsed_between(started_at: Any, finished_at: Any) -> int | None:
    started = _parse_datetime(started_at)
    finished = _parse_datetime(finished_at)
    if started is None or finished is None:
        return None
    return max(0, int(round((finished - started).total_seconds() * 1000)))


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _stage_duration_total_ms(stages: Any) -> int | None:
    if not isinstance(stages, list) or not stages:
        return None
    total = 0
    has_duration = False
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        duration = stage.get("duration_ms")
        if isinstance(duration, (int, float)):
            total += max(0, int(round(float(duration))))
            has_duration = True
    return total if has_duration else None


def _normalize_tugraph_rows(tugraph_response: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(tugraph_response, dict):
        return []
    if isinstance(tugraph_response.get("result"), list):
        return _normalize_raw_result_rows(
            headers=tugraph_response.get("header"),
            rows=tugraph_response["result"],
        )
    if isinstance(tugraph_response.get("rows"), list):
        return _normalize_raw_result_rows(headers=None, rows=tugraph_response["rows"])
    return []


def _normalize_raw_result_rows(*, headers: Any, rows: list[Any]) -> list[dict[str, Any]]:
    column_names = _column_names(headers)
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append(row)
            continue
        if not isinstance(row, list):
            normalized.append({"value": row})
            continue
        item: dict[str, Any] = {}
        for index, value in enumerate(row):
            column_name = column_names[index] if index < len(column_names) else f"col_{index}"
            item[column_name] = value
        normalized.append(item)
    return normalized


def _column_names(headers: Any) -> list[str]:
    if not isinstance(headers, list):
        return []
    names: list[str] = []
    for index, header in enumerate(headers):
        column_name = f"col_{index}"
        if isinstance(header, dict) and header.get("name") is not None:
            column_name = str(header["name"])
        elif isinstance(header, str):
            column_name = header
        names.append(column_name)
    return names


def _raw_tugraph_failed(tugraph_response: dict[str, Any] | None) -> bool:
    if not isinstance(tugraph_response, dict):
        return False
    return any(tugraph_response.get(key) for key in ("error", "errors", "error_message"))
