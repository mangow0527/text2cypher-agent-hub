from __future__ import annotations

import time
from typing import Any, Dict, List

import httpx

from .models import ExecutionResult


class TuGraphClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        graph: str,
        mock_mode: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.graph = graph
        self.mock_mode = mock_mode

    async def execute(self, cypher: str) -> ExecutionResult:
        if self.mock_mode:
            return self._mock_execute(cypher)

        async with httpx.AsyncClient(timeout=30.0) as client:
            jwt = await self._login(client)
            started = time.perf_counter()
            response = await client.post(
                f"{self.base_url}/cypher",
                json={"graph": self.graph, "script": cypher},
                headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if response.status_code >= 400:
                return self._build_error_result(response, elapsed_ms)
            data = response.json()
            rows = self._normalize_rows(data)
            return ExecutionResult(
                success=True,
                rows=rows,
                row_count=len(rows),
                error_message=None,
                elapsed_ms=elapsed_ms,
            )

    async def _login(self, client: httpx.AsyncClient) -> str:
        response = await client.post(
            f"{self.base_url}/login",
            json={"user": self.username, "password": self.password},
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        jwt = payload.get("jwt")
        if not jwt:
            raise ValueError("TuGraph login succeeded but no JWT was returned.")
        return jwt

    def _normalize_rows(self, data: Dict[str, object]) -> List[Dict[str, object]]:
        headers = data.get("header") or []
        result_rows = data.get("result") or []
        normalized: List[Dict[str, object]] = []
        for row in result_rows:
            if not isinstance(row, list):
                normalized.append({"value": row})
                continue
            item: Dict[str, object] = {}
            for index, value in enumerate(row):
                column_name = f"col_{index}"
                if index < len(headers) and isinstance(headers[index], dict):
                    column_name = str(headers[index].get("name", column_name))
                item[column_name] = value
            normalized.append(item)
        return normalized

    def _build_error_result(self, response: httpx.Response, elapsed_ms: int) -> ExecutionResult:
        message = response.text
        try:
            message = response.json().get("error_message", message)
        except ValueError:
            pass
        return ExecutionResult(
            success=False,
            rows=None,
            row_count=None,
            error_message=str(message),
            elapsed_ms=elapsed_ms,
        )

    def _mock_execute(self, cypher: str) -> ExecutionResult:
        normalized = cypher.lower()
        if "matchh" in normalized or "syntax_error" in normalized:
            return ExecutionResult(
                success=False,
                rows=None,
                row_count=None,
                error_message="Cypher syntax error near RETURN.",
                elapsed_ms=8,
            )
        if ":film" in normalized or ":device" in normalized:
            return ExecutionResult(
                success=False,
                rows=None,
                row_count=None,
                error_message="Schema error: requested label does not exist in network_schema_v10.",
                elapsed_ms=12,
            )
        if "count(" in normalized:
            return ExecutionResult(success=True, rows=[{"count": 3}], row_count=1, error_message=None, elapsed_ms=9)
        return ExecutionResult(
            success=True,
            rows=[{"id": "ne-1", "name": "edge-router-1"}],
            row_count=1,
            error_message=None,
            elapsed_ms=15,
        )
