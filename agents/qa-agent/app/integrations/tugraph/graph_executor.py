from __future__ import annotations

import time
from typing import Dict, Tuple

from app.domain.models import ResultSignature, RuntimeMeta, TuGraphConfig
from tugraph_http_ops import TuGraphHttpOps


class GraphExecutor:
    def __init__(self) -> None:
        self._clients: Dict[Tuple[str, str, str, str], TuGraphHttpOps] = {}

    def execute(self, cypher: str, config: TuGraphConfig) -> tuple[RuntimeMeta, ResultSignature, bool]:
        start = time.perf_counter()
        if not config.base_url:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return (
                RuntimeMeta(latency_ms=latency_ms, warnings=["TuGraph not configured"], planner="mock"),
                ResultSignature(
                    columns=["result"],
                    column_types=["string"],
                    row_count=1,
                    result_preview=[{"result": "mock"}],
                    result_rows=[{"result": "mock"}],
                ),
                True,
            )

        try:
            ops = self._get_client(config)
            payload = ops.call_cypher(cypher)
            header = payload.get("header") or []
            columns = [item.get("name", f"col_{idx}") for idx, item in enumerate(header)]
            rows = self._normalize_rows(payload.get("result") or [], columns)
            column_types = [str(item.get("type", "unknown")) for item in header]
            latency_ms = int((time.perf_counter() - start) * 1000)
            return (
                RuntimeMeta(latency_ms=latency_ms, planner="tugraph-http-legacy"),
                ResultSignature(
                    columns=columns,
                    column_types=column_types,
                    row_count=len(rows),
                    result_preview=rows[:5],
                    result_rows=rows,
                ),
                True,
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - start) * 1000)
            return (
                RuntimeMeta(latency_ms=latency_ms, planner="tugraph-http", error=str(exc)),
                ResultSignature(),
                False,
            )

    def _normalize_rows(self, result: list, columns: list[str] | None = None) -> list[dict]:
        normalized: list[dict] = []
        for row in result:
            if isinstance(row, dict):
                normalized.append(row)
            elif isinstance(row, list):
                if columns:
                    normalized.append({columns[idx] if idx < len(columns) else f"col_{idx}": value for idx, value in enumerate(row)})
                else:
                    normalized.append({f"col_{idx}": value for idx, value in enumerate(row)})
            else:
                normalized.append({"value": row})
        return normalized

    def test_connection(self, config: TuGraphConfig) -> dict:
        runtime_meta, signature, ok = self.execute("MATCH (n) RETURN count(n) AS total", config)
        return {
            "ok": ok,
            "runtime_meta": runtime_meta.model_dump(),
            "result_signature": signature.model_dump(),
        }

    def fetch_labels(self, config: TuGraphConfig) -> dict:
        if not config.base_url:
            return {"vertex": [], "edge": [], "planner": "mock"}
        ops = self._get_client(config)
        labels = ops._list_labels()
        return {
            "vertex": list(labels.get("vertex") or []),
            "edge": list(labels.get("edge") or []),
            "planner": "tugraph-http-legacy",
        }

    def _get_client(self, config: TuGraphConfig) -> TuGraphHttpOps:
        key = (
            config.base_url or "",
            config.username or "",
            config.password or "",
            config.graph or "",
        )
        client = self._clients.get(key)
        if client is None:
            client = TuGraphHttpOps(
                base_url=config.base_url,
                user=config.username,
                password=config.password,
                graph=config.graph,
            )
            self._clients[key] = client
        return client
