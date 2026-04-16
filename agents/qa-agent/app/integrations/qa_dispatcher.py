from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.domain.models import QASample


class QADispatcher:
    def __init__(self, client: httpx.Client | None = None, log_root: Path | None = None) -> None:
        self._client = client or httpx.Client(timeout=10)
        self._log_root = log_root or (settings.artifacts_dir / "logs")
        self._log_root.mkdir(parents=True, exist_ok=True)
        self._log_path = self._log_root / "dispatch.log"

    def dispatch_samples(self, samples: list[QASample]) -> dict[str, Any]:
        host = settings.test_agent_host.strip()
        if not host:
            return {
                "enabled": False,
                "status": "skipped",
                "host": None,
                "total": len(samples),
                "success": 0,
                "failed": 0,
                "results": [],
                "message": "TEST_AGENT_HOST is not configured.",
            }

        question_base_url, golden_base_url = self._resolve_base_urls(host)
        results = [self._dispatch_sample(question_base_url, golden_base_url, sample) for sample in samples]
        return self._summarize_results(question_base_url, golden_base_url, len(samples), results)

    def dispatch_release_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        host = settings.test_agent_host.strip()
        if not host:
            return {
                "enabled": False,
                "status": "skipped",
                "host": None,
                "total": len(rows),
                "success": 0,
                "failed": 0,
                "results": [],
                "message": "TEST_AGENT_HOST is not configured.",
            }

        question_base_url, golden_base_url = self._resolve_base_urls(host)
        results = [self._dispatch_row(question_base_url, golden_base_url, row) for row in rows]
        return self._summarize_results(question_base_url, golden_base_url, len(rows), results)

    def _summarize_results(
        self,
        question_base_url: str,
        golden_base_url: str,
        total: int,
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        success = sum(1 for item in results if item["status"] == "success")
        partial = sum(1 for item in results if item["status"] == "partial")
        failed = sum(1 for item in results if item["status"] == "failed")
        overall = "success"
        if failed and success == 0 and partial == 0:
            overall = "failed"
        elif failed or partial:
            overall = "partial"
        return {
            "enabled": True,
            "status": overall,
            "host": question_base_url,
            "question_host": question_base_url,
            "golden_host": golden_base_url,
            "total": total,
            "success": success,
            "partial": partial,
            "failed": failed,
            "results": results,
        }

    def _dispatch_sample(self, question_base_url: str, golden_base_url: str, sample: QASample) -> dict[str, Any]:
        question_result = self._post_with_retry(
            f"{question_base_url}/api/v1/qa/questions",
            {
                "id": sample.id,
                "question": sample.question_canonical_zh,
            },
        )
        golden_result = self._post_with_retry(
            f"{golden_base_url}/api/v1/qa/goldens",
            {
                "id": sample.id,
                "cypher": sample.cypher,
                "answer": sample.answer,
                "difficulty": sample.difficulty,
            },
        )
        status = "success"
        if not question_result["ok"] and not golden_result["ok"]:
            status = "failed"
        elif not question_result["ok"] or not golden_result["ok"]:
            status = "partial"
        return {
            "id": sample.id,
            "status": status,
            "question": question_result,
            "golden": golden_result,
        }

    def _dispatch_row(self, question_base_url: str, golden_base_url: str, row: dict[str, Any]) -> dict[str, Any]:
        question_result = self._post_with_retry(
            f"{question_base_url}/api/v1/qa/questions",
            {
                "id": row["id"],
                "question": row["question"],
            },
        )
        golden_result = self._post_with_retry(
            f"{golden_base_url}/api/v1/qa/goldens",
            {
                "id": row["id"],
                "cypher": row["cypher"],
                "answer": row["answer"],
                "difficulty": row["difficulty"],
            },
        )
        status = "success"
        if not question_result["ok"] and not golden_result["ok"]:
            status = "failed"
        elif not question_result["ok"] or not golden_result["ok"]:
            status = "partial"
        return {
            "id": row["id"],
            "status": status,
            "question": question_result,
            "golden": golden_result,
        }

    def _post_with_retry(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        last_error = ""
        last_status_code: int | None = None
        last_response_body: str | None = None
        for attempt in range(1, 4):
            try:
                response = self._client.post(url, json=payload)
                response_body = self._truncate_text(response.text)
                last_status_code = response.status_code
                last_response_body = response_body
                if 200 <= response.status_code < 300:
                    result = {
                        "ok": True,
                        "attempts": attempt,
                        "status_code": response.status_code,
                        "error": None,
                        "response_body": response_body,
                    }
                    self._write_log(url, payload, result)
                    return result
                last_error = f"HTTP {response.status_code}"
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                last_status_code = None
                last_response_body = None
            if attempt < 3:
                time.sleep(1)
        result = {
            "ok": False,
            "attempts": 3,
            "status_code": last_status_code,
            "error": last_error or "Unknown dispatch error",
            "response_body": last_response_body,
        }
        self._write_log(url, payload, result)
        return result

    def _base_url(self, host: str) -> str:
        if host.startswith("http://") or host.startswith("https://"):
            return host.rstrip("/")
        return f"http://{host.rstrip('/')}"

    def _resolve_base_urls(self, host: str) -> tuple[str, str]:
        normalized_host = host.rstrip("/")
        if normalized_host.startswith("http://") or normalized_host.startswith("https://"):
            scheme, remainder = normalized_host.split("://", 1)
            host_only = remainder.split(":", 1)[0]
            return (
                f"{scheme}://{host_only}:{settings.test_agent_question_port}",
                f"{scheme}://{host_only}:{settings.test_agent_golden_port}",
            )
        return (
            f"http://{normalized_host}:{settings.test_agent_question_port}",
            f"http://{normalized_host}:{settings.test_agent_golden_port}",
        )

    def _write_log(self, url: str, payload: dict[str, Any], result: dict[str, Any]) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "url": url,
            "payload": payload,
            "result": result,
        }
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _truncate_text(self, text: str, limit: int = 4000) -> str | None:
        normalized = text.strip()
        if not normalized:
            return None
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[:limit]}..."
