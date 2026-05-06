from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


class QARedispatchGateway:
    def __init__(self, client: httpx.Client | None = None, module_logs=None) -> None:
        self.client = client or httpx.Client(timeout=10)
        self.module_logs = module_logs

    def get_detail(self, qa_id: str) -> dict[str, Any]:
        url = f"{settings.qa_agent_base_url.rstrip('/')}/qa/{qa_id}"
        if self.module_logs is not None:
            self.module_logs.append(
                module="qa_case",
                level="info",
                operation="qa_agent_detail_requested",
                trace_id=qa_id,
                status="started",
                request_body={"url": url, "qa_id": qa_id},
            )
        response = self.client.get(url)
        payload = response.json()
        if self.module_logs is not None:
            self.module_logs.append(
                module="qa_case",
                level="info" if response.is_success else "error",
                operation="qa_agent_detail_completed",
                trace_id=qa_id,
                status="success" if response.is_success else "error",
                request_body={"url": url, "qa_id": qa_id},
                response_body=payload,
                http_status=response.status_code,
            )
        response.raise_for_status()
        return payload
